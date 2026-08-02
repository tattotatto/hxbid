"""宏曦标书 - 单节编辑服务.

提供目录树节点的内容存取、AI 修改、重新生成功能。
以叶子节点为最小编辑单元，替代全章加载的旧模式。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

SECTION_MODIFY_SYSTEM_PROMPT = """你是投标文件编辑助手。用户正在逐节审阅标书，你需要根据用户的修改意见，
只修改当前这一节的内容。

约束：
1. 只修改用户指定的节，不要改动其他节的内容
2. 保持原文中公司信息、人员姓名、证书编号等真实数据不变
3. 保持与同级其他节的互补性，不重复它们的内容
4. 使用同样的标题层级和格式规范
5. 禁止使用"首先""其次""此外""总而言之"等模板化连接词
6. 每个段落至少包含1个具体事实（数字、日期、项目名、证书编号等）
7. 直接返回修改后的完整文本，不要加解释"""


# ---------------------------------------------------------------------------
# 树操作工具
# ---------------------------------------------------------------------------

def _find_node_by_path(tree: list, path: list[str]) -> dict | None:
    """在子标题树中按路径查找节点."""
    if not path:
        return None
    for node in tree:
        if node.get("title") == path[0]:
            if len(path) == 1:
                return node
            children = node.get("children", [])
            if children:
                return _find_node_by_path(children, path[1:])
    return None


def _update_node_by_path(tree: list, path: list[str], updates: dict) -> bool:
    """更新树中指定路径节点的字段."""
    node = _find_node_by_path(tree, path)
    if node:
        node.update(updates)
        return True
    return False


def get_section_content(children_json: str, section_path: list[str]) -> str:
    """从子标题树中读取指定节的内容.

    Args:
        children_json: 章节的 children_json 字符串
        section_path: 节路径，如 ["服务方案", "门卫值守", "岗位职责"]

    Returns:
        节内容字符串，不存在返回空字符串
    """
    try:
        tree = json.loads(children_json) if isinstance(children_json, str) else children_json
    except json.JSONDecodeError:
        return ""

    # 如果 children_json 存的是扁平任务列表
    if isinstance(tree, list) and tree and "path" in tree[0]:
        # 扁平任务列表格式：从 path 匹配
        for task in tree:
            task_path = task.get("path", [])
            if task_path == section_path:
                return task.get("content", "")
        return ""

    # 树形结构：递归查找
    node = _find_node_by_path(tree, section_path)
    return node.get("content", "") if node else ""


def save_section_content(
    children_json: str, section_path: list[str], content: str
) -> str:
    """将内容保存到子标题树中指定节的 content 字段.

    Returns:
        更新后的 children_json 字符串
    """
    try:
        tree = json.loads(children_json) if isinstance(children_json, str) else children_json
    except json.JSONDecodeError:
        return children_json

    # 扁平任务列表格式
    if isinstance(tree, list) and tree and "path" in tree[0]:
        for task in tree:
            if task.get("path") == section_path:
                task["content"] = content
                task["human_edited"] = True
                return json.dumps(tree, ensure_ascii=False)
        return json.dumps(tree, ensure_ascii=False)

    # 树形结构
    _update_node_by_path(tree, section_path, {"content": content, "human_edited": True})
    return json.dumps(tree, ensure_ascii=False)


def collect_sibling_summaries(children_json: str, section_path: list[str]) -> list[str]:
    """收集同级节点的摘要（用于 AI 修改时防重复）."""
    try:
        tree = json.loads(children_json) if isinstance(children_json, str) else children_json
    except json.JSONDecodeError:
        return []

    # 找到父节点
    if len(section_path) <= 1:
        return []

    parent_path = section_path[:-1]
    current_title = section_path[-1]

    if isinstance(tree, list) and tree and "path" in tree[0]:
        # 扁平任务列表
        siblings = []
        for task in tree:
            task_path = task.get("path", [])
            if len(task_path) == len(section_path) and task_path[:-1] == parent_path:
                if task_path[-1] != current_title:
                    content = task.get("content", "")
                    summary = content[:80] + "…" if len(content) > 80 else content
                    siblings.append(f"{task_path[-1]}：{summary}" if summary else task_path[-1])
        return siblings

    # 树形结构
    parent_node = _find_node_by_path(tree, parent_path)
    if not parent_node:
        return []

    siblings = []
    for child in parent_node.get("children", []):
        if child.get("title") != current_title:
            content = child.get("content", "")
            summary = content[:80] + "…" if len(content) > 80 else content
            siblings.append(f"{child['title']}：{summary}" if summary else child["title"])
    return siblings


# ---------------------------------------------------------------------------
# AI 修改
# ---------------------------------------------------------------------------

async def modify_section(
    chapter_title: str,
    section_path: list[str],
    current_content: str,
    instruction: str,
    children_json: str = "[]",
    ai_adapter=None,
) -> dict:
    """AI 针对性修改单个节的内容.

    Returns:
        {"modified_content": str, "diff_summary": str}
    """
    if not ai_adapter:
        return {"modified_content": current_content, "diff_summary": "AI 服务不可用"}

    section_title = section_path[-1] if section_path else chapter_title
    ancestry = " > ".join([chapter_title] + section_path[:-1]) if len(section_path) > 1 else chapter_title

    # 收集同级节摘要
    siblings = collect_sibling_summaries(children_json, section_path)
    sibling_text = "\n".join(f"  - {s}" for s in siblings) if siblings else "（无同级节）"

    user_prompt = f"""【文档位置】{ancestry}
【当前节标题】{section_title}

【同级其他节摘要（请避免内容重复）】
{sibling_text}

【当前内容】
{current_content if current_content else "（尚未生成）"}

【修改要求】
{instruction}

请返回修改后的完整内容。只返回内容文本，不要加任何解释、标题或标记。"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": SECTION_MODIFY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=4096,
        )

        # 简单 diff 摘要
        diff_summary = _generate_diff_summary(current_content, response)

        return {
            "modified_content": response,
            "diff_summary": diff_summary,
        }

    except Exception as exc:
        logger.error("Section modify failed: %s", exc)
        return {
            "modified_content": current_content,
            "diff_summary": f"修改失败：{exc}",
        }


def _generate_diff_summary(original: str, modified: str) -> str:
    """生成简单的修改摘要."""
    if not original:
        return f"生成了新内容（{len(modified)} 字）"
    if original == modified:
        return "内容无变化"
    delta = len(modified) - len(original)
    if delta > 100:
        return f"扩充了约 {delta} 字的内容"
    elif delta < -100:
        return f"精简了约 {abs(delta)} 字的内容"
    else:
        return f"修改了部分内容（{len(modified)} 字）"


# ---------------------------------------------------------------------------
# 单节重新生成
# ---------------------------------------------------------------------------

async def regenerate_section(
    chapter_title: str,
    section_path: list[str],
    token_budget_hint: str,
    requirements: dict,
    children_json: str = "[]",
    company_profile: dict | None = None,
    ai_adapter=None,
) -> AsyncIterator[str]:
    """重新生成单个节的内容，流式返回.

    复用 subsection_generator.generate_section()。
    """
    from app.services.subsection_generator import generate_section
    from app.services.ai_pipeline import _budget_hint_to_tokens

    section_title = section_path[-1] if section_path else chapter_title
    max_tokens = _budget_hint_to_tokens(token_budget_hint)

    siblings = collect_sibling_summaries(children_json, section_path)

    async for chunk in generate_section(
        section_title=section_title,
        section_path=[chapter_title] + section_path,
        depth=len(section_path),
        requirements=requirements,
        max_tokens=max_tokens,
        sibling_summaries=siblings[:8],
        reference_sections=[],
        company_profile=company_profile,
        extra_guidance="",
    ):
        yield chunk
