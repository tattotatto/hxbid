"""宏曦标书 - 逐章生成流程辅助函数 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.ai_pipeline import _is_leaf_done, _mark_leaf_failure


class TestMarkLeafFailure:
    def test_marks_nested_leaf(self):
        tree = [{"title": "第一章", "children": [{"title": "服务方案", "content": "旧"}]}]
        _mark_leaf_failure(tree, ["第一章", "服务方案"], "AI 超时")
        leaf = tree[0]["children"][0]
        assert leaf["status"] == "failed"
        assert leaf["error"] == "AI 超时"

    def test_flat_task_list_supported(self):
        tree = [{"title": "服务方案", "path": ["第一章", "服务方案"], "content": "旧"}]
        _mark_leaf_failure(tree, ["第一章", "服务方案"], "AI 超时")
        assert tree[0]["status"] == "failed"


class TestIsLeafDone:
    def test_generated_with_content_is_done(self):
        assert _is_leaf_done({"status": "generated", "content": "正文"}) is True

    def test_failed_is_not_done(self):
        assert _is_leaf_done({"status": "failed", "error": "x"}) is False

    def test_no_status_is_not_done(self):
        assert _is_leaf_done({"content": "旧内容"}) is False
