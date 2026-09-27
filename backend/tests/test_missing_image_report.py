"""宏曦标书 - 缺失图片必须被报出来，而不是渲染成空白页 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.materials_injection import collect_missing_image_markers


class TestCollectMissingImageMarkers:
    def test_missing_file_is_reported_by_label(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        chapters = [{"title": "投标保证金凭证", "content": "[IMG:gone.png|保证金缴纳凭证]"}]
        assert collect_missing_image_markers(chapters) == ["保证金缴纳凭证"]

    def test_existing_file_is_not_reported(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        (tmp_path / "ok.png").write_bytes(b"x")
        chapters = [{"title": "附件", "content": "[IMG:ok.png|营业执照]"}]
        assert collect_missing_image_markers(chapters) == []

    def test_label_defaults_to_path_when_absent(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        chapters = [{"title": "附件", "content": "[IMG:gone.png]"}]
        assert collect_missing_image_markers(chapters) == ["gone.png"]

    def test_idpair_front_and_back_are_checked(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        (tmp_path / "front.png").write_bytes(b"x")
        chapters = [{
            "title": "法人身份证",
            "content": "[IDPAIR:front.png|正面|back.png|反面]",
        }]
        assert collect_missing_image_markers(chapters) == ["反面"]

    def test_no_markers_returns_empty(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        assert collect_missing_image_markers([{"title": "x", "content": "普通正文"}]) == []

    def test_children_are_scanned(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        chapters = [{"title": "章", "content": "", "children": [
            {"title": "节", "content": "[IMG:gone.png|子节的图]"}]}]
        assert collect_missing_image_markers(chapters) == ["子节的图"]

    def test_duplicates_are_reported_once(self, tmp_path, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        chapters = [
            {"title": "A", "content": "[IMG:gone.png|同一张]"},
            {"title": "B", "content": "[IMG:gone.png|同一张]"},
        ]
        assert collect_missing_image_markers(chapters) == ["同一张"]
