"""宏曦标书 - 生成入口的状态守卫 单元测试.

线上事故：用户在**没确认目录**的项目上点了「一键生成标书」—— 整条链路放行了它
（守卫只黑名单了 collecting），于是项目进了 generating；随后他跳去目录页点
「确认并继续」，被 400 拦住，提示还是「请先 POST /extract-chapters」这种对用户
毫无意义的话。而生成流在客户端断开时不会被复位，项目就永久卡在 generating。

守卫改成**白名单**：只有"目录已确认且信息搜集已完成"之后的状态才允许生成。
"""

from app.api.bid import _generation_blocked_reason


class TestGenerationStatusGuard:
    def test_structure_ready_is_blocked(self):
        """**没确认目录就不许生成** —— 这条是本次事故的入口."""
        reason = _generation_blocked_reason("structure_ready")
        assert reason is not None
        assert "确认" in reason, f"提示要让用户知道去确认目录，got {reason!r}"

    def test_collecting_is_blocked(self):
        reason = _generation_blocked_reason("collecting")
        assert reason is not None
        assert "信息搜集" in reason

    def test_draft_is_blocked(self):
        assert _generation_blocked_reason("draft") is not None

    def test_archived_is_blocked(self):
        assert _generation_blocked_reason("archived") is not None

    def test_parsed_is_allowed(self):
        """搜集完成 → 可以生成（正常主路径）."""
        assert _generation_blocked_reason("parsed") is None

    def test_review_and_error_are_allowed(self):
        """已生成完/失败 —— 允许重跑."""
        assert _generation_blocked_reason("review") is None
        assert _generation_blocked_reason("error") is None
        assert _generation_blocked_reason("exported") is None

    def test_generating_is_allowed(self):
        """卡在 generating 的项目必须能重跑（否则用户没出路）."""
        assert _generation_blocked_reason("generating") is None

    def test_unknown_status_is_blocked(self):
        """不在白名单里的一律拒绝 —— 宁可拦错也不要放行未知状态."""
        assert _generation_blocked_reason("") is not None
        assert _generation_blocked_reason("胡说") is not None


class TestUnstickGenerating:
    """生成流异常结束时把项目复位，别让它永久卡在 generating.

    触发场景（线上实测）：用户点了生成，随后跳去目录页 —— SSE 客户端断开，
    生成器被取消。`except Exception` **抓不到 CancelledError**（Python 3.8 起
    它是 BaseException），于是既不写 review 也不写 error，状态永远停在
    generating；用户再点确认目录被 400 拦住，没有任何出路。
    `except ValueError`（标题未细化）那条路同样不复位。
    """

    class _FakeProject:
        def __init__(self, status):
            self.status = status

    class _FakeSession:
        def __init__(self, project, committed):
            self._project = project
            self._committed = committed

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, _model, _pk):
            return self._project

        async def commit(self):
            self._committed.append(self._project.status)

    @staticmethod
    def _factory(project, committed):
        def make():
            return TestUnstickGenerating._FakeSession(project, committed)
        return make

    def test_resets_generating_to_parsed(self):
        import asyncio
        from app.api.bid import _unstick_generating

        proj = TestUnstickGenerating._FakeProject("generating")
        committed = []
        done = asyncio.run(_unstick_generating(
            "p1", session_factory=TestUnstickGenerating._factory(proj, committed)))
        assert done is True
        assert proj.status == "parsed"
        assert committed == ["parsed"]

    def test_leaves_review_untouched(self):
        """已正常收尾（review）不许被复位覆盖."""
        import asyncio
        from app.api.bid import _unstick_generating

        proj = TestUnstickGenerating._FakeProject("review")
        committed = []
        done = asyncio.run(_unstick_generating(
            "p1", session_factory=TestUnstickGenerating._factory(proj, committed)))
        assert done is False
        assert proj.status == "review"
        assert committed == []

    def test_leaves_error_untouched(self):
        """异常路径已写 error，也不该被覆盖."""
        import asyncio
        from app.api.bid import _unstick_generating

        proj = TestUnstickGenerating._FakeProject("error")
        done = asyncio.run(_unstick_generating(
            "p1", session_factory=TestUnstickGenerating._factory(proj, [])))
        assert done is False
        assert proj.status == "error"

    def test_missing_project_is_not_an_error(self):
        import asyncio
        from app.api.bid import _unstick_generating

        done = asyncio.run(_unstick_generating(
            "gone", session_factory=TestUnstickGenerating._factory(None, [])))
        assert done is False

    def test_db_failure_is_swallowed(self):
        """复位本身失败不能把生成流再炸一次."""
        import asyncio
        from app.api.bid import _unstick_generating

        def boom():
            raise RuntimeError("db down")

        assert asyncio.run(_unstick_generating("p1", session_factory=boom)) is False


class TestConfirmBlockedReason:
    """确认目录被拒时，提示必须让用户知道**该去做什么**.

    线上用户看到的是：「当前项目状态 generating 不允许确认目录。请先 POST
    /extract-chapters，再走确认流程。」—— 一个 API 指令，对用户毫无意义，
    而且他没做错任何事（是先生成了才来确认）。
    """

    def test_generating_tells_about_generation(self):
        from app.api.chapters import _confirm_blocked_reason

        reason = _confirm_blocked_reason("generating")
        assert "生成" in reason
        assert "POST" not in reason, "不该把 API 指令甩给用户"

    def test_collecting_says_already_confirmed(self):
        from app.api.chapters import _confirm_blocked_reason

        reason = _confirm_blocked_reason("collecting")
        assert "信息搜集" in reason or "已确认" in reason
        assert "POST" not in reason

    def test_review_says_locked(self):
        from app.api.chapters import _confirm_blocked_reason

        assert _confirm_blocked_reason("review")
        assert "POST" not in _confirm_blocked_reason("review")

    def test_unknown_status_still_readable(self):
        from app.api.chapters import _confirm_blocked_reason

        reason = _confirm_blocked_reason("胡说")
        assert "胡说" in reason and "POST" not in reason
