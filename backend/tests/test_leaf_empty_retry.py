"""叶子生成：空返回重试 + target_chars 透传测试.

背景（2026-09-13 大红山「服务方案」180 叶三个来回实测）：

    第 1 轮  180 叶 → 44 失败（24.4%）
    第 2 轮  重跑 44 → 18 失败（40.9%）
    第 3 轮  重跑 18 →  8 失败（44.4%）

每一轮重跑都能捞回约 56-60% 的剩余失败 —— 空返回**主要是暂态**的，不是这些
叶子本身有问题。而 ``_gen_one`` 当时是**不重试**的：流一个字符都没吐就写
``error: "empty_content"`` 收工。

同一个文件里本来就有 ``_generate_single_section_with_retry``（带指数退避的
重试版），但**一个调用方都没有** —— ``_gen_one`` 自己重写了一遍循环且没有
重试。这里锁定「走回那个重试版」，而不是再抄一份。
"""

import pytest

from app.services import subsection_generator
from app.services.ai_pipeline import _generate_single_section_with_retry


def _fake_generate_section(fail_times: int, text: str = "正文内容"):
    """前 *fail_times* 次返回空流，之后返回 *text*。记录调用参数供断言。"""
    state = {"n": 0, "last_kwargs": None}

    async def fake(**kwargs):
        state["n"] += 1
        state["last_kwargs"] = kwargs
        if state["n"] <= fail_times:
            return
        yield text

    return fake, state


def _leaf(**overrides):
    leaf = {
        "title": "项目背景、服务范围与需求理解",
        "path": ["服务方案", "服务总体理解与整体实施方案",
                 "项目背景、服务范围与需求理解"],
        "depth": 2,
        "max_tokens": 16384,
        "target_chars": 1400,
    }
    leaf.update(overrides)
    return leaf


async def _run(leaf, **kw):
    return await _generate_single_section_with_retry(
        leaf=leaf,
        requirements={},
        company_profile=None,
        reference_sections=[],
        retry_delay_base=0,
        **kw,
    )


class TestEmptyContentRetry:
    @pytest.mark.asyncio
    async def test_retries_after_empty_stream(self, monkeypatch):
        """空流不是终局 —— 重试一次就该拿到正文."""
        fake, state = _fake_generate_section(fail_times=1)
        monkeypatch.setattr(subsection_generator, "generate_section", fake)

        content, err = await _run(_leaf(), max_retries=2)

        assert err is None
        assert content == "正文内容"
        assert state["n"] == 2

    @pytest.mark.asyncio
    async def test_whitespace_only_stream_also_retries(self, monkeypatch):
        """只有空白字符等于空 —— 旧实现用 .strip() 判定，这里锁定该语义."""
        fake, state = _fake_generate_section(fail_times=1, text="\n\n   \n")
        monkeypatch.setattr(subsection_generator, "generate_section", fake)

        # 第一次吐空白 -> 判定空 -> 重试；第二次仍吐空白 -> 最终失败
        content, err = await _run(_leaf(), max_retries=1)

        assert content is None
        assert err is not None
        assert state["n"] == 2

    @pytest.mark.asyncio
    async def test_exhausts_attempts_then_reports_error(self, monkeypatch):
        fake, state = _fake_generate_section(fail_times=99)
        monkeypatch.setattr(subsection_generator, "generate_section", fake)

        content, err = await _run(_leaf(), max_retries=2)

        assert content is None
        assert "empty" in err.lower()
        # max_retries=2 → 共 3 次尝试（对齐实测「重跑两轮 44→18→8」）
        assert state["n"] == 3

    @pytest.mark.asyncio
    async def test_no_retry_when_first_attempt_succeeds(self, monkeypatch):
        fake, state = _fake_generate_section(fail_times=0)
        monkeypatch.setattr(subsection_generator, "generate_section", fake)

        content, err = await _run(_leaf())

        assert (content, err) == ("正文内容", None)
        assert state["n"] == 1


class TestTargetCharsReachesGenerator:
    """篇幅目标必须传到底层，否则 prompt 又回落到拿 headroom 当篇幅."""

    @pytest.mark.asyncio
    async def test_forwards_target_chars_and_headroom(self, monkeypatch):
        fake, state = _fake_generate_section(fail_times=0)
        monkeypatch.setattr(subsection_generator, "generate_section", fake)

        await _run(_leaf(target_chars=1375, max_tokens=16384))

        assert state["last_kwargs"]["target_chars"] == 1375
        assert state["last_kwargs"]["max_tokens"] == 16384

    @pytest.mark.asyncio
    async def test_target_chars_absent_is_tolerated(self, monkeypatch):
        """老任务字典没有 target_chars 时不能炸（回落到旧行为）."""
        fake, state = _fake_generate_section(fail_times=0)
        monkeypatch.setattr(subsection_generator, "generate_section", fake)

        leaf = _leaf()
        leaf.pop("target_chars")
        content, err = await _run(leaf)

        assert (content, err) == ("正文内容", None)
        assert state["last_kwargs"]["target_chars"] is None
