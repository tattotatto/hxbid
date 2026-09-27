"""宏曦标书 - 招标全文语料持久化 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.tender_corpus import load_full_text, persist_full_text


class TestPersistAndLoad:
    def test_roundtrip(self, tmp_path):
        rel = persist_full_text(str(tmp_path), "ab12", "招标全文内容")
        assert rel == "parsed/ab12.txt"
        assert load_full_text({"full_text_path": rel}, str(tmp_path)) == "招标全文内容"

    def test_persist_empty_text_returns_none(self, tmp_path):
        assert persist_full_text(str(tmp_path), "ab12", "") is None

    def test_missing_path_returns_none(self, tmp_path):
        assert load_full_text({}, str(tmp_path)) is None
        assert load_full_text({"full_text_path": "parsed/nope.txt"}, str(tmp_path)) is None

    def test_path_escape_is_refused(self, tmp_path):
        """语料路径不可越出 UPLOAD_DIR。"""
        out = load_full_text({"full_text_path": "../../etc/passwd"}, str(tmp_path))
        assert out is None

    def test_persist_failure_does_not_raise(self, tmp_path):
        """落盘失败只该返回 None，不能把上传解析整个搞崩。"""
        assert persist_full_text(str(tmp_path / "nope" / "\0bad"), "x", "内容") is None
