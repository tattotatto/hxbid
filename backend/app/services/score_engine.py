"""宏曦标书 - 自我评分引擎.

按评分指标对已生成内容判卷：每维度 AI 判卷（见 run_scoring，Task 6）+ 代码汇总
compute_report 合成 §4.2 评分报告。数据模型见
docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §4.2。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
from datetime import datetime, timezone

from app.services.scoring_autofix import is_auto_fixable

logger = logging.getLogger(__name__)

SCORE_STATUS_PASS = 0.90
SCORE_STATUS_PARTIAL = 0.60


def _match_dimensions(dims: dict[str, list[dict]], chapters: list[dict]) -> dict[str, str]:
    """dimension 名 ↔ 章节标题 匹配（精确/包含，首个命中）。无匹配维度不在结果中。

    Returns:
        {dimension: content}
    """
    out: dict[str, str] = {}
    by_title = {str(c.get("title") or ""): c for c in chapters}
    for dim in dims:
        hit_title = next(
            (t for t in by_title if dim and (dim in t or t in dim)),
            None,
        )
        if hit_title:
            out[dim] = str(by_title[hit_title].get("content") or "")
    return out


def _clamp_points(points_total: float, points_obtained) -> float:
    p = float(points_obtained)
    return min(max(p, 0.0), max(float(points_total), 0.0))


def compute_report(rubric: dict, graded: list[dict]) -> dict:
    """代码汇总：把逐项判卷结果合成为 §4.2 评分报告（纯函数，可测）.

    - 最终分数 = total / scored_total（unscored 项按其满分剔除折算）
    - points_obtained > points_total → 钳制 + 警告
    - 单项 status：≥90% pass；≥60% partial；否则 fail；unscored 保持
    """
    max_total = int(rubric.get("max_total") or 0)
    items = rubric.get("items") or []
    by_id = {str(it.get("id")): it for it in items}
    out_items: list[dict] = []
    warnings: list[str] = []
    total = 0.0
    scored_total = 0.0
    unscored_names: list[str] = []

    for g in graded:
        item = by_id.get(str(g.get("id"))) or {}
        pts_total = float(item.get("points") or g.get("points_total") or 0)
        raw = float(g.get("points_obtained") or 0)
        name = str(item.get("name") or g.get("name") or "")
        if g.get("status") == "unscored":
            status = "unscored"
            obtained = 0.0
            unscored_names.append(name or "未命名指标")
        else:
            obtained = _clamp_points(pts_total, raw)
            if raw > pts_total:
                warnings.append(f"{name} 得分超出满分，已钳制为 {obtained}")
            ratio = obtained / max(pts_total, 1e-9)
            if ratio >= SCORE_STATUS_PASS:
                status = "pass"
            elif ratio >= SCORE_STATUS_PARTIAL:
                status = "partial"
            else:
                status = "fail"
            scored_total += pts_total
            total += obtained
        suggestion = str(g.get("suggestion") or "")
        kind = str(item.get("kind") or "")
        out_items.append({
            "id": str(g.get("id") or item.get("id") or ""),
            "dimension": str(item.get("dimension") or g.get("dimension") or ""),
            "name": name,
            "points_total": round(pts_total, 1),
            "points_obtained": round(obtained, 1),
            "status": status,
            "evidence": str(g.get("evidence") or ""),
            "gap": str(g.get("gap") or ""),
            "suggestion": suggestion,
            # 前端据此决定显不显示「自动修改」：报价项必须用户自己填，判卷失败
            # 的行没有真实失分点，没有改进建议的项也无从改起。规则见 scoring_autofix。
            "kind": kind,
            "auto_fixable": is_auto_fixable(kind, suggestion, status),
        })

    note = ""
    if unscored_names:
        note = f"未参与计分：{'、'.join(dict.fromkeys(unscored_names))}"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method_name": str(rubric.get("method_name") or ""),
        "max_total": max_total,
        "total": round(total, 1),
        "scored_total": round(scored_total, 1),
        "unscored_note": note,
        "warnings": warnings,
        "items": out_items,
    }


JUDGE_SYSTEM_PROMPT = (
    "你是资深评标专家。根据招标文件评分办法中的评分项，对已生成的标书内容逐项判分。"
    "严格输出 JSON，不要输出任何其他文字。"
)


async def run_scoring(rubric: dict, chapters: list[dict], ai) -> dict:
    """对已生成内容按评分指标判卷，产出 §4.2 报告.

    - 每 dimension 一次 AI 调用，串行（medium token 预算）
    - dimension↔章节映射：_match_dimensions（精确/包含，首个命中）
    - 无匹配维度 → 用全部已组装内容尽力评分（单次调用）
    - 单项/单维度判卷失败 → 该项 unscored + 原因，其余照常，绝不整体失败

    Args:
        rubric: §4.1 评分指标
        chapters: [{title, content}] 已生成章节（与导出同一份数据）
        ai: ai_adapter（chat_completion）
    """
    items = rubric.get("items") or []
    dims: dict[str, list[dict]] = {}
    for it in items:
        dims.setdefault(str(it.get("dimension") or "其他"), []).append(it)

    matched = _match_dimensions(dims, chapters)
    all_content = "\n\n".join(c.get("content", "") for c in chapters)[:30000]
    graded: list[dict] = []
    for dim, dim_items in dims.items():
        content = matched.get(dim) or all_content
        try:
            graded.extend(await _grade_dimension(dim, dim_items, content, ai))
        except Exception as exc:
            logger.warning("评分维度 %s 判卷失败: %s", dim, exc)
            for it in dim_items:
                graded.append({
                    "id": it.get("id"),
                    "dimension": dim,
                    "name": it.get("name", ""),
                    "points_obtained": 0,
                    "status": "unscored",
                    "evidence": "",
                    "gap": "",
                    "suggestion": f"判卷失败：{exc}",
                })
    # 判卷输出可能遗漏指标项（AI 畸形响应）——回填 unscored 行，保证报告逐项完整（§4.2）
    graded_ids = {g.get("id") for g in graded}
    for it in items:
        if it.get("id") not in graded_ids:
            graded.append({
                "id": it.get("id"),
                "dimension": it.get("dimension", ""),
                "name": it.get("name", ""),
                "points_obtained": 0,
                "status": "unscored",
                "evidence": "",
                "gap": "",
                "suggestion": "判卷未覆盖",
            })
    return compute_report(rubric, graded)


async def _grade_dimension(dimension: str, items: list[dict], content: str, ai) -> list[dict]:
    """单维度一次判卷调用，返回逐项判卷结果（[{"id", "points_obtained", "status", "evidence", "gap", "suggestion"}]）。

    status：evidence 为空或「未含报价」→ "unscored"；否则 "graded"（汇总层按分值定 pass/partial/fail）。
    """
    rows = "\n".join(
        f"- id={it.get('id')} name={it.get('name')} 满分={it.get('points')} "
        f"给分标准={it.get('criteria')} kind={it.get('kind')}"
        for it in items
    )
    user_prompt = f"""【评分维度】{dimension}

【评分项】
{rows}

【该维度已生成内容】
{content[:30000]}

请逐项输出：
{{
  "items": [
    {{
      "id": "...",
      "points_obtained": 12,
      "status_source": "内容实际出现的章节号/标题，作为给分依据",
      "gap": "失分点说明（无则空字符串）",
      "suggestion": "改进建议（无则空字符串）"
    }}
  ]
}}

要求：
- points_obtained 不得超过该项满分；实在无法判断给 0。
- 报价项（kind=price）若内容中无报价信息：points_obtained=0、status_source="未含报价"。
- status_source 必须具体到章节号/标题，不能写「内容中」「未知」。
"""
    response = await ai.chat_completion(
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=8192,
        response_format={"type": "json_object"},
    )
    raw = json.loads(response)
    result: list[dict] = []
    for g in raw.get("items") or []:
        evidence = str(g.get("status_source") or "")
        status = "graded" if evidence and evidence != "未含报价" else "unscored"
        result.append({
            "id": str(g.get("id") or ""),
            "points_obtained": float(g.get("points_obtained") or 0),
            "status": status,
            "evidence": evidence,
            "gap": str(g.get("gap") or ""),
            "suggestion": str(g.get("suggestion") or ""),
        })
    return result
