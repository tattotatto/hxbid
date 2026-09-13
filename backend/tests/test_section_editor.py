"""宏曦标书 - 小节内容保存 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import json

from app.services.section_editor import save_section_content


TREE = json.dumps([
    {
        "title": "投标人基本资料",
        "children": [
            {"title": "投标人基本情况表", "content": "旧内容", "children": []},
            {"title": "有效的营业执照等证明文件", "content": "", "children": []},
        ],
    },
    {"title": "投标函", "content": "旧函件", "children": []},
], ensure_ascii=False)

FLAT = json.dumps([
    {"path": ["服务方案", "岗位职责"], "content": "旧内容"},
    {"path": ["服务方案", "人员配置"], "content": ""},
], ensure_ascii=False)


class TestSaveSectionContent:
    def test_leaf_content_is_saved_and_reports_matched(self):
        updated, matched = save_section_content(
            TREE, ["投标函"], "新函件内容"
        )
        assert matched is True
        node = json.loads(updated)[1]
        assert node["content"] == "新函件内容"
        assert node["human_edited"] is True

    def test_nested_leaf_is_saved_and_reports_matched(self):
        updated, matched = save_section_content(
            TREE, ["投标人基本资料", "投标人基本情况表"], "填好的表"
        )
        assert matched is True
        nested = json.loads(updated)[0]["children"][0]
        assert nested["content"] == "填好的表"
        assert nested["human_edited"] is True

    def test_container_node_writes_lead_in_not_content(self):
        updated, matched = save_section_content(
            TREE, ["投标人基本资料"], "引导段"
        )
        assert matched is True
        node = json.loads(updated)[0]
        assert node["lead_in"] == "引导段"
        assert "content" not in node

    def test_unknown_path_does_not_match(self):
        updated, matched = save_section_content(TREE, ["不存在的章节"], "内容")
        assert matched is False
        assert json.loads(updated) == json.loads(TREE)

    def test_partially_unknown_path_does_not_match(self):
        updated, matched = save_section_content(
            TREE, ["投标人基本资料", "并不存在的子节"], "内容"
        )
        assert matched is False
        assert json.loads(updated) == json.loads(TREE)

    def test_empty_path_does_not_match(self):
        _, matched = save_section_content(TREE, [], "内容")
        assert matched is False

    def test_flat_task_list_matches_by_path(self):
        updated, matched = save_section_content(
            FLAT, ["服务方案", "岗位职责"], "新职责"
        )
        assert matched is True
        task = json.loads(updated)[0]
        assert task["content"] == "新职责"
        assert task["human_edited"] is True

    def test_flat_task_list_unknown_path_does_not_match(self):
        updated, matched = save_section_content(
            FLAT, ["服务方案", "不存在的节"], "内容"
        )
        assert matched is False
        assert json.loads(updated) == json.loads(FLAT)

    def test_invalid_json_reports_not_matched_and_returns_original(self):
        updated, matched = save_section_content("不是 json", ["任意"], "内容")
        assert matched is False
        assert updated == "不是 json"

    def test_empty_tree_does_not_match(self):
        _, matched = save_section_content("[]", ["任意"], "内容")
        assert matched is False
