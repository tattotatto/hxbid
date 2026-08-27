"""宏曦标书 - 自我评分引擎.

按评分指标对已生成内容判卷：每维度 AI 判卷（见 run_scoring，Task 6）+ 代码汇总
compute_report 合成 §4.2 评分报告。数据模型见
docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §4.2。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import logging
from datetime import datetime, timezone

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
        out_items.append({
            "id": str(g.get("id") or item.get("id") or ""),
            "dimension": str(item.get("dimension") or g.get("dimension") or ""),
            "name": name,
            "points_total": round(pts_total, 1),
            "points_obtained": round(obtained, 1),
            "status": status,
            "evidence": str(g.get("evidence") or ""),
            "gap": str(g.get("gap") or ""),
            "suggestion": str(g.get("suggestion") or ""),
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