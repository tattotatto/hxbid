"""宏曦标书 - 标题细化服务.

对 AI 撰写类型的章节，根据招标文件评分标准和服务需求，
将章节标题分解为 3-4 级子标题树。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

TITLE_REFINE_SYSTEM_PROMPT = """你是投标文件大纲设计专家。你的任务是将一个标书章节标题展开为 3-4 级的详细子标题树。

展开原则：
1. 每个二级标题对应一个独立的服务方向或管理模块
2. 三级标题是二级标题的具体实施方案
3. 四级标题（如有）是实施方案的具体操作步骤
4. 展开深度取决于评分标准——评分权重高的方向展开更深
5. 参考招标文件的服务需求和评分标准，确保覆盖所有评分项
6. 同级标题之间内容互补不重叠，形成完整体系

标题命名规范：
- 使用动宾结构或名词短语（如"门卫值守管理方案""人员出入管理流程"）
- 避免使用空泛标题（如"概述""其他"）
- 每个标题应该是具体的、可独立撰写的内容单元

叶子节点是最终的生成任务，每个叶子节点应该是一个可以用 800-2000 字写清楚的具体主题。"""

REFINE_CHAT_SYSTEM_PROMPT = """你是投标文件大纲编辑助手。用户正在审阅标题细化结果，
你可以帮助用户修改子标题树。

你的能力：
1. 新增子标题 — 在指定位置添加新的子标题
2. 删除子标题 — 移除不需要的子标题
3. 修改标题 — 改标题文案
4. 调整层级 — 提升或降低标题层级
5. 拆分/合并 — 将一个标题拆成多个，或合并多个为一个

回复格式：
{{
  "reply": "已按照您的要求...",
  "children": [...修改后的完整子标题树...]
}}

子标题树格式：
[
  {{"title": "门卫值守管理方案", "children": [
    {{"title": "门卫岗位职责与操作规范", "children": []}},
    {{"title": "人员出入管理流程", "children": []}}
  ]}}
]

直接返回JSON，不要包含其他文字。"""


async def refine_chapter_titles(
    chapter_title: str,
    chapter_meta: dict,
    requirements: dict,
    ai_adapter,
) -> list[dict]:
    """将 AI 撰写章节的标题细化为子标题树.

    Args:
        chapter_title: 章节标题（如"服务方案"）
        chapter_meta: 章节元数据（scoring_context, format_notes 等）
        requirements: 解析后的招标要求
        ai_adapter: AI 适配器

    Returns:
        子标题树列表，每个节点:
        {"title": str, "children": [...], "token_budget_hint": str}
    """
    if not ai_adapter:
        return []

    # Build context
    scoring_context = chapter_meta.get("scoring_context", "")
    format_notes = chapter_meta.get("format_notes", "")

    req_lines = []
    if requirements.get("project_name"):
        req_lines.append(f"项目名称：{requirements['project_name']}")
    if requirements.get("service_requirements"):
        req_lines.append(f"服务内容：{'；'.join(requirements['service_requirements'])}")
    if requirements.get("evaluation_criteria"):
        req_lines.append(f"评标标准：{requirements['evaluation_criteria']}")
    if requirements.get("personnel_requirements"):
        req_lines.append(f"人员要求：{requirements['personnel_requirements']}")
    if requirements.get("special_requirements"):
        req_lines.append(f"特殊要求：{'；'.join(requirements['special_requirements'])}")

    user_prompt = f"""请将以下章节展开为 3-4 级子标题树。

【章节标题】{chapter_title}
【评分上下文】{scoring_context or "无特殊评分要求"}
【格式说明】{format_notes or "无特殊格式要求"}

【招标项目信息】
{chr(10).join(req_lines) if req_lines else "无额外信息"}

要求：
- 二级标题 6-12 个
- 总叶子节点数 15-40 个
- 评分权重高的方向展开更深
- 每个叶子节点是一个具体的、可独立撰写的主题

返回 JSON 对象：
{{
  "children": [
    {{
      "title": "二级标题",
      "token_budget_hint": "large",
      "children": [
        {{"title": "三级标题", "token_budget_hint": "medium", "children": []}}
      ]
    }}
  ]
}}

token_budget_hint: tiny|small|medium|large|xlarge
叶子节点（children 为空的节点）是最终需要 AI 撰写的内容。

直接返回JSON对象，不要包含其他文字。"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": TITLE_REFINE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)
        children = result.get("children", [])

        # Count leaf nodes
        def count_leaves(nodes):
            count = 0
            for n in nodes:
                kids = n.get("children", [])
                if kids:
                    count += count_leaves(kids)
                else:
                    count += 1
            return count

        leaf_count = count_leaves(children)
        logger.info(
            "Refined '%s': %d top-level children, %d leaf nodes",
            chapter_title, len(children), leaf_count,
        )

        return children

    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Title refinement failed for '%s': %s", chapter_title, exc)
        return []


async def chat_refine_titles(
    children_json: str,
    chapter_title: str,
    user_message: str,
    ai_adapter,
) -> dict:
    """对话式修改子标题树.

    Args:
        children_json: 当前的子标题树 JSON
        chapter_title: 章节标题
        user_message: 用户修改建议
        ai_adapter: AI 适配器

    Returns:
        {"reply": str, "children": [...]}
    """
    if not ai_adapter:
        return {"reply": "AI 服务不可用", "children": json.loads(children_json)}

    try:
        current = json.loads(children_json) if isinstance(children_json, str) else children_json
    except json.JSONDecodeError:
        current = []

    current_str = json.dumps(current, ensure_ascii=False, indent=2)

    user_prompt = f"""当前"{chapter_title}"的子标题树：
```json
{current_str}
```

用户修改建议：
{user_message}

请根据建议修改子标题树，返回修改后的完整树。"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": REFINE_CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)
        return {
            "reply": result.get("reply", "已更新子标题树。"),
            "children": result.get("children", current),
        }
    except Exception as exc:
        logger.error("Title refinement chat failed: %s", exc)
        return {
            "reply": f"修改失败：{exc}",
            "children": current,
        }


def flatten_children_to_tasks(children: list, parent_path: list | None = None) -> list[dict]:
    """将子标题树扁平化为生成任务列表.

    每个叶子节点 = 一个 AI 生成任务。

    Returns:
        任务列表，每个任务: {
            "path": ["服务方案", "门卫值守", "岗位职责"],
            "title": "门卫岗位职责与操作规范",
            "depth": 2,
            "token_budget_hint": "medium",
        }
    """
    if parent_path is None:
        parent_path = []

    tasks = []
    for node in children:
        title = node.get("title", "")
        node_path = parent_path + [title]
        kids = node.get("children", [])

        if kids:
            tasks.extend(flatten_children_to_tasks(kids, node_path))
        else:
            # Leaf node → generation task
            tasks.append({
                "path": node_path,
                "title": title,
                "depth": len(node_path) - 1,
                "token_budget_hint": node.get("token_budget_hint", "medium"),
            })

    return tasks
