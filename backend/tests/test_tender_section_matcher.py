"""宏曦标书 - 招标原文小节匹配器 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.tender_section_matcher import (
    enumerate_headers,
    normalize_title,
)


class TestNormalizeTitle:
    def test_strips_numbering_prefix(self):
        assert normalize_title("二、投标函") == "投标函"

    def test_strips_parenthesised_numbering(self):
        assert normalize_title("（一）投标函") == "投标函"

    def test_strips_multilevel_numbering(self):
        assert normalize_title("1.1 项目概况") == "项目概况"

    def test_fullwidth_and_whitespace(self):
        # 用户从 Word 粘贴：全角空格 + 全角括号
        assert normalize_title("　（一）投标函　") == "投标函"

    def test_plain_title_untouched(self):
        assert normalize_title("投标函") == "投标函"

    def test_empty(self):
        assert normalize_title("") == ""


class TestEnumerateHeaders:
    def test_all_numbering_styles_get_expected_levels(self):
        text = "\n".join([
            "第一节 投标文件格式",      # level 1
            "一、投标函",               # level 1
            "（一）投标函附录",          # level 2
            "(二)开标一览表",           # level 2
            "1、分项报价表",            # level 3
            "1.1 分项明细",             # level 4
        ])
        headers = enumerate_headers(text)
        got = [(h.title, h.level) for h in headers]
        assert got == [
            ("投标文件格式", 1),
            ("投标函", 1),
            ("投标函附录", 2),
            ("开标一览表", 2),
            ("分项报价表", 3),
            ("分项明细", 4),
        ]

    def test_multilevel_numbering_wins_over_single_level(self):
        # 「1.1 项目概况」同时匹配 level-4 与 level-3 两条正则，
        # 必须先判 4（更具体），否则层级会被误判成 3。
        headers = enumerate_headers("1.1 项目概况")
        assert len(headers) == 1
        assert headers[0].level == 4
        assert headers[0].title == "项目概况"

    def test_start_offsets_are_real(self):
        text = "一、投标函\n正文\n二、承诺书\n正文2"
        headers = enumerate_headers(text)
        assert [h.start for h in headers] == [0, text.index("二、承诺书")]
        assert text[headers[1].start:].startswith("二、承诺书")

    def test_empty_text_returns_empty(self):
        assert enumerate_headers("") == []
        assert enumerate_headers(None) == []

    def test_long_line_is_not_a_header(self):
        # 标题行文字上限 80 字符 —— 超长的是正文，不是标题
        long_body = "一、" + "字" * 90
        assert enumerate_headers(long_body) == []
