"""宏曦标书 - 逐章生成流程辅助函数 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.ai_pipeline import (
    _filter_requirements_for_chapter,
    _is_leaf_done,
    _mark_leaf_failure,
)


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


class TestFilterRequirementsForChapter:
    def test_personnel_keyed_on_role_retained_when_matching(self):
        reqs = {
            "project_name": "某保安项目",
            "required_personnel": [
                {"role": "项目负责人", "certifications": ["保安师证"], "count": 1},
                {"role": "消防员", "certifications": [], "count": 2},
            ],
        }
        filtered = _filter_requirements_for_chapter(reqs, "项目负责人配置方案")
        roles = [p["role"] for p in filtered["required_personnel"]]
        assert roles == ["项目负责人"]

    def test_personnel_top_level_keys_preserved(self):
        reqs = {
            "project_name": "某保安项目",
            "required_personnel": [
                {"role": "项目负责人", "count": 1},
            ],
        }
        filtered = _filter_requirements_for_chapter(reqs, "服务方案")
        # 无关岗位被移除，但顶层键 project_name 保留
        assert filtered["project_name"] == "某保安项目"
        assert filtered["required_personnel"] == []
