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
