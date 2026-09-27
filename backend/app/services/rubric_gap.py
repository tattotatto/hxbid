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


def dimension_keywords(dimension: str) -> list[str]:
    """维度名 → 从具体到宽松的候选关键词（首个命中即用）.

    评分维度是「技术标」「商务标」这类**分类标签**，而招标自己的章节名是
    「十八、技术文件其他材料」「十一、商务文件其他材料」—— 只做字面双向包含
    永远匹配不上。剥掉「标 / 部分 / 文件 / 内容」这类后缀取核心词再匹配。
    """
    d = (dimension or "").strip()
    if not d:
        return []
    out = [d]
    core = d
    for suffix in ("标", "部分", "文件", "内容"):
        if core.endswith(suffix) and len(core) > len(suffix) + 1:
            core = core[: -len(suffix)]
            out.append(core)
            break
    return out


def _title_matches(title: str, keywords: list[str]) -> bool:
    return any(k and (k in title or title in k) for k in keywords)


def _dimension_parent_exists(titles: list[str], dimension: str) -> bool:
    keywords = dimension_keywords(dimension)
    return bool(keywords) and any(_title_matches(t, keywords) for t in titles)


def attach_key_for(title: str, attach: dict) -> str | None:
    """返回该章节应接收的补入节点维度 key（按核心关键词匹配，首个命中）."""
    for k in attach:
        if _title_matches(title, dimension_keywords(k)):
            return k
    return None


def build_rubric_nodes(missing: list[dict], titles: list[str]):
    """为缺失指标构建补入节点。

    **只挂不建**（用户 2026-09-27 裁定）：维度匹配不到已有章节时**不新建顶层
    章节** —— 用户确认过的目录必须被严格遵守。匹配不上的条目走 ``unplaced``
    报出来，由调用方提示用户手动处理，绝不静默丢弃、也绝不擅自改结构。

    Returns:
        (attach_map, added_titles, unplaced_titles)
        - attach_map: {dimension: [子节点]}，由调用方按 ``attach_key_for``
          挂到已有章节下
        - added_titles: 本次将新增的标题列表
        - unplaced_titles: 找不到归属、**未加进目录**的条目标题（必须告知用户）
    """
    attach: dict[str, list[dict]] = {}
    added_titles: list[str] = []
    unplaced_titles: list[str] = []
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

        names = [str(it.get("name") or "") for it in items]
        if _dimension_parent_exists(titles, dimension):
            attach[dimension] = [_leaf(it) for it in items]
            added_titles.extend(names)
        else:
            unplaced_titles.extend(names)
    return attach, added_titles, unplaced_titles