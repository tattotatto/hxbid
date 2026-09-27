"""宏曦标书 - 附件类章节生成 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json

from app.services.ai_pipeline import _generate_attachment_chapter


class _Chapter:
    def __init__(self, title, meta):
        self.title = title
        self.chapter_meta_json = json.dumps(meta)


def test_emits_img_markers_in_order():
    ch = _Chapter("投标保证金凭证", {"attachments": [
        {"kind": "upload", "id": "u1", "label": "保证金缴纳凭证", "path": "project_p1/a.png"},
        {"kind": "qualification", "id": "q1", "label": "基本账户开户许可证", "path": "ocr/b.png"},
    ]})
    meta = json.loads(ch.chapter_meta_json)
    content, warnings = _generate_attachment_chapter(ch, meta)
    assert content.splitlines() == [
        "[IMG:project_p1/a.png|保证金缴纳凭证]",
        "[IMG:ocr/b.png|基本账户开户许可证]",
    ]
    assert warnings == []


def test_empty_attachments_keeps_placeholder_and_warns():
    """场景：用户选了附件类型但一个都没挂。"""
    ch = _Chapter("其他材料", {})
    content, warnings = _generate_attachment_chapter(ch, {})
    assert content == ""
    assert any("未挂载任何附件" in w for w in warnings)


def test_label_defaults_to_filename_when_absent():
    ch = _Chapter("附件", {"attachments": [{"kind": "upload", "id": "u1", "path": "p/a.png"}]})
    content, _ = _generate_attachment_chapter(ch, json.loads(ch.chapter_meta_json))
    assert content == "[IMG:p/a.png|a.png]"


def test_attachment_without_path_is_skipped_and_warned():
    ch = _Chapter("附件", {"attachments": [
        {"kind": "upload", "id": "u1", "label": "坏记录"},
        {"kind": "upload", "id": "u2", "label": "好的", "path": "p/a.png"},
    ]})
    content, warnings = _generate_attachment_chapter(ch, json.loads(ch.chapter_meta_json))
    assert "坏记录" not in content
    assert any("坏记录" in w for w in warnings)
    assert content == "[IMG:p/a.png|好的]"


def test_none_attachment_entry_does_not_crash():
    """旧项目里可能存着 null 项，不能让它把整章生成搞崩。"""
    ch = _Chapter("附件", {"attachments": [None]})
    content, warnings = _generate_attachment_chapter(ch, json.loads(ch.chapter_meta_json))
    assert content == ""
    assert warnings
