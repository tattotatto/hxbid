"""宏曦标书 - 历史标书配对学习分析服务.

流程:读取招标文件+标书 → 分析招标文件(需求+格式) → 对比标书产学习报告 →
存 lesson_json → 把「要求→应答」对齐对索引进向量库。
"""

import asyncio
import json
import logging

from app.database import async_session
from app.models.bid_lesson import BidLesson
from app.models.project import BidProject, ProjectChapter
from app.services.ai_adapter import ai_adapter
from app.services.ai_pipeline import parse_bid_requirements
from app.services.document_parser import parse_document
from app.services.format_extractor import extract_format_from_document
from app.services.vector_store import vector_store
from sqlalchemy import select

logger = logging.getLogger(__name__)

# 串行信号量:一次只分析一个配对,避免瞬时打爆 AI token 预算
_analysis_sem = asyncio.Semaphore(1)

# 对比输入上限:招标分析 + 标书正文都要截断
_COMPARE_INPUT_LIMIT = 15000
_BID_TEXT_LIMIT = 12000


def _truncate(text: str, limit: int = 12000) -> str:
    if text is None:
        return ""
    return text[:limit]


async def _analyze_tender(tender_text: str) -> dict:
    """分析招标文件:需求(复用 parse_bid_requirements)+ 格式(复用 extract_format_from_document)."""
    requirements = await parse_bid_requirements(tender_text)
    format_template = None
    try:
        format_template = await extract_format_from_document(tender_text, ai_adapter)
    except Exception as e:
        logger.warning("format extraction skipped: %s", e)
    return {"requirements": requirements, "format_template": format_template}


async def _compare_bid(tender_analysis: dict, bid_text: str) -> dict:
    """对比标书:招标要求逐条 → 标书应答 → 质量判定 + 写法要点."""
    requirements = tender_analysis.get("requirements", {})
    format_template = tender_analysis.get("format_template") or {}
    bid_text = _truncate(bid_text, _BID_TEXT_LIMIT)

    user_prompt = f"""你是资深投标专家。请对比「招标文件要求」与「已中标的标书文本」,
学习标书应该怎么写,并以JSON返回结构化结果。

招标文件要求(JSON):
{json.dumps(requirements, ensure_ascii=False)[:6000]}

招标文件格式模板(JSON,可为空):
{json.dumps(format_template, ensure_ascii=False)[:6000]}

标书正文:
{bid_text}

返回JSON结构(直接返回JSON对象,不要任何额外文字):
{{
  "tender_meta": {{"project_name": "项目名", "summary": "对招标文件要点的概括"}},
  "requirements_coverage": [
    {{
      "requirement": "招标文件的具体要求原文",
      "category": "qualification|technical|commercial|format",
      "bid_response": "标书中对应的应答内容(原文摘录,可空)",
      "how_addressed": "标书是怎么组织来应答这条的(如放在资格审查、用表格、用承诺等)",
      "quality": "good|partial|missing",
      "lesson": "这条要求应该怎么写/放哪,一句话写法要点"
    }}
  ],
  "structure_mapping": [
    {{"tender_section": "招标文件要求的章节", "bid_section": "标书对应章节", "format_ok": true, "notes": "说明"}}
  ],
  "writing_style": {{"overall": "总体行文风格", "patterns": ["惯用句式/结构"], "strengths": ["值得借鉴的优点"]}},
  "lessons": ["标书应该怎么写的关键要点(3-6条)"]
}}

要求:
- requirements_coverage 覆盖招标文件所有明确要求(资质/人员/业绩/技术/商务/格式)。
- bid_response 只摘录标书原文,缺失时为空字符串。
- quality=missing 时 lesson 简述为什么缺失、应怎么补。
- 所有字段必须存在,未提及用空字符串或空数组。"""

    messages = [
        {"role": "system", "content": "你是严谨的投标文件撰写专家。"},
        {"role": "user", "content": user_prompt},
    ]
    response = await ai_adapter.chat_completion(
        messages=messages,
        temperature=0.3,
        max_tokens=8192,
        response_format={"type": "json_object"},
    )
    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        return {
            "tender_meta": {},
            "requirements_coverage": [],
            "structure_mapping": [],
            "writing_style": {},
            "lessons": [],
        }
    for key in (
        "tender_meta", "requirements_coverage",
        "structure_mapping", "writing_style", "lessons",
    ):
        result.setdefault(key, {} if key in ("tender_meta", "writing_style") else [])
    return result


def _build_alignment_chunks(pair_id: str, name: str, lesson: dict) -> list[dict]:
    """把 requirements_coverage 转成 RAG 对齐片段.纯函数.

    每个 quality != missing 且有应答的条目 → 一个 chunk,
    metadata 打 source=lesson,便于生成时按相似要求召回。
    """
    chunks = []
    for item in lesson.get("requirements_coverage", []) or []:
        req = (item.get("requirement") or "").strip()
        resp = (item.get("bid_response") or "").strip()
        if not req or not resp or item.get("quality") == "missing":
            continue
        lesson_tip = (item.get("lesson") or "").strip()
        content = f"招标要求：{req}\n标书应答：{resp}"
        if lesson_tip:
            content += f"\n写法要点：{lesson_tip}"
        chunks.append({
            "title": f"招标要求：{req[:60]} → 标书应答",
            "content": content,
            "metadata": {
                "source": "lesson",
                "pair_id": pair_id,
                "pair_name": name,
                "category": item.get("category", ""),
            },
        })
    return chunks


def index_lesson_alignment(pair_id: str, name: str, lesson: dict) -> int:
    """把对齐片段索引进向量库,返回成功条数."""
    if not vector_store.is_available():
        return 0
    chunks = _build_alignment_chunks(pair_id, name, lesson)
    indexed = 0
    for i, chunk in enumerate(chunks):
        ok = vector_store.index_chapter(
            chapter_id=f"{pair_id}_lesson_{i}",
            project_id=pair_id,
            title=chunk["title"],
            content=chunk["content"],
            metadata=chunk["metadata"],
        )
        if ok:
            indexed += 1
    return indexed


async def _assemble_project_bid_text(db, project_id: str) -> str:
    """系统项目的标书文本:由章节最终内容组装."""
    rows = await db.execute(
        select(ProjectChapter)
        .where(ProjectChapter.project_id == project_id)
        .order_by(ProjectChapter.order_index)
    )
    parts = []
    for ch in rows.scalars().all():
        body = ch.final_content or ch.ai_generated_content or ""
        if body.strip():
            parts.append(f"{ch.title}\n{body}")
    return "\n\n".join(parts)


def _read_document_text(path: str) -> str:
    if not path:
        return ""
    from pathlib import Path
    p = Path(path)
    if not p.is_file():
        return ""
    return parse_document(str(p))


async def run_analysis(pair_id: str) -> None:
    """异步编排:分析招标→对比标书→存报告→索引进向量库.串行执行."""
    async with _analysis_sem:
        async with async_session() as db:
            pair = await db.get(BidLesson, pair_id)
            if not pair:
                return
            pair.status = "analyzing"
            pair.error = ""
            await db.commit()
            try:
                # 招标文件文本
                tender_text = _read_document_text(pair.tender_path)
                if not tender_text.strip():
                    raise ValueError("招标文件解析为空")
                # 标书文本:上传对用文件,系统项目用章节组装
                if pair.source_type == "project" and not pair.bid_path:
                    bid_text = await _assemble_project_bid_text(db, pair.project_id)
                else:
                    bid_text = _read_document_text(pair.bid_path)
                if not bid_text.strip():
                    raise ValueError("标书解析为空")

                tender_analysis = await _analyze_tender(_truncate(tender_text, _COMPARE_INPUT_LIMIT))
                lesson = await _compare_bid(tender_analysis, bid_text)
                pair.lesson_json = json.dumps(lesson, ensure_ascii=False)
                pair.status = "ready"
                await db.commit()

                indexed = index_lesson_alignment(pair.id, pair.name, lesson)
                logger.info("lesson %s ready, indexed %d chunks", pair.id, indexed)
            except Exception as exc:  # noqa: BLE001
                logger.exception("lesson analysis failed for %s", pair_id)
                pair.status = "failed"
                pair.error = str(exc)
                await db.commit()
