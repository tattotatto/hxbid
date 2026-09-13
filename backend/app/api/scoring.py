"""宏曦标书 - 评分指标 API（评标办法驱动的目录补全 + 自我评分的数据源）.

数据模型见 docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §4。
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.project import BidProject
from app.models.user import User
from app.services.ai_adapter import ai_adapter
from app.services.score_engine import run_scoring
from app.services.scoring_rubric import extract_rubric, normalize_rubric, validate_rubric
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


class RubricParseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)


class RubricUpdateRequest(BaseModel):
    rubric: dict = Field(..., description="编辑/核对后的评分指标（§4.1 结构）")


async def _get_bid_project(project_id: str, db: AsyncSession) -> BidProject:
    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/scoring-rubric")
async def get_scoring_rubric(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取评分指标 + status（found/none/manual）."""
    project = await _get_bid_project(project_id, db)
    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        rubric = {}
    return {"rubric": rubric}


@router.post("/{project_id}/scoring-rubric/parse")
async def parse_scoring_rubric(
    project_id: str,
    data: RubricParseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动粘贴评标办法文本 → AI 提取指标 → 落库（status="found"，失败则 "none"）."""
    project = await _get_bid_project(project_id, db)
    rubric = await extract_rubric(data.text, ai_adapter)
    rubric = normalize_rubric(rubric)  # 最后一次归一，保证结构完整
    project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)
    await db.commit()
    return {"rubric": rubric, "problems": validate_rubric(rubric)}


@router.put("/{project_id}/scoring-rubric")
async def update_scoring_rubric(
    project_id: str,
    data: RubricUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存核对/编辑后的指标（status 置 "manual"）；items 变动时 applied 置 False，
    使目录补全进入可再次触发状态（幂等防重复补的撤销条件，见 spec §6.3）。"""
    project = await _get_bid_project(project_id, db)
    try:
        prev = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        prev = {}
    try:
        rubric = normalize_rubric(data.rubric, force_status="manual")
        if prev.get("items") != rubric.get("items"):
            rubric["applied"] = False
        problems = validate_rubric(rubric)
    except (ValueError, TypeError):
        # 用户手工编辑的数据未经 AI 归一，畸形分值（如 points="15 分"）会触发
        # normalize/validate 的 int() 转换异常 —— 一律按 400 业务错误返回，杜绝 500。
        raise HTTPException(status_code=400, detail="评分指标数据格式有误，请检查分值填写")
    project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)
    await db.commit()
    return {"rubric": rubric, "problems": problems}


@router.post("/{project_id}/score")
async def rescore_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动重评：读已落库内容（final_content||ai_generated_content + children 小节标题）
    重跑判卷，覆盖报告。与导出同一份数据（spec §7.2）。"""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        rubric = {}
    if rubric.get("status") not in ("found", "manual") or not rubric.get("items"):
        raise HTTPException(
            status_code=400,
            detail="该项目未检测到可用评分指标（状态 none），请先粘贴/编辑评分办法",
        )

    def _load_chapters() -> list[dict]:
        out = []
        for ch in sorted(project.chapters, key=lambda c: c.order_index):
            content = ch.final_content or ch.ai_generated_content or ""
            try:
                children = json.loads(ch.children_json or "[]")
            except json.JSONDecodeError:
                children = []
            titles: list[str] = []
            def _walk(nodes):
                for n in nodes or []:
                    titles.append(str(n.get("title") or ""))
                    _walk(n.get("children"))
            _walk(children)
            if titles:
                content = f"{content}\n\n小节：\n" + "\n".join(titles)
            out.append({"title": ch.title, "content": content})
        return out

    report = await run_scoring(rubric, _load_chapters(), ai_adapter)
    project.scoring_report_json = json.dumps(report, ensure_ascii=False)
    await db.commit()
    return report


class AutoFixResponse(BaseModel):
    success: bool = False
    chapter_id: str = ""
    chapter_title: str = ""
    section_path: list[str] = []
    section_title: str = ""
    diff_summary: str = ""
    modified_content: str = ""


def _find_chapter_for_dimension(chapters: list, dimension: str):
    """dimension ↔ 章节标题做双向包含匹配（与 score_engine._match_dimensions 同语义，首个命中）."""
    if not dimension:
        return None
    for ch in sorted(chapters, key=lambda c: c.order_index):
        title = str(ch.title or "")
        if title and (dimension in title or title in dimension):
            return ch
    return None


@router.post("/{project_id}/scoring-items/{item_id}/auto-fix", response_model=AutoFixResponse)
async def auto_fix_scoring_item(
    project_id: str,
    item_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按自我评分给出的失分点/改进建议，自动改写对应小节.

    只写 ``final_content``（与 TreeEditor 保存同一层），保留 ``ai_generated_content``
    作为 AI 原始基线。报价项（kind=price）、固定格式/表格章节、定位不到小节的项
    一律拒绝——这些情况改了是帮倒忙。
    """
    from app.services.scoring_autofix import AutoFixError, apply_auto_fix, is_auto_fixable

    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
        report = json.loads(project.scoring_report_json or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="评分数据损坏，请重新评分后再试")

    rubric_item = next(
        (it for it in (rubric.get("items") or []) if str(it.get("id")) == str(item_id)), None
    )
    report_item = next(
        (it for it in (report.get("items") or []) if str(it.get("id")) == str(item_id)), None
    )
    if not rubric_item or not report_item:
        raise HTTPException(status_code=404, detail="评分项不存在，请重新评分后再试")
    # 现算而不是读报告里存的 auto_fixable：报告是历史快照，判定规则一变
    # （比如后来排除了判卷失败的行）旧报告就会失真。
    if not is_auto_fixable(
        rubric_item.get("kind"), report_item.get("suggestion"), report_item.get("status")
    ):
        raise HTTPException(
            status_code=400,
            detail="该项需要你手工填写（如报价），或评卷尚未给出可用的改进建议，无法自动修改",
        )

    dimension = str(rubric_item.get("dimension") or report_item.get("dimension") or "")
    chapter = _find_chapter_for_dimension(project.chapters, dimension)
    if not chapter:
        raise HTTPException(
            status_code=400,
            detail=f"评分维度「{dimension}」在目录中没有对应章节，无法自动修改",
        )

    # 素材提示（真实资质/人员/业绩）——缺了 AI 容易编造，失败不阻断
    try:
        from app.api.chapters import _materials_guidance_for_section

        materials_guidance = await _materials_guidance_for_section(
            str(chapter.title or ""), project_id, db
        )
    except Exception as exc:
        logger.warning("自动修改取素材失败（%s）: %s", chapter.title, exc)
        materials_guidance = ""

    try:
        fixed = await apply_auto_fix(
            chapter_title=str(chapter.title or ""),
            chapter_type=str(chapter.chapter_type or ""),
            children_json=chapter.children_json or "[]",
            chapter_content=chapter.final_content or chapter.ai_generated_content or "",
            report_item=report_item,
            rubric_item=rubric_item,
            materials_guidance=materials_guidance,
            ai_adapter=ai_adapter,
        )
    except AutoFixError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("评分自动修改失败（chapter=%s）", chapter.title)
        raise HTTPException(status_code=500, detail=f"自动修改失败：{exc}")

    chapter.children_json = fixed["children_json"]
    chapter.final_content = fixed["final_content"]
    try:
        meta = json.loads(chapter.chapter_meta_json or "{}")
    except json.JSONDecodeError:
        meta = {}
    if isinstance(meta, dict):
        meta["auto_fix"] = {
            "item_id": str(item_id),
            "section_path": fixed["section_path"],
            "diff_summary": fixed["diff_summary"],
        }
        chapter.chapter_meta_json = json.dumps(meta, ensure_ascii=False)
    await db.commit()

    section_path = fixed["section_path"]
    return AutoFixResponse(
        success=True,
        chapter_id=str(chapter.id),
        chapter_title=str(chapter.title or ""),
        section_path=section_path,
        section_title=section_path[-1] if section_path else str(chapter.title or ""),
        diff_summary=fixed["diff_summary"],
        modified_content=fixed["modified_content"],
    )


@router.get("/{project_id}/scoring-report")
async def get_scoring_report(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取最新评分报告（重进项目展示）。"""
    project = await _get_bid_project(project_id, db)
    try:
        return json.loads(project.scoring_report_json or "{}")
    except json.JSONDecodeError:
        return {}
