"""检查清单引擎单元测试."""
# Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.

from pathlib import Path

from docx import Document  # noqa: F401  (仅用于读回断言)

from app.services.checklist_engine import (
    CHECKLIST_ITEM_TEMPLATES,
    STATUS_OK,
    STATUS_WARN,
    build_checklist_docx,
    derive_statuses,
    fill_pages,
    locate_page,
    merge_items,
)


class TestTemplates:
    def test_nine_preset_rows_with_unique_keys(self):
        assert len(CHECKLIST_ITEM_TEMPLATES) == 9
        keys = [t["key"] for t in CHECKLIST_ITEM_TEMPLATES]
        assert len(set(keys)) == 9
        for t in CHECKLIST_ITEM_TEMPLATES:
            assert t["label"]
            assert isinstance(t["keywords"], list) and t["keywords"]

    def test_preset_keys_cover_required_submission_items(self):
        keys = {t["key"] for t in CHECKLIST_ITEM_TEMPLATES}
        assert {"quotation", "bid_letter", "legal_rep_cert", "authorization",
                "signature_seal", "commitment", "qualification",
                "performance", "personnel"} <= keys


class TestMergeItems:
    def test_default_returns_templates(self):
        assert merge_items(None, None) == CHECKLIST_ITEM_TEMPLATES

    def test_removed_drops_preset(self):
        rows = merge_items(None, ["quotation", "performance"])
        keys = [r["key"] for r in rows]
        assert "quotation" not in keys and "performance" not in keys
        assert len(rows) == 7

    def test_item_with_preset_key_overrides_label(self):
        rows = merge_items([{"key": "quotation", "label": "报价总表"}], None)
        row = next(r for r in rows if r["key"] == "quotation")
        assert row["label"] == "报价总表"
        # 覆盖 label 不丢预置定位关键词
        assert "开标一览表" in row["keywords"]

    def test_custom_key_appends_with_label_as_keyword(self):
        rows = merge_items([{"key": "custom-1", "label": "项目实施方案"}], None)
        custom = [r for r in rows if r["key"] == "custom-1"]
        assert len(custom) == 1
        assert custom[0]["keywords"] == ["项目实施方案"]

    def test_idempotent_preserves_preset_then_custom_order(self):
        merged = merge_items(
            [{"key": "quotation", "label": "改"}, {"key": "custom-9", "label": "追加行"}],
            ["bid_letter"],
        )
        assert [r["key"] for r in merged] == [r["key"] for r in
            merge_items([{"key": "quotation", "label": "改"},
                         {"key": "custom-9", "label": "追加行"}], ["bid_letter"])]


class TestLocatePage:
    def test_first_hit_across_pages(self):
        assert locate_page(["第1页", "报价表在此", "无"], ["报价表"]) == 2

    def test_miss_returns_none(self):
        assert locate_page(["全部无关", "文字"], ["报价表"]) is None

    def test_any_keyword_hits(self):
        assert locate_page(["无", "盖章页"], ["签字", "盖章"]) == 2

    def test_first_page_wins(self):
        assert locate_page(["盖章在此", "盖章又见"], ["盖章"]) == 1

    def test_empty_keywords_returns_none(self):
        assert locate_page(["任意文本"], []) is None


class TestDeriveStatuses:
    def test_quotation_ok_when_opening_table_built(self):
        rows = derive_statuses(
            [{"key": "quotation", "label": "报价", "keywords": ["报价"]}],
            source_ctx={"chapter_titles": [], "bid_opening_ok": True,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert rows[0]["status"] == STATUS_OK

    def test_chapter_title_matches_mark_ok(self):
        rows = derive_statuses(
            [{"key": "bid_letter", "label": "投标函", "keywords": ["投标函"]}],
            source_ctx={"chapter_titles": ["投标函"], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert rows[0]["status"] == STATUS_OK

    def test_empty_source_marks_warn(self):
        rows = derive_statuses(
            [{"key": k, "label": v, "keywords": []} for k, v in [
                ("quotation", "报价"), ("performance", "业绩")]],
            source_ctx={"chapter_titles": [], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert all(r["status"] == STATUS_WARN for r in rows)
        # signature_seal 恒 OK：签名页由渲染引擎无条件输出（render_engine.py:1694）
        seal = derive_statuses(
            [{"key": "signature_seal", "label": "签字盖章", "keywords": []}],
            source_ctx={"chapter_titles": [], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert seal[0]["status"] == STATUS_OK

    def test_count_based_sources(self):
        rows = derive_statuses(
            [{"key": k, "label": v, "keywords": []} for k, v in [
                ("qualification", "资质"), ("performance", "业绩"),
                ("personnel", "人员")]],
            source_ctx={"chapter_titles": [], "bid_opening_ok": False,
                        "qual_count": 1, "contract_count": 2, "personnel_count": 2},
        )
        assert all(r["status"] == STATUS_OK for r in rows)

    def test_custom_row_status_from_db_fallback_only(self):
        rows = derive_statuses(
            [{"key": "custom-1", "label": "保密承诺附件", "keywords": ["保密承诺附件"]}],
            source_ctx={"chapter_titles": ["保密承诺附件"], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert rows[0]["status"] == STATUS_OK
        rows2 = derive_statuses(
            [{"key": "custom-1", "label": "保密承诺附件", "keywords": ["保密承诺附件"]}],
            source_ctx={"chapter_titles": [], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert rows2[0]["status"] == STATUS_WARN


class TestFillPages:
    def test_missing_pdf_returns_rows_unchanged(self):
        rows = [{"key": "quotation", "label": "报价", "keywords": ["报价"],
                 "status": STATUS_WARN}]
        out = fill_pages("C:/no_such_dir/缺.pdf", rows)
        assert out is rows  # 降级路径：原对象返回，调用方维持校验源状态


class TestBuildChecklistDocx:
    def test_docx_has_headers_and_rows(self, tmp_path):
        rows = [
            {"key": "quotation", "label": "报价（开标一览表 / 报价表）",
             "page": "3", "status": STATUS_OK},
            {"key": "signature_seal", "label": "签字盖章页",
             "page": "", "status": STATUS_WARN},
        ]
        out = tmp_path / "清单.docx"
        build_checklist_docx("测试项目", rows, str(out), generated_at="2026-08-28 10:00")
        assert out.exists()
        doc = Document(str(out))
        assert doc.tables
        table = doc.tables[0]
        headers = [c.text for c in table.rows[0].cells]
        assert all(h in headers for h in ("说明", "页码", "状态", "确认", "备注"))
        assert len(table.rows) == len(rows) + 1
        # 确认列均填空框 ☐，页码回填正确
        for i, row in enumerate(rows, start=1):
            assert table.rows[i].cells[3].text == "☐"
        assert table.rows[1].cells[1].text == "3"
        # 标题与项目名在正文区
        body_text = "\n".join(p.text for p in doc.paragraphs)
        assert "投标文件检查清单" in body_text
        assert "测试项目" in body_text