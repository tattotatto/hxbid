"""宏曦标书 - 标题细化服务.

对 AI 撰写类型的章节，根据招标文件评分标准和服务需求，
将章节标题分解为 3-4 级子标题树。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import asyncio
import json
import logging

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

叶子节点是最终的生成任务，每个叶子节点应该是一个可以用 800-2000 字写清楚的具体主题。

分组标题（有 children 的节点）由系统在生成内容时自动补充引导段，因此你只需产出具体、非空泛的分组标题，不要用"概述""其他"等占位。"""

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


# 单次调用请求的叶子数上限。实测（2026-09-13）：68 个叶子 ≈ 6,669 字符稳定成功；
# 而一次要求 75-175 个叶子时输出到 23,460 字符撞 max_tokens=16384 被截断。
# 取 90（≈8,800 字符）留足余量——超出这个量必须分治，不能再单次硬要。
MAX_LEAVES_PER_CALL = 90

# 单个章节展开出的二级标题数上限（每个二级标题一次调用）。
MAX_SECTIONS = 16

# 第二遍（逐节展开叶子）的并发上限。串行时 16 个二级标题 × 约 40 秒 = 8-11 分钟，
# 全落在生成接口的 SSE 流里；不设限则会把推理服务打满。取 4 是吞吐与压力的折中。
MAX_CONCURRENCY = 4

EXPAND_SECTIONS_SYSTEM_PROMPT = """你是投标文件大纲设计专家。你的任务是为一个标书章节列出**二级标题**。

只列二级标题，不要展开它们的下级——下级由后续调用逐个展开。

要求：
- 每个二级标题对应一个独立的服务方向或管理模块
- 必须覆盖给定的每一个评分点（评分点是评委的给分项，漏一个就丢一项分）
- 评分权重高的方向可以拆得更细
- 用动宾结构或名词短语，避免"概述""其他"这类空泛标题

直接返回 JSON 对象，不要包含其他文字：
{"children": [{"title": "二级标题", "token_budget_hint": "large", "children": []}]}"""

EXPAND_LEAVES_SYSTEM_PROMPT = """你是投标文件大纲设计专家。你的任务是把一个二级标题展开为叶子节点。

叶子节点是最终的生成任务，每个叶子应该是一个能用 800-2000 字写清楚的具体主题。

要求：
- 叶子标题具体、可独立撰写，同级之间内容互补不重叠
- 评分权重高的方向展开更深
- 不要产出"概述""其他"这类空泛标题
- 叶子节点不再有下级（children 为空数组）

直接返回 JSON 对象，不要包含其他文字：
{"children": [{"title": "叶子标题", "token_budget_hint": "medium", "children": []}]}"""


def _build_req_lines(requirements: dict, scoring_context: str) -> list[str]:
    """拼装章节生成/展开共用的招标项目信息行（评分指标在此接入）."""
    req_lines: list[str] = []
    if requirements.get("project_name"):
        req_lines.append(f"项目名称：{requirements['project_name']}")
    if requirements.get("service_requirements"):
        req_lines.append(f"服务内容：{'；'.join(requirements['service_requirements'])}")
    rubric = requirements.get("scoring_rubric")
    if rubric and rubric.get("items"):
        from app.services.scoring_rubric import rubric_context_lines
        req_lines.extend(rubric_context_lines(rubric, scoring_context))
    elif requirements.get("evaluation_criteria"):
        req_lines.append(f"评标标准：{requirements['evaluation_criteria']}")
    if requirements.get("personnel_requirements"):
        req_lines.append(f"人员要求：{requirements['personnel_requirements']}")
    if requirements.get("special_requirements"):
        req_lines.append(f"特殊要求：{'；'.join(requirements['special_requirements'])}")
    return req_lines


async def _expand_once(ai_adapter, system_prompt: str, user_prompt: str,
                       temperature: float = 0.5) -> list[dict]:
    """一次展开调用 → children 列表；任何异常向上抛（调用方决定降级策略）."""
    response = await ai_adapter.chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=16384,
        response_format={"type": "json_object"},
    )
    result = json.loads(response)
    children = result.get("children", [])
    return children if isinstance(children, list) else []


async def expand_chapter_titles(
    chapter_title: str,
    chapter_meta: dict,
    requirements: dict,
    ai_adapter,
    target_pages: int = 2000,
    *,
    max_leaves_per_call: int = MAX_LEAVES_PER_CALL,
    max_sections: int = MAX_SECTIONS,
    max_concurrency: int = MAX_CONCURRENCY,
) -> list[dict]:
    """两级分治展开章节标题树（替代单次大调用，见 MAX_LEAVES_PER_CALL 说明）.

    第一遍：只出 8-max_sections 个二级标题（小 JSON）。
    第二遍：逐个二级标题展开叶子（并发上限 max_concurrency），
    每次请求的叶子数封顶在 max_leaves_per_call。

    降级策略（都不抛异常，避免整份标书生成中断）：
    - 第一遍失败 → 返回 []，调用方保留「整章当唯一叶子」的既有兜底
    - 某个二级标题展开失败 → 该节降级为叶子本身，不丢章节
    """
    if not ai_adapter:
        return []

    scoring_context = chapter_meta.get("scoring_context", "")
    req_lines = _build_req_lines(requirements, scoring_context)
    req_block = "\n".join(req_lines) if req_lines else "无额外信息"

    # 目标叶子总数与旧 leaf_max 同口径：scale 随目标页数放大
    scale = max(1.0, min(4.0, target_pages / 800))
    total_target = max(1, int(70 * scale))

    # ── 第一遍：二级标题 ──
    sections_prompt = f"""请为下面这一章列出二级标题（只列二级标题，不要展开下级）。

【章节标题】{chapter_title}
【评分上下文】{scoring_context or "无特殊评分要求"}
【整份标书目标页数】约 {target_pages} 页

【招标项目信息】
{req_block}

二级标题 {min(8, max_sections)}-{max_sections} 个。"""

    try:
        sections = await _expand_once(
            ai_adapter, EXPAND_SECTIONS_SYSTEM_PROMPT, sections_prompt)
    except Exception as exc:
        logger.error("标题展开第一遍失败（%s）：%s", chapter_title, exc)
        return []

    sections = [s for s in sections if isinstance(s, dict) and s.get("title")]
    if not sections:
        logger.error("标题展开第一遍返回空（%s）", chapter_title)
        return []
    sections = sections[:max_sections]

    # ── 第二遍：逐节展开叶子（有界并发）──
    per_section = max(1, min(max_leaves_per_call,
                             -(-total_target // len(sections))))

    async def _expand_section(section: dict, sem: asyncio.Semaphore) -> list[dict]:
        """单个二级标题 → 叶子列表；失败返回 [] 由调用方降级。

        信号量罩住整个 ``_expand_once``：并发上限约束的是在途请求数，
        而不是启动的协程数。
        """
        section_title = str(section.get("title"))
        leaves_prompt = f"""请把下面这个二级标题展开为叶子节点。

【所属章节】{chapter_title}
【二级标题】{section_title}
【评分上下文】{scoring_context or "无特殊评分要求"}
【整份标书目标页数】约 {target_pages} 页
本节需展开：总叶子节点数 {per_section} 个

【招标项目信息】
{req_block}"""
        try:
            async with sem:
                leaves = await _expand_once(
                    ai_adapter, EXPAND_LEAVES_SYSTEM_PROMPT, leaves_prompt)
        except Exception as exc:
            # 该节降级为叶子本身：内容照常撰写，不丢章节
            logger.error("标题展开第二遍失败（%s / %s）：%s",
                         chapter_title, section_title, exc)
            return []
        return [n for n in leaves if isinstance(n, dict) and n.get("title")]

    sem = asyncio.Semaphore(max(1, max_concurrency))
    leaves_per_section = await asyncio.gather(
        *(_expand_section(s, sem) for s in sections))

    # gather 保序，与 sections 一一对应；失败的节拿到 [] 走降级
    for section, leaves in zip(sections, leaves_per_section):
        if leaves:
            for leaf in leaves:
                leaf.setdefault("token_budget_hint", "medium")
                leaf["children"] = []
            section["children"] = leaves
        else:
            logger.warning("标题展开第二遍返回空（%s / %s），降级为单叶子",
                           chapter_title, section.get("title"))

    for section in sections:
        section.setdefault("token_budget_hint", "large")
        section.setdefault("children", [])

    leaf_total = count_leaves(sections)
    logger.info("展开 '%s'：%d 个二级标题、%d 个叶子（目标 %d）",
                chapter_title, len(sections), leaf_total, total_target)
    return sections


async def refine_chapter_titles(
    chapter_title: str,
    chapter_meta: dict,
    requirements: dict,
    ai_adapter,
    target_pages: int = 2000,
) -> list[dict]:
    """将 AI 撰写章节的标题细化为子标题树.

    Args:
        chapter_title: 章节标题（如"服务方案"）
        chapter_meta: 章节元数据（scoring_context, format_notes 等）
        requirements: 解析后的招标要求
        ai_adapter: AI 适配器
        target_pages: 整份标书目标页数，用于放大叶子节点数（页数越多，标题越细）

    Returns:
        子标题树列表，每个节点:
        {"title": str, "children": [...], "token_budget_hint": str}
    """
    if not ai_adapter:
        return []

    # Build context
    scoring_context = chapter_meta.get("scoring_context", "")
    format_notes = chapter_meta.get("format_notes", "")

    req_lines = _build_req_lines(requirements, scoring_context)

    # 按目标页数放大叶子数：默认 2000 页时每章 75-175 个叶子（技术部分单章即可覆盖大部分评分项）
    scale = max(1.0, min(4.0, target_pages / 800))
    leaf_min = int(30 * scale)
    leaf_max = int(70 * scale)

    user_prompt = f"""请将以下章节展开为 3-4 级子标题树。

【章节标题】{chapter_title}
【评分上下文】{scoring_context or "无特殊评分要求"}
【格式说明】{format_notes or "无特殊格式要求"}
【整份标书目标页数】约 {target_pages} 页——请据此把标题展开到足够细，确保每个叶子节点都能写出 800-2000 字的充实内容，避免叶子过少导致内容空洞。

【招标项目信息】
{chr(10).join(req_lines) if req_lines else "无额外信息"}

要求：
- 二级标题 8-16 个
- 总叶子节点数 {leaf_min}-{leaf_max} 个
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
            max_tokens=16384,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)
        children = result.get("children", [])

        leaf_count = count_leaves(children)
        logger.info(
            "Refined '%s': %d top-level children, %d leaf nodes",
            chapter_title, len(children), leaf_count,
        )

        return children

    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Title refinement failed for '%s': %s", chapter_title, exc)
        return []


def annotate_tree_depths(nodes: list, base: int = 0) -> list:
    """递归为树的每个节点写入 depth（从 base 起算），返回原树。

    容器与叶子节点都会标注；叶子节点的 depth 与 flatten_children_to_tasks
    产出的任务 depth（len(path)-1）保持一致。
    """
    for node in nodes:
        node["depth"] = base
        kids = node.get("children", [])
        if kids:
            annotate_tree_depths(kids, base + 1)
    return nodes


def count_leaves(nodes: list) -> int:
    """统计树中叶子节点（无 children 的节点）总数。"""
    count = 0
    for n in nodes:
        kids = n.get("children", [])
        if kids:
            count += count_leaves(kids)
        else:
            count += 1
    return count


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
