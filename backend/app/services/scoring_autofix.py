"""宏曦标书 - 评分驱动的自动修改.

把自我评分报告里的失分点 / 改进建议，转成对目标小节的 AI 改写，并给出更新后的
小节树（children_json）与整章正文（final_content）。

两条设计约束（都有实测依据）：

1. **只改小节，不整章重建。** 章节级 lead_in 不存在 children_json 里，从树重建
   整章正文会把它丢掉。所以改写结果是在现有正文里**替换目标小节那一段**，其余
   字节原样保留；正文与树对不上时宁可拒绝，也不猜。
2. **固定格式 / 表格章节拒绝改写。** 那类章节的内容是招标文件原文，改它等于篡改
   招标要求。

定位逻辑为纯函数，便于单测。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging

from app.services.section_editor import get_section_content, save_section_content

logger = logging.getLogger(__name__)

# 报价项在整套评分逻辑里只评分、不进生成（见 scoring_rubric.ALL_KINDS 语义），
# 没有可改的正文——用户必须自己填报价。
PRICE_KIND = "price"

# fixed_form 是招标原文填空，table 是招标表格，内容都不是 AI 生成的
NON_FIXABLE_CHAPTER_TYPES = ("fixed_form", "table")

NON_FIXABLE_MESSAGE = "该章节是固定格式/表格章节，内容是招标文件原文，不能自动改写"

# 改写是「补强」不是「精简」：原文本就完整，换成明显更短的版本一定是净损失。
# 两种成因都在真实模型上实测见过——模型自行压缩（2969 字 → 805 字），输出被
# max_tokens 截断（表格中途断掉，后两节整段消失，2972 → 2361）。
# ai_adapter 对「content 非空但 finish_reason=length」有意放行（生成路径上半篇
# 好过没有），所以这道防线只能设在改写这一层。
# 小节的字符数太少时比例是噪声（9 字的节少写 4 个字≠丢内容），设一个长度下限。
REWRITE_MIN_KEEP_RATIO = 0.9
REWRITE_RATIO_MIN_CHARS = 200


class AutoFixError(Exception):
    """无法自动修改：定位不到目标、章节类型不允许、正文与树不一致、AI 失败等。

    消息直接透给前端展示，必须是人话。
    """


# ---------------------------------------------------------------------------
# 是否提供「自动修改」
# ---------------------------------------------------------------------------

def is_auto_fixable(kind: str, suggestion: str, status: str = "") -> bool:
    """该项是否值得显示「自动修改」按钮.

    三条判据，规则留在后端一处，前端只读 ``auto_fixable`` 字段：

    1. 不是报价项 —— 报价只评分、不进生成，必须用户自己填；
    2. 不是 unscored —— 判卷失败/未计分时，``suggestion`` 里装的是
       "判卷失败：Expecting ',' delimiter" 这类错误文案（见 run_scoring 的回填
       分支），拿它当改写指令只会让 AI 去修一个并不存在的失分点；
    3. 确实有改进建议 —— 没有意见就没有改写依据。
    """
    if str(kind or "") == PRICE_KIND:
        return False
    if str(status or "") == "unscored":
        return False
    return bool(str(suggestion or "").strip())


def build_instruction(
    name: str,
    gap: str,
    suggestion: str,
    criteria: str = "",
) -> str:
    """把评分意见转成给 AI 的改写要求.

    三者全空时抛 AutoFixError —— 没有意见就没有改写依据。
    """
    gap, suggestion, criteria = gap.strip(), suggestion.strip(), criteria.strip()
    if not (gap or suggestion or criteria):
        raise AutoFixError("该评分项没有失分点或改进建议，无法自动修改")

    parts = [f"【评分项】{name}"]
    if gap:
        parts.append(f"【当前失分点】{gap}")
    if suggestion:
        parts.append(f"【评标改进建议】{suggestion}")
    if criteria:
        parts.append(f"【该项给分标准】{criteria}")

    return (
        "\n".join(parts)
        + "\n\n请针对上述失分点和改进建议补强本节内容。要求：\n"
        "1. 只补强，不要删改与本意见无关的原有内容\n"
        "2. 保留原文中的公司名称、人员姓名、证书编号、金额等真实数据\n"
        "3. 不要编造不存在的资质、业绩或数字\n"
        "4. 直接返回修改后的完整本节内容，不要加任何解释、标题或标记"
    )


# ---------------------------------------------------------------------------
# 目标定位（纯函数）
# ---------------------------------------------------------------------------

def _is_flat_task_list(tree: list) -> bool:
    """旧项目的扁平任务列表：节点直接带 path。"""
    return bool(tree) and isinstance(tree[0], dict) and "path" in tree[0]


def _iter_leaves(nodes: list, prefix: list[str]):
    """深度优先产出 (路径, 标题)。容器节点本身不是改写目标。"""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        title = str(node.get("title") or "")
        path = prefix + [title]
        children = node.get("children") or []
        if children:
            yield from _iter_leaves(children, path)
        else:
            yield path, title


def _collect_leaves(tree: list) -> list[tuple[list[str], str]]:
    if _is_flat_task_list(tree):
        leaves = []
        for task in tree:
            if not isinstance(task, dict):
                continue
            path = list(task.get("path") or [])
            if path:
                leaves.append((path, str(path[-1])))
        return leaves
    return list(_iter_leaves(tree, []))


def _score_leaf(title: str, key_terms: list[str], name: str) -> int:
    """小节标题与评分项的匹配度：命中关键词个数；都没命中再看评分项名。"""
    score = sum(1 for term in key_terms if term and term in title)
    if score == 0 and name and title and (name in title or title in name):
        score = 1
    return score


def locate_section(
    item: dict,
    chapter_type: str,
    children_json: str,
) -> tuple[list[str] | None, str]:
    """定位该评分项应当改写的小节.

    Returns:
        (section_path, error)
        - ``[]``   → 无子节，整个章节是改写单元
        - ``[...]`` → 命中的叶子小节路径
        - ``None`` → 不能自动修改，error 说明原因
    """
    if str(chapter_type or "") in NON_FIXABLE_CHAPTER_TYPES:
        return None, NON_FIXABLE_MESSAGE

    try:
        tree = json.loads(children_json) if isinstance(children_json, str) else children_json
    except (json.JSONDecodeError, TypeError):
        tree = []
    if not isinstance(tree, list):
        tree = []

    leaves = _collect_leaves(tree)
    if not leaves:
        return [], ""

    key_terms = [str(t).strip() for t in (item.get("key_terms") or []) if str(t).strip()]
    name = str(item.get("name") or "").strip()

    best_path: list[str] | None = None
    best_score = 0
    for path, title in leaves:
        score = _score_leaf(title, key_terms, name)
        if score > best_score:
            best_path, best_score = path, score

    if best_path is None:
        return None, "未找到与该评分项匹配的小节，请在目录中手动指定要修改的小节"
    return best_path, ""


# ---------------------------------------------------------------------------
# 执行改写
# ---------------------------------------------------------------------------

async def apply_auto_fix(
    *,
    chapter_title: str,
    chapter_type: str,
    children_json: str,
    chapter_content: str,
    report_item: dict,
    rubric_item: dict,
    materials_guidance: str = "",
    ai_adapter=None,
) -> dict:
    """按评分意见改写目标小节.

    Args:
        chapter_content: 章节现有正文（调用方传 ``final_content or ai_generated_content``）
        report_item: 评分报告项（含 gap / suggestion）
        rubric_item: 评分指标项（含 name / key_terms / criteria）

    Returns:
        {"section_path", "modified_content", "children_json", "final_content", "diff_summary"}

    Raises:
        AutoFixError: 定位不到、类型不允许、正文与树不一致、AI 失败等
    """
    section_path, err = locate_section(rubric_item, chapter_type, children_json)
    if section_path is None:
        raise AutoFixError(err)

    instruction = build_instruction(
        name=str(rubric_item.get("name") or report_item.get("name") or ""),
        gap=str(report_item.get("gap") or ""),
        suggestion=str(report_item.get("suggestion") or ""),
        criteria=str(rubric_item.get("criteria") or ""),
    )

    base = chapter_content or ""
    if section_path:
        old_content = get_section_content(children_json, section_path)
        if not old_content.strip():
            raise AutoFixError("目标小节尚无内容，无法定位改写位置，请手动编辑")
        if old_content not in base:
            raise AutoFixError("章节正文与目录树不一致，无法安全定位该小节，请手动编辑")
    else:
        old_content = base

    if not ai_adapter:
        raise AutoFixError("AI 服务不可用")

    from app.services.section_editor import modify_section

    try:
        result = await modify_section(
            chapter_title=chapter_title,
            section_path=section_path or [chapter_title],
            current_content=old_content,
            instruction=instruction,
            children_json=children_json,
            materials_guidance=materials_guidance,
            ai_adapter=ai_adapter,
        )
    except Exception as exc:
        logger.warning("评分自动修改失败（%s）: %s", chapter_title, exc)
        raise AutoFixError(f"AI 改写失败：{exc}") from exc

    summary = str(result.get("diff_summary") or "")
    modified = str(result.get("modified_content") or "")
    # modify_section 内部失败时不抛异常，而是原样返回旧内容 + 失败摘要
    if summary.startswith("修改失败") or summary == "AI 服务不可用":
        raise AutoFixError(summary)
    if not modified.strip() or modified == old_content:
        raise AutoFixError("AI 未产生实质修改，原内容保持不变")
    if (
        len(old_content) >= REWRITE_RATIO_MIN_CHARS
        and len(modified) < len(old_content) * REWRITE_MIN_KEEP_RATIO
    ):
        raise AutoFixError(
            f"AI 改写结果比原文少了 {len(old_content) - len(modified)} 字，疑似被截断，"
            "已放弃本次修改（小节保持原样），请重试或手动编辑"
        )

    if section_path:
        new_children, matched = save_section_content(children_json, section_path, modified)
        if not matched:
            raise AutoFixError("小节树定位失败，内容未保存")
        new_final = base.replace(old_content, modified, 1)
    else:
        # 整章：没有子节可定位，改写结果直接作为章节正文
        new_children = children_json
        new_final = modified

    return {
        "section_path": section_path,
        "modified_content": modified,
        "children_json": new_children,
        "final_content": new_final,
        "diff_summary": summary,
    }
