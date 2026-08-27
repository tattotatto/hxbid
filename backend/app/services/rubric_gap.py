"""宏曦标书 - 评分指标驱动目录补全（Feature A 纯函数）.

把评分指标中的内容型缺失项转成章节补入节点。数据模型见
docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §6。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.scoring_rubric import CONTENT_KINDS


def gap_detect(rubric: dict, chapter_titles: list[str]) -> list[dict]:
    """对每个内容型指标（kind ∈ CONTENT_KINDS），用其 key_terms（≥2 字符）
    对全部章节/小节标题做包含匹配；任一词未命中任一标题 → 该指标进 missing_items。

    kind ∈ {quality, price} 不参与（quality 仅生成指导，price 仅评分）。

    Returns:
        [{"dimension": str, "item": {...}}]
    """
    titles = [t for t in chapter_titles if t]
    missing: list[dict] = []
    for it in rubric.get("items") or []:
        if it.get("kind") not in CONTENT_KINDS:
            continue
        key_terms = [t for t in it.get("key_terms") or [] if len(t.strip()) >= 2]
        if not key_terms:
            continue
        if not any(any(term in title for term in key_terms) for title in titles):
            missing.append({
                "dimension": str(it.get("dimension") or "其他"),
                "item": it,
            })
    return missing


def _dimension_parent_exists(titles: list[str], dimension: str) -> bool:
    return bool(dimension) and any(dimension in t or t in dimension for t in titles)


def attach_key_for(title: str, attach: dict) -> str | None:
    """返回该章节应接收的补入节点维度 key（与 _dimension_parent_exists 同语义：双向包含，首个命中）."""
    for k in attach:
        if k and (k in title or title in k):
            return k
    return None


def build_rubric_nodes(missing: list[dict], titles: list[str]):
    """为缺失指标构建补入节点。

    Returns:
        (new_top_nodes, attach_map, added_titles)
        - new_top_nodes: 无同名维度章节时新建的顶层节点列表（含 children）
        - attach_map: {dimension_title: [子节点]}，挂到已有 dimension 章节下
        - added_titles: 本次新增的标题列表（供前端提示与 confirm 响应）
    """
    new_top: list[dict] = []
    attach: dict[str, list[dict]] = {}
    added_titles: list[str] = []
    by_dim: dict[str, list[dict]] = {}
    for m in missing:
        by_dim.setdefault(str(m["dimension"] or "其他"), []).append(m["item"])

    for dimension, items in by_dim.items():
        def _leaf(it):
            return {
                "title": str(it.get("name") or ""),
                "type": "ai_generated",
                "source": "scoring_rubric",
                "rubric_item_id": str(it.get("id") or ""),
            }

        if _dimension_parent_exists(titles, dimension):
            attach[dimension] = [_leaf(it) for it in items]
        else:
            new_top.append({
                "title": dimension,
                "type": "ai_generated",
                "source": "scoring_rubric",
                "children": [_leaf(it) for it in items],
            })
        added_titles.extend(str(it.get("name") or "") for it in items)
    return new_top, attach, added_titles