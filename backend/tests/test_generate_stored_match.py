"""宏曦标书 - 生成阶段使用已固化匹配区间 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json

from app.services.ai_pipeline import _resolve_section_text
from app.services.tender_section_matcher import corpus_hash

CORPUS = "一、投标函\n\n致：某某单位\n我方已仔细阅读。\n\n二、开标一览表\n\n序号 | 服务内容\n"


class _Chapter:
    def __init__(self, title, meta):
        self.title = title
        self.chapter_meta_json = json.dumps(meta)


class TestResolveSectionText:
    def test_uses_stored_offsets_verbatim(self):
        """存了区间就直接切片——这是"所见即所得"的核心。"""
        start = CORPUS.index("二、开标一览表")
        ch = _Chapter("开标一览表", {"match": {
            "status": "matched", "start": start, "end": len(CORPUS),
            "corpus_hash": corpus_hash(CORPUS),
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text == CORPUS[start:]
        assert warnings == []

    def test_stale_hash_triggers_relookup_and_warns(self):
        """场景：原文变了（重新解析过），存的位置不可信。"""
        ch = _Chapter("投标函", {"match": {
            "status": "matched", "start": 0, "end": 5,
            "corpus_hash": "sha1:stale",
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text is not None
        assert text.startswith("一、投标函")
        assert any("原文位置已变化" in w for w in warnings)

    def test_stale_hash_and_no_relookup_reports_error(self):
        ch = _Chapter("不存在的小节", {"match": {
            "status": "matched", "start": 0, "end": 5, "corpus_hash": "sha1:stale",
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text is None
        assert any("未匹配到招标原文" in w for w in warnings)

    def test_no_match_meta_returns_none_silently(self):
        """老项目没有 match 字段 → 交给老的按标题定位路径，不算警告。"""
        ch = _Chapter("投标函", {})
        text, warnings = _resolve_section_text(
            ch, {}, {"format_section_text": CORPUS}, None,
        )
        assert text is None
        assert warnings == []

    def test_out_of_range_offsets_are_treated_as_stale(self):
        ch = _Chapter("投标函", {"match": {
            "status": "matched", "start": 0, "end": 999999,
            "corpus_hash": corpus_hash(CORPUS),
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text is not None
        assert warnings, "越界区间必须触发重匹配并告警"

    def test_full_text_source_is_sliced_from_full_text(self):
        """source=full_text 时区间是针对全文语料的，不能拿格式章节去切。"""
        full = "另一份全文语料"
        ch = _Chapter("服务方案", {"match": {
            "status": "matched", "source": "full_text", "start": 0, "end": len(full),
            "corpus_hash": corpus_hash(full),
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, full,
        )
        assert text == full
        assert warnings == []

    def test_missing_status_returns_none_silently(self):
        ch = _Chapter("投标函", {"match": {"status": "missing"}})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text is None
        assert warnings == []
