"""非流式补全「空返回」的专用异常类型.

``chat_completion`` 早已在 content 为空时 raise，但抛的是裸 ``RuntimeError``：
调用方无法把它和「AI 没话说」「JSON 坏掉」区分开。大红山的扫描调用用
``except (json.JSONDecodeError, Exception)`` 一锅端，于是「max_tokens 给低了」
这条配置问题被渲染成一句含糊的「AI扫描失败」，整条固定格式填充链路静默
回退 AI 自由生成，潜伏至今才被挖出来。

这里给它一个可单独捕获的类型：语义是**预算配置问题**，不是数据问题。
"""

from types import SimpleNamespace

import pytest

from app.services import ai_adapter


class _FakeClient:
    def __init__(self, content, finish_reason):
        self._content = content
        self._finish_reason = finish_reason
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    async def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self._content),
            finish_reason=self._finish_reason,
        )])


def _patch(monkeypatch, content, finish_reason):
    client = _FakeClient(content, finish_reason)
    monkeypatch.setattr(ai_adapter.ai_adapter, "_get_client", lambda p=None: client)
    monkeypatch.setattr(ai_adapter.ai_adapter, "get_model", lambda p=None: "fake-model")
    return client


class TestEmptyContentErrorType:
    @pytest.mark.asyncio
    async def test_empty_content_raises_dedicated_type(self, monkeypatch):
        """推理吃满预算导致 content 为空 → 专用类型，而不是裸 RuntimeError."""
        from app.services.ai_adapter import AIEmptyContentError

        _patch(monkeypatch, content="", finish_reason="length")
        with pytest.raises(AIEmptyContentError):
            await ai_adapter.ai_adapter.chat_completion(
                messages=[], max_tokens=8192,
            )

    @pytest.mark.asyncio
    async def test_dedicated_type_is_catchable_as_runtime_error(self, monkeypatch):
        """仍是 RuntimeError 的子类——既有调用方的 except RuntimeError 不受影响."""
        from app.services.ai_adapter import AIEmptyContentError

        assert issubclass(AIEmptyContentError, RuntimeError)

        _patch(monkeypatch, content="", finish_reason="length")
        with pytest.raises(RuntimeError):
            await ai_adapter.ai_adapter.chat_completion(
                messages=[], max_tokens=8192,
            )

    @pytest.mark.asyncio
    async def test_message_keeps_finish_reason_and_budget(self, monkeypatch):
        """诊断信息必须保住 finish_reason 与 max_tokens，否则没法定位."""
        from app.services.ai_adapter import AIEmptyContentError

        _patch(monkeypatch, content="", finish_reason="length")
        with pytest.raises(AIEmptyContentError) as exc:
            await ai_adapter.ai_adapter.chat_completion(
                messages=[], max_tokens=8192,
            )
        assert "length" in str(exc.value)
        assert "8192" in str(exc.value)


class TestOutputTruncated:
    """content 非空但被 length 截断——生成路径容忍，改写路径必须能拒.

    ai_adapter 此前对这种情况**故意不抛**（见 chat_completion_stream 注释：
    那是大红山 70% 叶子的常态，半篇好过没有）。所以默认行为保持不变，
    由调用方按 ``raise_on_truncation=True`` 自己选择要不要严格。
    """

    @pytest.mark.asyncio
    async def test_default_tolerates_truncation(self, monkeypatch):
        """默认不抛——生成路径靠这个宽容度活下来，不能改."""
        _patch(monkeypatch, content="写到一半的半篇正文", finish_reason="length")
        out = await ai_adapter.ai_adapter.chat_completion(messages=[], max_tokens=4096)
        assert out == "写到一半的半篇正文"

    @pytest.mark.asyncio
    async def test_opt_in_raises_dedicated_type(self, monkeypatch):
        from app.services.ai_adapter import AIOutputTruncatedError

        _patch(monkeypatch, content="写到一半的半篇正文", finish_reason="length")
        with pytest.raises(AIOutputTruncatedError) as exc:
            await ai_adapter.ai_adapter.chat_completion(
                messages=[], max_tokens=4096, raise_on_truncation=True,
            )
        assert "length" in str(exc.value)
        assert "4096" in str(exc.value)

    @pytest.mark.asyncio
    async def test_opt_in_passes_normal_completion(self, monkeypatch):
        _patch(monkeypatch, content="完整正文", finish_reason="stop")
        out = await ai_adapter.ai_adapter.chat_completion(
            messages=[], max_tokens=4096, raise_on_truncation=True,
        )
        assert out == "完整正文"

    @pytest.mark.asyncio
    async def test_truncated_type_is_catchable_as_runtime_error(self):
        from app.services.ai_adapter import AIOutputTruncatedError

        assert issubclass(AIOutputTruncatedError, RuntimeError)
