"""流式补全的空返回守卫测试.

``chat_completion_stream`` 只 ``yield delta.content``，把不 carry content 的
chunk（含 ``delta.reasoning_content``）**连同 finish_reason 一起丢掉**。
而非流式 ``chat_completion`` 有显式防御：

    if not content:
        raise RuntimeError(f"AI returned empty content (finish_reason={finish}, "
                           f"max_tokens={kwargs.get('max_tokens')}). ...")

容器日志里这条消息救过场：

    05:24:59 Variable scanning failed: AI returned empty content
             (finish_reason=length, max_tokens=8192). Increase max_tokens
             to leave room after reasoning_tokens.

流式路径没有这道防线 → ``_gen_one`` 只能记一个无信息的 ``empty_content``，
看不出「是推理把预算吃光了」还是「模型没话说」。这里把同样的诊断补上。

注意**不能对「有内容但被 length 截断」抛异常**：那正是 70% 叶子的常态，
抛了会把已经写好的半篇正文丢掉重跑，代价太大。只有「一个字符都没吐」
才抛 —— 那种情况丢掉的本来就是空。
"""

from types import SimpleNamespace

import pytest

from app.services import ai_adapter


def _chunk(content=None, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )]
    )


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        async def gen():
            for c in self._chunks:
                yield c
        return gen()


class _FakeClient:
    def __init__(self, chunks):
        self._chunks = chunks
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    async def _create(self, **kwargs):
        return _FakeStream(self._chunks)


def _patch(monkeypatch, chunks):
    client = _FakeClient(chunks)
    monkeypatch.setattr(ai_adapter.ai_adapter, "_get_client", lambda p=None: client)
    monkeypatch.setattr(ai_adapter.ai_adapter, "get_model", lambda p=None: "fake-model")


async def _drain(**kwargs):
    out = []
    async for piece in ai_adapter.ai_adapter.chat_completion_stream(**kwargs):
        out.append(piece)
    return out


class TestStreamYieldsContent:
    @pytest.mark.asyncio
    async def test_content_chunks_are_yielded(self, monkeypatch):
        _patch(monkeypatch, [_chunk("正文"), _chunk("内容", finish_reason="stop")])
        assert await _drain(messages=[], max_tokens=16384) == ["正文", "内容"]


class TestStreamEmptyGuard:
    @pytest.mark.asyncio
    async def test_reasoning_only_stream_raises_with_finish_reason(self, monkeypatch):
        """推理吃满预算：chunk 只为 reasoning，正文一个字没有 -> 必须抛."""
        _patch(monkeypatch, [
            _chunk(None), _chunk(None), _chunk(None, finish_reason="length"),
        ])
        with pytest.raises(RuntimeError) as exc:
            await _drain(messages=[], max_tokens=3525)
        assert "length" in str(exc.value)
        assert "3525" in str(exc.value)

    @pytest.mark.asyncio
    async def test_wholly_empty_stream_raises(self, monkeypatch):
        _patch(monkeypatch, [])
        with pytest.raises(RuntimeError):
            await _drain(messages=[], max_tokens=3525)

    @pytest.mark.asyncio
    async def test_message_points_at_reasoning_tokens(self, monkeypatch):
        """诊断要能指向「给推理留空间」这个真因，而不是含糊的 empty."""
        _patch(monkeypatch, [_chunk(None, finish_reason="length")])
        with pytest.raises(RuntimeError) as exc:
            await _drain(messages=[], max_tokens=8192)
        assert "reasoning" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_truncated_with_content_does_not_raise(self, monkeypatch):
        """有正文但被 length 截断 —— 大红山 70% 叶子的常态，不能抛.

        抛了等于把半篇正文丢掉重跑，代价远大于收益。
        """
        _patch(monkeypatch, [_chunk("半篇正文"), _chunk(None, finish_reason="length")])
        assert await _drain(messages=[], max_tokens=3525) == ["半篇正文"]
