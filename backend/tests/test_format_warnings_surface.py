"""宏曦标书 - 生成期降级告警必须能浮到用户眼前 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.ai_pipeline import _merge_format_warnings


class TestMergeFormatWarnings:
    def test_no_warnings_leaves_status_untouched(self):
        v = {"overall_status": "pass", "warnings": []}
        out = _merge_format_warnings(v, [])
        assert out["overall_status"] == "pass"
        assert out["warnings"] == []

    def test_warnings_upgrade_pass_to_pass_with_warnings(self):
        """只看 warnings 列表是没用的——前端只读 overall_status。

        旧实现把降级告警追加进 verification["warnings"] 就完事，而
        overall_status 在追加**之前**已算好，于是"所有固定格式章节都退回 AI
        撰写"的项目在界面上仍然显示「通过」，且 warnings 全仓无读者。
        """
        v = {"overall_status": "pass", "warnings": []}
        out = _merge_format_warnings(v, ["「投标函」未匹配到招标原文，已由 AI 撰写"])
        assert out["overall_status"] == "pass_with_warnings"
        assert "投标函" in out["warnings"][0]

    def test_fail_is_not_downgraded(self):
        v = {"overall_status": "fail", "warnings": ["列数不符"]}
        out = _merge_format_warnings(v, ["「X」退 AI"])
        assert out["overall_status"] == "fail"
        assert len(out["warnings"]) == 2

    def test_existing_warnings_are_appended_not_replaced(self):
        v = {"overall_status": "pass", "warnings": ["原有的"]}
        out = _merge_format_warnings(v, ["新增的"])
        assert out["warnings"] == ["原有的", "新增的"]

    def test_missing_warnings_key_is_created(self):
        out = _merge_format_warnings({"overall_status": "pass"}, ["新"])
        assert out["warnings"] == ["新"]
        assert out["overall_status"] == "pass_with_warnings"
