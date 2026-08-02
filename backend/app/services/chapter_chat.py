"""宏曦标书 - 章节对话编辑服务.

提供多轮对话式的章节结构编辑功能。
用户通过自然语言与 AI 对话来修改投标文件的章节结构。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

CHAPTER_CHAT_SYSTEM_PROMPT = """你是投标文件章节结构编辑助手。用户正在审阅从招标文件提取的投标文件章节列表，
你可以帮助用户修改章节结构。

你的能力：
1. 新增章节 — 在指定位置插入新章节
2. 删除章节 — 移除不需要的章节
3. 修改章节 — 改标题、改类型、改序号
4. 调整顺序 — 重新排列章节顺序
5. 拆分章节 — 将一个章节拆成多个
6. 合并章节 — 将多个章节合并为一个
7. 修改章节属性 — 修改 format_notes、scoring_context 等

当前章节列表就是你操作的上下文。用户每次提出修改建议后，你需要：
1. 理解用户的修改意图
2. 修改章节列表 JSON
3. 在回复中简要说明做了什么修改
4. 返回修改后的完整章节列表

回复格式：
{{
  "reply": "已按照您的要求...",
  "chapters": [...修改后的完整章节列表...]
}}

章节结构格式：
[
  {{
    "order_index": 1,
    "number": "一",
    "title": "投标函",
    "type": "fixed_form",
    "required": true,
    "format_notes": "须按招标文件固定格式",
    "children": []
  }}
]

type 取值: fixed_form | table | ai_generated | attachment | mixed

注意事项：
- 保持 order_index 连续
- 保持 number 序号与 order_index 一致
- 不要丢失已有的 format_notes 和 scoring_context
- 用户说"拆成XX和YY"意味着把一个章节替换为两个或多个
- 回复要简洁、确认性，让用户知道改了什么
- 直接返回上面的JSON格式，不要包含其他文字"""


async def chat_edit_chapters(
    chapters_json: str,
    user_message: str,
    conversation_id: str | None = None,
    ai_adapter=None,
) -> dict:
    """通过对话编辑章节结构.

    Args:
        chapters_json: 当前的章节列表 JSON 字符串
        user_message: 用户的修改建议
        conversation_id: 对话 ID（用于多轮上下文，可选）
        ai_adapter: AI 适配器实例

    Returns:
        {
            "reply": "AI 的回复",
            "chapters": [...],
            "conversation_id": "...",
        }
    """
    if not ai_adapter:
        raise ValueError("AI adapter is required")

    conv_id = conversation_id or str(uuid.uuid4())[:8]

    # 解析当前章节
    try:
        current_chapters = json.loads(chapters_json) if isinstance(chapters_json, str) else chapters_json
    except json.JSONDecodeError:
        current_chapters = []

    chapters_str = json.dumps(current_chapters, ensure_ascii=False, indent=2)

    user_prompt = f"""当前投标文件章节列表：
```json
{chapters_str}
```

用户的修改建议：
{user_message}

请根据用户的建议修改章节列表，返回修改后的完整列表。"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": CHAPTER_CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)

        # Normalize
        reply = result.get("reply", "已更新章节列表。")
        updated_chapters = result.get("chapters", current_chapters)

        # Fix order_index after modifications
        for i, ch in enumerate(updated_chapters):
            ch["order_index"] = i + 1

        logger.info(
            "Chapter chat edit: '%s' -> %d chapters",
            user_message[:50], len(updated_chapters),
        )

        return {
            "reply": reply,
            "chapters": updated_chapters,
            "conversation_id": conv_id,
        }

    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Chapter chat edit failed: %s", exc)
        return {
            "reply": f"抱歉，处理章节修改时出现错误：{exc}",
            "chapters": current_chapters,
            "conversation_id": conv_id,
        }
