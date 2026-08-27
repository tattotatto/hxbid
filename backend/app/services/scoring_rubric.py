"""宏曦标书 - 评分指标（评标办法结构化）服务.

从招标文件「评标办法/评分标准」章节提取/校验/渲染结构化评分指标。
评分指标是「目录补全 + 自我评分」的唯一真相源，数据模型见
docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §4.1。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# 内容型 kind：需要在目录中有对应章节/小节，缺失则自动补
CONTENT_KINDS = {"content", "cert", "personnel", "performance"}
# 全部合法 kind
ALL_KINDS = CONTENT_KINDS | {"quality", "price"}

EXTRACT_RUBRIC_SYSTEM_PROMPT = (
    "你是招标文件评标办法解析专家。从用户给出的评标办法章节文本中"
    "抽取完整的评分指标表，严格输出 JSON，不要输出任何其他文字。"
)


def normalize_rubric(raw, *, force_status=None) -> dict:
    """把任意输入（AI 输出 / 手动编辑 / PUT 请求）归一化为 §4.1 评分指标结构.

    容错：非 dict 输入回退空结构；畸形 items 剔除；未知 kind 归一为 quality；
    字符串 key_terms（`;；、,，\n` 分隔）拆成词表，单字符剔除。
    """
    if not isinstance(raw, dict):
        raw = {}
    status = force_status or raw.get("status") or "none"
    items = raw.get("items")
    if not isinstance(items, list):
        items = []
    normalized_items = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        kind = it.get("kind", "")
        if kind not in ALL_KINDS:
            kind = "quality"
        key_terms = it.get("key_terms")
        if not isinstance(key_terms, list):
            key_terms = re.split(r"[;；、,，\n]", str(it.get("key_terms") or ""))
        normalized_items.append({
            "id": str(it.get("id") or f"item-{i:02d}"),
            "dimension": str(it.get("dimension") or "其他"),
            "name": str(it.get("name") or ""),
            "points": int(it.get("points") or 0),
            "kind": kind,
            "criteria": str(it.get("criteria") or ""),
            "key_terms": [
                t.strip() for t in key_terms
                if isinstance(t, str) and len(t.strip()) >= 2
            ],
        })
    return {
        "status": status,
        "method_name": str(raw.get("method_name") or ""),
        "max_total": int(raw.get("max_total") or 0),
        "applied": bool(raw.get("applied", False)),
        "raw_text": str(raw.get("raw_text") or ""),
        "items": normalized_items,
    }


def validate_rubric(rubric: dict) -> list[str]:
    """校验评分指标，返回问题列表（空 list = 通过）.

    - items 为空 → 提示（目录补全/自我评分无从驱动）
    - 单项缺 name/criteria → 提示
    - 分值加总与 max_total 偏差 > 5 → 提示需人工核对（非阻塞）
    """
    problems: list[str] = []
    items = rubric.get("items") or []
    if not items:
        problems.append("评分指标为空，无法驱动目录补全与自我评分")
    total_points = 0
    for it in items:
        if not it.get("name"):
            problems.append(f"指标 {it.get('id')} 缺少名称")
        if not it.get("criteria"):
            problems.append(f"指标 {it.get('id')}「{it.get('name')}」缺少给分标准")
        total_points += int(it.get("points") or 0)
    max_total = int(rubric.get("max_total") or 0)
    if items and max_total > 0 and abs(total_points - max_total) > 5:
        problems.append(
            f"各指标分值加总 {total_points} 与满分 {max_total} 偏差超过 5 分，请人工核对"
        )
    return problems


async def extract_rubric(text: str, ai_adapter) -> dict:
    """AI 从评标办法章节文本提取结构化评分指标（§4.1）.

    任何失败（JSON 畸形 / 空 content / API 错误 / 未检测到评分表）一律降级
    status="none" 并保留 raw_text，供前端手动粘贴/手填 —— 从不让提取失败阻塞生成。
    """
    user_prompt = f"""请从下面招标文件「评标办法」章节文本中提取完整的评分指标表。

严格按以下 JSON 输出（不要输出其他文字）：
{{
  "method_name": "综合评分法",
  "max_total": 100,
  "items": [
    {{
      "dimension": "技术部分",
      "name": "服务方案的完整性与针对性",
      "points": 15,
      "kind": "quality | content | cert | personnel | performance | price",
      "criteria": "方案完整、针对本项目…",
      "key_terms": ["服务方案", "针对性"]
    }}
  ]
}}

规则：
- kind 语义：content=方案/承诺/实施类内容，cert=资质证书，personnel=人员，performance=业绩，
  quality=完整性/针对性等纯评分维度，price=报价。不确定时用 quality。
- key_terms 给 2-6 个用于匹配标书目录标题的词（做包含匹配的关键词），每个至少 2 个字符。
- items 数量以原文实际评分项为准（通常 4-15 项）；丢项视为提取失败。
- points 必须是整数，加总应等于 max_total。
- 未检测到评分表（最低价法/无评分办法）时，items 返回空数组。
- method_name 填评标办法名称；没有则填空。

【评标办法章节文本】
{text[:30000]}
"""
    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": EXTRACT_RUBRIC_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=16384,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response)
    except Exception as exc:  # JSONDecodeError / RuntimeError(空 content) / API 错误
        logger.warning("评分指标提取失败，降级 none: %s", exc)
        return normalize_rubric({"raw_text": text[:20000]}, force_status="none")
    rubric = normalize_rubric(raw, force_status="found")
    rubric["raw_text"] = text[:20000]
    if not rubric["items"]:
        logger.info("评标办法文本中未检测到评分表（最低价法？），状态 none")
        rubric["status"] = "none"
    return rubric


def rubric_context_lines(rubric: dict, scoring_context: str) -> list[str]:
    """把结构化评分指标转成章节生成提示行.

    只渲染内容型 + 质量型（price 不进生成提示，quality 作为写作指导）。
    scoring_context 非空时按其过滤同维度指标。
    """
    items = rubric.get("items") or []
    if scoring_context:
        items = [it for it in items if it.get("dimension") == scoring_context]
    content = [it for it in items if it.get("kind") in CONTENT_KINDS]
    quality = [it for it in items if it.get("kind") == "quality"]
    lines: list[str] = []
    if content:
        lines.append("【评标要求·内容项需在正文中逐项覆盖】")
        lines.extend(f"- {it.get('name')}（{it.get('points')}分）：{it.get('criteria')}" for it in content)
    if quality:
        lines.append("【评分要点·质量项作为写作指导】")
        lines.extend(f"- {it.get('name')}（{it.get('points')}分）：{it.get('criteria')}" for it in quality)
    return lines