"""宏曦标书 - 招标原文小节匹配器 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.tender_section_matcher import (
    MatchCandidate,
    classify,
    corpus_hash,
    enumerate_headers,
    match_tender_section,
    normalize_title,
    page_at,
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


FORMAT_SECTION = """一、投标函

致：某某单位
我方已仔细阅读。

二、开标一览表

序号 | 服务内容 | 报价
"""


class TestPageAt:
    def test_resolves_offset_to_page(self):
        pmap = [{"start": 0, "page": 32}, {"start": 100, "page": 33}]
        assert page_at(pmap, 0) == 32
        assert page_at(pmap, 99) == 32
        assert page_at(pmap, 100) == 33
        assert page_at(pmap, 500) == 33

    def test_no_map_returns_none(self):
        assert page_at(None, 10) is None
        assert page_at([], 10) is None


class TestCorpusHash:
    def test_stable_and_prefixed(self):
        h = corpus_hash("abc")
        assert h.startswith("sha1:")
        assert h == corpus_hash("abc")

    def test_changes_with_content(self):
        assert corpus_hash("abc") != corpus_hash("abd")

    def test_empty_text_still_hashes(self):
        assert corpus_hash("") == corpus_hash("")


class TestMatchTenderSection:
    def test_exact_match_returns_offsets_and_hash(self):
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "matched"
        assert res.source == "format_section"
        assert res.best is not None
        assert FORMAT_SECTION[res.best.start:res.best.end].startswith("一、投标函")
        assert "我方已仔细阅读" in FORMAT_SECTION[res.best.start:res.best.end]
        assert res.corpus_hash == corpus_hash(FORMAT_SECTION)

    def test_ai_generated_is_not_applicable(self):
        res = match_tender_section(
            "服务方案", chapter_type="ai_generated",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "na"
        assert res.best is None

    def test_missing_when_title_absent(self):
        res = match_tender_section(
            "根本不存在的一节", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "missing"
        assert res.best is None

    def test_missing_when_corpus_absent(self):
        """场景：招标文件解析不出格式章节（扫描件 PDF）。"""
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text=None, full_text=None,
        )
        assert res.status == "missing"
        assert res.source is None

    def test_empty_title_is_missing(self):
        res = match_tender_section(
            "   ", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "missing"

    def test_falls_back_to_full_text(self):
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text="", full_text=FORMAT_SECTION,
        )
        assert res.status == "matched"
        assert res.source == "full_text"

    def test_prefers_format_section_over_full_text(self):
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION, full_text="一、投标函\n别的正文",
        )
        assert res.source == "format_section"

    def test_skips_toc_entry_and_takes_real_section(self):
        """场景：同名标题在目录和正文各出现一次，必须取正文那份。"""
        corpus = (
            "目录\n"
            "一、投标函 ...................... -32-\n"
            "一、投标函\n\n致：某某单位\n"
        )
        res = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=corpus,
        )
        assert res.status == "matched"
        assert "致：某某单位" in corpus[res.best.start:res.best.end]

    def test_duplicate_real_sections_are_ambiguous(self):
        """场景：正文与附录有同名小节 → 交给用户挑，不替他决定。"""
        corpus = "一、投标函\n\n正文甲\n\n一、投标函\n\n正文乙\n"
        res = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=corpus,
        )
        assert res.status == "ambiguous"
        assert len(res.candidates) == 2
        assert all(c.score == 1.0 for c in res.candidates)

    def test_page_resolved_from_page_map(self):
        pmap = [{"start": 0, "page": 30}]
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION, format_page_map=pmap,
        )
        assert res.best.page == 30

    def test_word_pasted_title_still_matches(self):
        """场景：用户从 Word 粘贴，带全角空格与手写编号。"""
        res = match_tender_section(
            "　一、投标函　", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "matched"


class TestTableMatching:
    TABLES = [
        {"page": 33, "table_index": 0,
         "rows": [["序号", "服务内容", "报价"], ["1", "安保", ""]]},
    ]

    def test_table_chapter_picks_table_by_page_proximity(self):
        pmap = [{"start": 0, "page": 32}, {"start": 40, "page": 33}]
        res = match_tender_section(
            "开标一览表", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=self.TABLES,
            format_page_map=pmap,
        )
        assert res.status == "matched"
        assert res.source == "table"
        assert res.table_index == 0
        assert res.best is not None, "文字也命中了「二、开标一览表」"

    def test_table_chapter_matches_even_when_caption_absent(self):
        """表格章节的内容就是那张表，标题文案对不上小节也要按表头把表选出来。"""
        res = match_tender_section(
            "序号服务内容报价", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=self.TABLES,
        )
        assert res.status == "matched"
        assert res.source == "table"
        assert res.table_index == 0
        assert res.best is None

    def test_table_chapter_without_tables_degrades_to_text_result(self):
        """一张表都没有 → 退回普通文本匹配的结果，而不是硬报 matched。"""
        res = match_tender_section(
            "开标一览表", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=[],
        )
        assert res.table_index is None
        assert res.source == "format_section"
        assert res.status == "matched"

    def test_table_header_scoring_picks_best_of_several(self):
        tables = [
            {"page": 10, "table_index": 0,
             "rows": [["序号", "姓名", "职称"], ["1", "", ""]]},
            {"page": 11, "table_index": 0,
             "rows": [["序号", "服务内容", "报价"], ["1", "", ""]]},
        ]
        res = match_tender_section(
            "序号服务内容报价", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=tables,
        )
        assert res.table_index == 1

    def test_pages_are_preferred_over_header_score(self):
        """有页码时以页码就近为准：表紧跟在小节标题之后。"""
        tables = [
            {"page": 31, "table_index": 0,
             "rows": [["序号", "服务内容", "报价"], ["1", "", ""]]},
            {"page": 40, "table_index": 0,
             "rows": [["序号", "服务内容", "报价"], ["1", "", ""]]},
        ]
        pmap = [{"start": 0, "page": 32}]
        res = match_tender_section(
            "开标一览表", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=tables,
            format_page_map=pmap,
        )
        assert res.table_index == 1, "第 31 页在小节之前，应取其后的第 40 页那张"


class TestTocEntryWithPageOnItsOwnLine:
    """目录条目：标题独占一行、页码另起一行 —— 这是 PDF 目录最常见的形态.

    旧的覆盖测试用的是「一、投标函 ...... -32-」（页码与标题同行），那种行
    `score_title` 算出来 ~0.06 直接被阈值滤掉，走的是**打分**过滤。而真正为
    这种形态准备的 `has_body` / 页码剥离路径（标题后只剩一个页码行）此前
    **一条测试都没有**。
    """

    CORPUS = (
        "一、投标函\n"
        "-32-\n"
        "\n"
        "一、投标函\n"
        "\n"
        "致：某某单位\n"
        "我方已仔细阅读。\n"
    )

    def test_page_only_body_entry_is_skipped(self):
        res = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=self.CORPUS,
        )
        assert res.status == "matched"
        assert "致：某某单位" in self.CORPUS[res.best.start:res.best.end]
        assert res.best.start > 0, "必须取正文那份，不能取页码那一条"

    def test_bare_page_number_line_counts_as_no_body(self):
        """直接钉住 has_body：标题后面只有一个页码行 → 不算有正文。"""
        from app.services.tender_section_matcher import enumerate_headers, has_body

        headers = enumerate_headers(self.CORPUS)
        toc = next(i for i, h in enumerate(headers) if h.start == 0)
        real = next(i for i, h in enumerate(headers) if h.start > 0)
        assert has_body(self.CORPUS, headers, toc) is False
        assert has_body(self.CORPUS, headers, real) is True

    def test_decorated_page_number_after_blank_lines_is_still_no_body(self):
        """带装饰的页码（破折号/全角空格）也要被认出来。"""
        from app.services.tender_section_matcher import enumerate_headers, has_body

        corpus = "十九、附件\n\n\n— 73 —\n\n一、别的\n\n正文\n"
        headers = enumerate_headers(corpus)
        assert has_body(corpus, headers, 0) is False


class TestWeakCandidatesAreStillOffered:
    """真实招标文件暴露的问题：用户的标题是真实小节名的**前缀**时，候选被滤空了.

    实测：红云红河的「投标保证金」对真实标题「（五）投标保证金及基本户凭证」
    打分 0.8×5/13 ≈ 0.31，低于 MATCH_THRESHOLD，于是 status=missing 且
    **候选列表为空** —— 抽屉里写着「建议改标题，或从下方候选中选一个」，
    而下方空无一物，用户无路可走，只能拿到 AI 撰写的文本。
    对一个明确规定了格式的章节，这就是废标风险。

    定档仍要保守（不许把弱命中当 matched），但候选必须给出来让用户挑。
    """

    CORPUS = "（五）投标保证金及基本户凭证\n\n致：某某单位\n正文甲\n"

    def test_partial_title_offers_candidates_but_is_not_matched(self):
        res = match_tender_section(
            "投标保证金", chapter_type="fixed_form", format_section_text=self.CORPUS,
        )
        assert res.status == "missing", "弱命中不许擅自当成 matched"
        assert res.best is None
        assert res.candidates, "但必须有候选，否则用户无路可走"
        assert "投标保证金及基本户凭证" in res.candidates[0].title

    def test_user_picked_weak_candidate_is_usable(self):
        """用户从弱候选中点选后，那条区间必须真的能切片。"""
        res = match_tender_section(
            "投标保证金", chapter_type="fixed_form", format_section_text=self.CORPUS,
        )
        c = res.candidates[0]
        assert self.CORPUS[c.start:c.end].startswith("（五）投标保证金及基本户凭证")
        assert "正文甲" in self.CORPUS[c.start:c.end]

    def test_unrelated_title_gets_no_candidates(self):
        """无关的标题不该硬凑候选。"""
        res = match_tender_section(
            "应急预案", chapter_type="fixed_form", format_section_text=self.CORPUS,
        )
        assert res.status == "missing"
        assert res.candidates == []

    def test_empty_corpus_still_has_no_candidates(self):
        res = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=None,
        )
        assert res.candidates == []
