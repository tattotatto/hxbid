"""导出请求/响应契约新增字段默认值测试."""
# Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.

from app.schemas.bid import ExportRequest, ExportResponse


class TestChecklistContractDefaults:
    def test_export_request_checklist_defaults(self):
        # 默认 include_checklist=True：旧前端调新后端默认多产出清单，无破坏（spec §8）
        req = ExportRequest(project_id="p1")
        assert req.include_checklist is True
        assert req.checklist_items is None
        assert req.checklist_removed is None

    def test_export_request_accepts_checklist_payload(self):
        req = ExportRequest(
            project_id="p1",
            include_checklist=False,
            checklist_items=[{"key": "custom-1", "label": "项目实施方案"}],
            checklist_removed=["performance"],
        )
        assert req.include_checklist is False
        assert req.checklist_items[0]["label"] == "项目实施方案"
        assert req.checklist_removed == ["performance"]

    def test_export_response_checklist_urls_default_empty(self):
        res = ExportResponse()
        assert res.checklist_docx_url == ""
        assert res.checklist_pdf_url == ""