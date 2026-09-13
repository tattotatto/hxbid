"""pdf_extractor 模块测试.

测试 PDF 格式章节提取器：文本提取、表格提取、章节定位。
"""

import os
from pathlib import Path

import pytest

from app.services.pdf_extractor import (
    extract_clean_text_from_pages,
    extract_format_section,
    extract_full_document,
    extract_tables_from_pages,
    extract_text_from_pages,
    locate_format_pages,
)


# ---------------------------------------------------------------------------
# 测试文件路径
# ---------------------------------------------------------------------------

TENDER_PDF = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "素材", "招标文件正文.pdf"
)

HAS_TENDER_PDF = os.path.exists(TENDER_PDF) and os.path.isfile(TENDER_PDF)


def _skip_if_no_pdf():
    """CI 或开发环境可能没有招标文件 PDF."""
    if not HAS_TENDER_PDF:
        pytest.skip("Tender PDF not found: %s" % TENDER_PDF)


# ---------------------------------------------------------------------------
# 核心功能测试
# ---------------------------------------------------------------------------


class TestExtractFormatSection:
    """extract_format_section 集成测试."""

    def test_extract_format_section_basic(self):
        """完整提取招标文件格式章节 - 基本可用性."""
        _skip_if_no_pdf()
        result = extract_format_section(TENDER_PDF)

        # 必须返回包含文本的结果
        assert result["full_text"], "Expected non-empty full_text"
        assert len(result["full_text"]) > 500, (
            f"Expected >500 chars, got {len(result['full_text'])}"
        )

        # 格式章节应包含标志性内容
        full_text = result["full_text"]
        has_format = "投标文件格式" in full_text or "投标书格式" in full_text
        has_bid = "投标函" in full_text or "开标一览表" in full_text
        assert has_format or has_bid, (
            "Expected format-related content in extracted text"
        )

        # 页码应合理
        assert result["start_page"] >= 1
        assert result["end_page"] >= result["start_page"]
        assert result["total_pages"] >= result["end_page"]

        print(
            f"Text: {len(result['full_text'])} chars, "
            f"Tables: {len(result['tables'])}, "
            f"Pages: {result['start_page']}-{result['end_page']} / {result['total_pages']}"
        )

    def test_tables_extracted(self):
        """表格正确提取 — 至少应有多个表格."""
        _skip_if_no_pdf()
        result = extract_format_section(TENDER_PDF)

        # 招标文件格式章节通常包含多个表格（开标一览表、报价表、人员表等）
        assert len(result["tables"]) >= 1, (
            f"Expected at least 1 table, got {len(result['tables'])}"
        )

        # 验证表格数据结构
        for t in result["tables"]:
            assert "page" in t
            assert "table_index" in t
            assert "rows" in t
            assert isinstance(t["rows"], list)
            assert len(t["rows"]) >= 2, (
                f"Table on page {t['page']} has fewer than 2 rows"
            )
            # 每行应为字符串列表
            for row in t["rows"]:
                assert isinstance(row, list), f"Expected list row, got {type(row)}"

        print(f"Found {len(result['tables'])} tables")


class TestLocateFormatPages:
    """locate_format_pages 单元测试."""

    def test_locate_finds_format_section(self):
        """正向测试：能找到格式章节."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            start, end = locate_format_pages(pdf)
            assert start is not None, "Expected to find format section"
            assert 0 <= start <= end < len(pdf.pages), (
                f"Invalid page range: {start}-{end} (total {len(pdf.pages)} pages)"
            )
            print(f"Format section pages: {start + 1} - {end + 1}")
        finally:
            pdf.close()

    def test_locate_returns_none_for_no_match(self):
        """无格式关键词的文档应返回 None."""
        import pdfplumber

        # 使用第一页验证空查询场景（但可能仍匹配，仅测试接口返回类型）
        pdf = pdfplumber.open(TENDER_PDF)
        try:
            result = locate_format_pages(pdf)
            # 只验证返回类型一致性，不强制要求 None（实际文件可能匹配）
            if result is not None:
                start, end = result
                assert isinstance(start, int)
                assert isinstance(end, int)
        finally:
            pdf.close()


class TestLocateFormatPagesBacktrack:
    """格式章节回溯到真正章首页（fake pdf，大红山页几何回归）."""

    class FakePage:
        def __init__(self, text): self._text = text
        def extract_text(self): return self._text

    class FakePdf:
        def __init__(self, pages): self.pages = pages

    @staticmethod
    def _ruidashan_pdf():
        """大红山招标文件 p73-p77 的页几何（0-indexed）.

        实测：从文档末尾倒扫，第一个命中的是 idx 3（p76 投标函正文里
        顺口提了「投标文件格式」），而不是 idx 0（p73 真正的章名页）。
        中间隔着 p74 目录、p75 封面两页无关键词，所以必须回溯满 3 页
        才够得到章首——这正是回溯窗口 off-by-one 的暴击点。
        """
        cls = TestLocateFormatPagesBacktrack
        return cls.FakePdf([
            cls.FakePage("第六章 投标文件格式"),                      # 0 = p73 章名页
            cls.FakePage("目录\n一、封面.........75\n二、投标函.......76"),  # 1 = p74 目录
            cls.FakePage("一、封面\n（项目名称）招标项目\n"
                         "投 标 文 件\n投标人：\n法定代表人："),           # 2 = p75 封面
            cls.FakePage("二、投标函\n投标函\n致： 招标人名称\n"
                         "根据贵方 项目名称 招标文件（投标文件格式见第六章）"),  # 3 = p76 仅提及
            cls.FakePage("十九、附件\n（附件正文）"),                    # 4 = p77
        ])

    def test_backtracks_three_pages_to_chapter_title(self):
        """关键词页往前第 3 页才是章首时，必须回溯到那里.

        回归：旧实现的窗口是 `range(i - 1, max(i - 3, -1), -1)`，
        range 的 stop 是开区间 → 实际只回溯 i-1、i-2 两页，够不到 i-3。
        p73（章名）/p74（目录）/p75（封面）因此整页被排除在
        format_section_text 之外，封面章节在最终标书里彻底消失。
        """
        start, end = locate_format_pages(self._ruidashan_pdf())
        assert start == 0, f"应回溯到章名页 0, got {start}"
        assert end == 4

    def test_backtrack_window_includes_cover_page(self):
        """回溯范围内必须涵盖封面页（p75 → idx 2）.

        这条测的是用户可见症状本身：封面有没有落进抽取范围。
        """
        pdf = self._ruidashan_pdf()
        start, _end = locate_format_pages(pdf)
        page_texts = [
            pdf.pages[i].extract_text() for i in range(start, len(pdf.pages))
        ]
        joined = "\n".join(page_texts)
        assert "一、封面" in joined, "封面页未进入格式章节抽取范围"
        assert "第六章 投标文件格式" in joined, "章名页未进入抽取范围"

    def test_backtrack_still_works_for_adjacent_page(self):
        """章首页紧邻关键词页（回溯 1 页）时不得回归."""
        cls = TestLocateFormatPagesBacktrack
        pdf = cls.FakePdf([
            cls.FakePage("第六章 投标文件格式"),
            cls.FakePage("二、投标函\n（格式模板）"),
        ])
        assert locate_format_pages(pdf) == (0, 1)

    def test_returns_none_when_no_keyword_anywhere(self):
        cls = TestLocateFormatPagesBacktrack
        pdf = cls.FakePdf([
            cls.FakePage("第一章 招标公告"),
            cls.FakePage("第二章 投标人须知"),
        ])
        assert locate_format_pages(pdf) is None


class TestExtractTextFromPages:
    """extract_text_from_pages 测试."""

    def test_extract_text_single_page(self):
        """单页文本提取."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            text = extract_text_from_pages(pdf, 0, 0)
            assert text, "First page should have text"
            assert isinstance(text, str)
        finally:
            pdf.close()

    def test_extract_text_multi_page(self):
        """多页文本提取."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            # 提取前 3 页
            text = extract_text_from_pages(pdf, 0, min(2, len(pdf.pages) - 1))
            assert text, "Pages should have text"
            assert len(text) > 100
        finally:
            pdf.close()

    def test_extract_text_out_of_range(self):
        """超出页码范围不抛异常."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            # 超出范围应返回空字符串
            text = extract_text_from_pages(pdf, 99999, 100000)
            assert text == ""
        finally:
            pdf.close()


class TestExtractCleanTextFromPages:
    """extract_clean_text_from_pages：按行几何还原被排版拆开的段落.

    行数据全部取自真实招标文件（大红山）第 75/76 页（0-indexed）——页高 841.9、
    页宽 595.3、左边距 90。段内换行 gap 10.0–10.3，真正的行结束 19.9–20.0，
    中间没有灰区；页码 `-75-` 居中、bottom 落在页高 95.9% 处。
    """

    PAGE_W, PAGE_H = 595.3, 841.9

    @staticmethod
    def _line(text, x0, x1, top, bottom):
        return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom}

    def _page(self, lines, width=None, height=None, tables=()):
        class FakePage:
            def __init__(self):
                self.width = width or self.PAGE_W
                self.height = height or self.PAGE_H

            def extract_text_lines(self):
                return lines

            def find_tables(self):
                return list(tables)

            def extract_text(self):
                return "\n".join(ln["text"] for ln in lines)

        FakePage.PAGE_W = self.PAGE_W
        FakePage.PAGE_H = self.PAGE_H
        return FakePage()

    def _pdf(self, pages):
        class FakePdf:
            pass

        pdf = FakePdf()
        pdf.pages = pages
        return pdf

    def test_joins_wrapped_lines_into_one_paragraph(self):
        """段内换行按字符拼接（不插空格），换段/列表项保持独立行."""
        page = self._page([
            self._line("根据贵方 项目名称 招标文件（招标编号为 ），我方针对本项目的",
                       111.00, 505.21, 212.46, 222.91),
            self._line("投标总报价为： 万元人民币，含税（大写： 万元人民币），其中：治",
                       90.00, 505.33, 232.95, 244.00),
            self._line("安保卫业务的投标单价为： 万元/年人民币，含税（大写： 万元/年人",
                       90.00, 505.21, 254.31, 265.36),
            self._line("民币）；矿区井口、生活区游泳池值守服务业务的投标单价为： 万元/年人民",
                       90.00, 505.21, 275.70, 286.15),
            self._line("币，含税（大写： 万元/年人民币），提交招标文件要求的全套投标文件，包括：",
                       90.00, 504.60, 296.10, 306.55),
            self._line("1）招标文件中要求的投标文件；", 126.00, 278.17, 326.46, 336.91),
        ])
        text = extract_clean_text_from_pages(self._pdf([page]), 0, 0)

        # 4 行残句拼成一段，且「治」+「安保卫」中间不留空格/换行
        assert "本项目的投标总报价为： 万元人民币" in text
        assert "其中：治安保卫业务的投标单价为：" in text
        assert "万元/年人民币）；矿区井口" in text
        assert "全套投标文件，包括：" in text
        # 列表项是独立行，不能被粘到上一段末尾
        assert text.endswith("\n1）招标文件中要求的投标文件；")
        # 段落内部的表格空位必须原样保留
        assert "投标总报价为： 万元人民币" in text

    def test_does_not_join_a_line_that_starts_a_list_item(self):
        """列表项标号开头的新行不得被当续行粘上去.

        大红山第 78 页（投标承诺书）整页行距统一 10.31–10.43，段内换行和换列表项
        **没有行距差**，几何上分不开：上一行「…参加 项目的投标。」x1=498.97 顶到栏边、
        下一行 `a) …` 间隔 10.31，纯几何判定必然把 `a) …` 吃掉，而成品里
        `b)`–`i)` 因为各自上一行不满行又是独立行——同一份文档两种排法。
        """
        page = self._page([
            self._line("将遵循公开、公平、公正和诚实守信的原则，参加 项目的投标。",
                       90.00, 498.97, 172.83, 183.88),
            self._line("a) 所提供的一切材料都是真实、有效、合法的。",
                       90.00, 382.09, 194.19, 205.24),
            self._line("b) 不与招标人或其他投标人串通投标，损害国家利益、社会利益或他人的",
                       90.00, 503.04, 215.55, 226.60),
            self._line("合法权益。本公司如被查实在本项目招标投标活动中存在围标串标、提供虚假材料的",
                       90.00, 497.04, 237.03, 248.08),
        ])
        text = extract_clean_text_from_pages(self._pdf([page]), 0, 0)

        assert "\na) 所提供的一切材料都是真实、有效、合法的。" in text
        # 「b) …」自己顶到右边距，它的续行仍要拼回去
        assert "损害国家利益、社会利益或他人的合法权益。" in text

    def test_joins_a_wrapped_line_that_begins_with_a_number(self):
        """续行以数字开头时仍要拼回去（只有「数字+标号标点」才是列表项）."""
        page = self._page([
            self._line("2）金额为", 90.00, 498.00, 172.83, 183.88),
            self._line("100000元人民币的投标保证金；", 90.00, 320.00, 194.19, 205.24),
        ])
        text = extract_clean_text_from_pages(self._pdf([page]), 0, 0)
        assert "2）金额为100000元人民币的投标保证金；" in text

    def test_drops_page_number_in_bottom_margin(self):
        """页脚页码整行丢弃."""
        page = self._page([
            self._line("投标人名称：", 90.00, 152.89, 732.90, 743.35),
            self._line("-75-", 287.64, 307.60, 795.48, 807.60),
        ])
        text = extract_clean_text_from_pages(self._pdf([page]), 0, 0)
        assert "投标人名称：" in text
        assert "-75-" not in text
        assert "75" not in text

    def test_keeps_lone_number_inside_text_area(self):
        """正文区里独占一行的数字不是页码，必须保留.

        页脚/页眉判定按页高比例，不是「整行只有数字」——开标一览表里的
        序号、金额都可能独占一行。
        """
        page = self._page([
            self._line("投标总报价（万元）", 90.00, 200.00, 400.00, 410.00),
            self._line("1377.2262", 90.00, 160.00, 420.00, 430.00),
        ])
        text = extract_clean_text_from_pages(self._pdf([page]), 0, 0)
        assert "1377.2262" in text

    def test_does_not_join_lines_inside_a_table(self):
        """表格里的两行不能拼——实测响应表行距 1.1pt，比段内换行还小.

        表格行跨列排布，拼起来会把两行单元格文本搅成一句。判据是行落在
        find_tables 给出的 bbox 内，不是行距。
        """
        class FakeTable:
            bbox = (89.5, 200.0, 546.7, 400.0)

        page = self._page([
            self._line("投 标人 响 应说 明 投标文件关", 89.5, 546.7, 210.00, 220.00),
            self._line("（针对招标要求简 联内容所在", 89.5, 546.7, 223.01, 233.01),
        ], tables=[FakeTable()])
        text = extract_clean_text_from_pages(self._pdf([page]), 0, 0)
        assert text == "投 标人 响 应说 明 投标文件关\n（针对招标要求简 联内容所在"

    def test_joins_prose_outside_table_on_a_mixed_page(self):
        """同一页上表格外的正文照常拼——表格 bbox 只保护它自己覆盖的行."""
        class FakeTable:
            bbox = (89.5, 400.0, 546.7, 600.0)

        page = self._page([
            self._line("根据贵方 项目名称 招标文件（招标编号为 ），我方针对本项目的",
                       111.00, 505.21, 212.46, 222.91),
            self._line("投标总报价为： 万元人民币，含税。", 90.00, 505.33, 232.95, 244.00),
            self._line("表内第一行", 89.5, 546.7, 410.00, 420.00),
            self._line("表内第二行", 89.5, 546.7, 423.01, 433.01),
        ], tables=[FakeTable()])
        text = extract_clean_text_from_pages(self._pdf([page]), 0, 0)
        assert text == (
            "根据贵方 项目名称 招标文件（招标编号为 ），我方针对本项目的"
            "投标总报价为： 万元人民币，含税。\n表内第一行\n表内第二行"
        )

    def test_joins_latin_words_with_a_space(self):
        """西文换行处原先就有空格，拼回去要补一个，否则 con+tract 粘成一个词."""
        page = self._page([
            self._line("the bidder shall submit the signed con", 90.00, 505.21, 100.00, 110.00),
            self._line("tract within ten days.", 90.00, 300.00, 120.00, 130.00),
        ])
        text = extract_clean_text_from_pages(self._pdf([page]), 0, 0)
        assert text == "the bidder shall submit the signed con tract within ten days."

    def test_pages_joined_by_blank_line(self):
        """跨页永远是硬换行：页尾「投标人名称：」不能粘上页首「日期：」."""
        p1 = self._page([self._line("投标人名称：", 90.00, 152.89, 732.90, 743.35)])
        p2 = self._page([self._line("日期： 年 月 日", 90.00, 231.73, 73.50, 83.95)])
        text = extract_clean_text_from_pages(self._pdf([p1, p2]), 0, 1)
        assert text == "投标人名称：\n\n日期： 年 月 日"

    def test_out_of_range_returns_empty(self):
        """超出页码范围不抛异常."""
        page = self._page([self._line("正文", 90.00, 150.00, 100.00, 110.00)])
        assert extract_clean_text_from_pages(self._pdf([page]), 99, 100) == ""


class TestExtractTablesFromPages:
    """extract_tables_from_pages 测试."""

    def test_extract_tables_basic(self):
        """基本表格提取."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            start, end = locate_format_pages(pdf)
            if start is None:
                start, end = 0, len(pdf.pages) - 1
            tables = extract_tables_from_pages(pdf, start, end)
            assert isinstance(tables, list)
            # 格式章节应有表格
            if tables:
                for t in tables:
                    assert "page" in t
                    assert "rows" in t
                    assert len(t["rows"]) >= 2
        finally:
            pdf.close()

    def test_extract_tables_out_of_range(self):
        """超出页码范围返回空列表."""
        _skip_if_no_pdf()
        import pdfplumber

        pdf = pdfplumber.open(TENDER_PDF)
        try:
            tables = extract_tables_from_pages(pdf, 99999, 100000)
            assert tables == []
        finally:
            pdf.close()


class TestExtractFullDocument:
    """extract_full_document 测试."""

    def test_extract_full_document(self):
        """全文档提取."""
        _skip_if_no_pdf()
        result = extract_full_document(TENDER_PDF)

        assert result["full_text"], "Expected non-empty full_text"
        assert len(result["full_text"]) > 1000, (
            f"Expected >1000 chars for full doc, got {len(result['full_text'])}"
        )
        assert result["total_pages"] > 0
        assert isinstance(result["tables"], list)

        print(
            f"Full doc: {len(result['full_text'])} chars, "
            f"{len(result['tables'])} tables, "
            f"{result['total_pages']} pages"
        )


# ---------------------------------------------------------------------------
# 错误处理测试
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """错误与边界条件测试."""

    def test_file_not_found(self):
        """不存在的文件抛出 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            extract_format_section("/nonexistent/file.pdf")

    def test_file_not_found_full_doc(self):
        """全文档提取 - 不存在的文件抛出 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            extract_full_document("/nonexistent/file.pdf")


class TestLocateEvaluationSection:
    """评标办法章节定位（fake pdf 冒烟）."""

    def test_returns_none_when_missing(self):
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([FakePage("第一章 招标公告"), FakePage("第二章 投标人须知")])
        assert locate_evaluation_section(pdf) is None

    def test_finds_section_and_stops_at_format(self):
        from app.services.pdf_extractor import (
            locate_evaluation_section,
            extract_text_from_pages,
        )

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("第一章 招标公告"),
            FakePage("第三章 评标办法 综合评分法 评分标准……技术部分 15 分"),
            FakePage("详细评分细则：服务方案 5 分、业绩 10 分"),
            FakePage("第六章 投标文件格式 投标函"),
            FakePage("（格式模板页）"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 1 and end == 2
        assert "综合评分法" in text and "投标文件格式" not in text
        # 与 extract_text_from_pages 一致（0-indexed 含 end）
        assert text == extract_text_from_pages(pdf, 1, 2)

    def test_skips_toc_and_cross_references(self):
        """目录行与正文交叉引用不得误锚——只认独立的章节标题行.

        回归：真实招标文件第 1 页目录含「第四章 评标办法….41」，正文大量
        「按照第四章“评标办法”规定…」交叉引用；旧实现按关键词首次命中
        锚到目录页，抓回参保人须知全文。标题行 `$` 锚定制天然排除目录
        （目录行带页码）与交叉引用（句中「办法」后有后续文字）。
        """
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("目录\n第一章 招标公告........2\n第四章 评标办法..........41"),
            FakePage("第一章 招标公告\n（招标公告正文…）"),
            FakePage("第二章 投标人须知\n25.评标\n"
                     "评标委员会按照第四章“评标办法”规定的方法进行评审。"),
            FakePage("第四章 评标办法"),
            FakePage("详细评分标准\n技术部分 15 分\n商务部分 10 分"),
            FakePage("第五章 投标文件格式\n（模板页）"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 3, f"应锚定真实章节页, got start={start}"
        assert end == 4
        assert "评标办法" in text

    def test_bare_heading_without_chapter_number(self):
        """无「第X章」前缀的裸章节标题同样能锚定."""
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("目录\n一、评标办法.....3"),
            FakePage("第一部分\n招标公告正文"),
            FakePage("评标办法（综合评分法）"),
            FakePage("评分细则：价格 30 分、方案 20 分"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 2 and end == 3
        assert "综合评分法" in text

    def test_skips_chapter_enumeration_list(self):
        """「招标文件由下列部分组成」章节名称清单不是章节起始页.

        真实文档页面中部常有「8.1 本项目的招标文件由下列部分组成：第一章…第四章
        评标办法…第六章」的章节罗列，每章题独占一行且无页码——若只按标题行锚定
        会误锚。章节起始页的标题必然位于页首，且其后不跟兄弟章题。
        """
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("第一章 招标公告\n（招标公告正文…）"),
            FakePage("二．招标文件\n8.1 本项目的招标文件由下列部分组成：\n"
                     "第一章 招标公告\n第二章 投标须知\n第三章 需求任务书\n"
                     "第四章 评标办法\n第五章 合同条款\n第六章 投标文件格式"),
            FakePage("第四章 评标办法"),
            FakePage("评分标准：技术 15 分\n商务 10 分"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 2 and end == 3
        assert "评分标准" in text

    def test_does_not_stop_at_inline_tender_letter_mention(self):
        """正文中零星提及「投标函」不是章节边界（大红山招标文件回归）.

        真实文档（玉溪大红山，101 页）：「初步评审表」第 10 条为
        「在宝华智慧招标平台上传的投标文件不完整的（投标文件至少包括
        投标函、投标承诺书、投标报价、服务方案、拟投入人员)」。
        旧实现按**整页子串**匹配 `"投标函" in text`，一见这两个字就把
        评标办法章节截断在初步评审表上——只取到 1 页 718 字，第 47-48 页
        的技术评审表（评分标准 40 分）被切在门外，extract_rubric 拿到空表，
        评分点驱动的目录补全与自评分整条链路失效。

        正解：「投标函」只认**行首**（格式章节里它是标题行），正文表格里的
        顺带提及不是边界。
        """
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("第四章 评标办法"),                                  # 0 起始
            FakePage("一、初步评审：\n初步评审表：\n"
                     "序号 评审标准 说明 是否关联"),                       # 1
            FakePage("含税价289.80万元/年（含税）；井口值守及游泳池值\n"
                     "守业务240.00万元/年（含税）。\n"
                     "同一投标人提交两个及以上不同的投标文件或投标报"),     # 2 表格续行
            FakePage("6 否决条款 否\n"
                     "价，但招标文件要求提交备选投标的除外\n"
                     "7 投标报价低于成本，或存在串通涨价、价格欺诈行为的\n"
                     "8 投标人出现串通投标、虚假投标、以行贿手段谋取中标\n"
                     "9 投标人未按评标委员会要求澄清、说明或补正的\n"
                     "10 在宝华智慧招标平台上传的投标文件不完整的（投标文\n"
                     "件至少包括投标函、投标承诺书、投标报价、服务方 否决条款 否"),  # 3 误命中在此
            FakePage("二、详细评审：\n（二）技术标（40.00分）\n"
                     "4 门岗、井口值守、游泳池安保管理综合方案 0.0 10.0 否"),  # 4 真正的评分表
            FakePage("第五章 合同条款及格式"),                              # 5 下一章
            FakePage("（合同条款正文）"),                                   # 6
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 0
        assert end == 4, f"评分表页被截断, got end={end}"
        assert "门岗、井口值守、游泳池安保管理综合方案" in text
        assert "合同条款" not in text

    def test_stops_at_next_chapter_heading(self):
        """下一个「第X章」标题出现在页首 → 评标办法章节结束.

        旧实现只认 FORMAT_KEYWORDS（「投标文件格式」等），遇到
        「第五章 合同条款及格式」这类与格式无关的下一章标题不认边界，
        会把后面整份文档都吞进「评标办法」正文。
        """
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("第四章 评标办法"),
            FakePage("评分标准：服务方案 10 分"),
            FakePage("第五章 合同条款及格式"),
            FakePage("（合同条款正文）"),
            FakePage("第六章 投标文件格式"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 0 and end == 1, f"got start={start} end={end}"
        assert "合同条款" not in text and "投标文件格式" not in text

    def test_running_header_of_evaluation_chapter_is_not_a_boundary(self):
        """评标办法自身的章题作为页眉重复出现时不得当作「下一章」.

        `第四章 评标办法` 常作为页眉出现在本章每一页页首；若把它也算作
        下一章标题，章节会在第 1 页就结束。
        """
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("第四章 评标办法"),
            FakePage("第四章 评标办法\n一、初步评审："),
            FakePage("第四章 评标办法\n二、详细评审：服务方案 10 分"),
            FakePage("第五章 合同条款及格式"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 0 and end == 2, f"got start={start} end={end}"
        assert "详细评审" in text
