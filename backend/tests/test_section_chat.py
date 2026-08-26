"""宏曦标书 - 章节对话 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import json
from unittest.mock import AsyncMock
import pytest
from app.services.section_editor import chat_section


class TestChatSection:
    @pytest.mark.asyncio
    async def test_returns_reply_and_revised_content(self):
        ai = AsyncMock()
        ai.chat_completion.return_value = json.dumps(
            {"reply": "已按你的要求补充了人员配置。", "revised_content": "第一章 服务方案\n新增内容"}
        )
        result = await chat_section(
            chapter_title="第一章",
            section_path=["第一章", "服务方案"],
            current_content="旧内容",
            messages=[{"role": "user", "content": "补充人员配置"}],
            ai_adapter=ai,
            materials_guidance="【素材】人员：李四",
        )
        assert result["reply"] == "已按你的要求补充了人员配置。"
        assert result["revised_content"] == "第一章 服务方案\n新增内容"

    @pytest.mark.asyncio
    async def test_requires_ai_adapter(self):
        with pytest.raises(RuntimeError):
            await chat_section(
                chapter_title="第一章", section_path=["第一章"],
                current_content="", messages=[], ai_adapter=None,
            )

    @pytest.mark.asyncio
    async def test_empty_response_raises(self):
        ai = AsyncMock()
        ai.chat_completion.return_value = ""
        with pytest.raises(RuntimeError):
            await chat_section(
                chapter_title="第一章", section_path=["第一章"],
                current_content="", messages=[], ai_adapter=ai,
            )
