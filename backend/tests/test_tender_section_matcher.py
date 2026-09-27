"""宏曦标书 - 招标原文小节匹配器 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.tender_section_matcher import (
    MatchCandidate,
    classify,
    enumerate_headers,
    normalize_title,
    score_title,
    section_body,
)

# 一份刻意混入目录条目、子级标题与同名正文的语料
SAMPLE = """目录
一、投标函 ................................ -32-
二、开标一览表 ............................ -33-

一、投标函

致：某某单位
我方已仔细阅读并充分理解贵方招标文件的全部内容。

投标人：（公章）
日期：  年  月  日

（一）投标函附录

附录内容一
附录内容二

二、开标一览表

序号 | 服务内容 | 报价
"""


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


class TestScoreTitle:
    def test_exact_is_one(self):
        assert score_title("投标函", "投标函") == 1.0

    def test_substring_is_scaled(self):
        # 「投标函」是「投标函附录」的子串 → 0.8 × 短/长
        s = score_title("投标函", "投标函附录")
        assert 0.4 < s < 0.8

    def test_unrelated_is_below_threshold(self):
        assert score_title("投标函", "应急预案") < 0.75

    def test_empty_inputs(self):
        assert score_title("", "投标函") == 0.0
        assert score_title("投标函", "") == 0.0


class TestSectionBody:
    def test_sub_level_heading_does_not_truncate(self):
        """关键修正：`（一）投标函附录` 是子级，不得截断 `一、投标函` 的正文。"""
        headers = enumerate_headers(SAMPLE)
        idx = next(i for i, h in enumerate(headers)
                   if h.title == "投标函" and "致：" in section_body(SAMPLE, headers, i))
        body = section_body(SAMPLE, headers, idx)
        assert "日期：  年  月  日" in body
        assert "（一）投标函附录" in body, "子级标题应当留在父级正文里"
        assert "二、开标一览表" not in body, "同级标题必须截断"

    def test_next_same_level_heading_truncates(self):
        headers = enumerate_headers(SAMPLE)
        idx = next(i for i, h in enumerate(headers)
                   if h.title == "开标一览表" and "序号" in section_body(SAMPLE, headers, i))
        body = section_body(SAMPLE, headers, idx)
        assert "序号" in body

    def test_last_section_runs_to_end(self):
        headers = enumerate_headers(SAMPLE)
        body = section_body(SAMPLE, headers, len(headers) - 1)
        assert body.strip().endswith("报价")


class TestClassify:
    def _c(self, score):
        return MatchCandidate(title="t", raw="t", level=1, start=0, end=1, score=score)

    def test_clear_winner_is_matched(self):
        # 与次高分拉开 0.5 ≥ AMBIGUITY_GAP —— 这是确定的赢家
        assert classify([self._c(1.0), self._c(0.5)]) == "matched"
        assert classify([self._c(1.0)]) == "matched"

    def test_close_scores_are_ambiguous(self):
        assert classify([self._c(0.95), self._c(0.9)]) == "ambiguous"

    def test_two_exact_matches_are_ambiguous(self):
        """同名小节出现两次（正文 + 附录）→ 必须让用户挑，不能随便取一个。"""
        assert classify([self._c(1.0), self._c(1.0)]) == "ambiguous"
