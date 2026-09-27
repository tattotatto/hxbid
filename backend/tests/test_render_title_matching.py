"""宏曦标书 - 渲染时按标题查表必须容忍序号差异 单元测试.

线上反馈：「取章节结构的时候连序号一起取就可以自动匹配好多了，如一、封面」。

根因：两侧对序号的**存放方式不一致** ——
  - chapter_structure_json（生成侧）：AI 把序号并进了 title → 「一、封面」
  - format_template_json（模板侧）：序号留在 number 字段、title 是裸的 → 「封面」
而 `_render_body_strict` 里六处都用**裸字符串精确查表**：查不到就静默落空、
内容走位置兜底；更糟的是 rendered_set 也会误判"这章没渲染过"，把同一章
**再渲染一遍**。

修法：查表前两侧都归一化（剥序号 + 去空白 + 全半角统一）。这是稳的 ——
两侧标题来自两次独立的 AI 调用，靠"要求它们都带序号"不可能可靠一致。
"""

from app.services.render_engine import _canonical_title, _title_index


class TestCanonicalTitle:
    def test_numbered_and_bare_forms_are_equal(self):
        assert _canonical_title("一、封面") == _canonical_title("封面")

    def test_other_numbering_styles_also_collapse(self):
        """两侧的序号体系不一定相同 —— 阿拉伯数字、括号都要归一."""
        bare = _canonical_title("封面")
        for form in ("一、封面", "（一）封面", "(一)封面", "1.封面", "1、封面", "第一节 封面"):
            assert _canonical_title(form) == bare, form

    def test_whitespace_and_fullwidth_ignored(self):
        assert _canonical_title("　封面 ") == _canonical_title("封面")
        assert _canonical_title("一、 封面") == _canonical_title("封面")

    def test_different_titles_stay_different(self):
        assert _canonical_title("封面") != _canonical_title("投标函")

    def test_empty_is_empty(self):
        assert _canonical_title("") == ""
        assert _canonical_title(None) == ""


class TestTitleIndex:
    def test_generated_chapter_with_numbering_matches_bare_template_title(self):
        """**这是本次报的问题**：生成侧「一、封面」要能被模板侧「封面」查到."""
        chapters = [{"title": "一、封面", "content": "封面内容"}]
        index = _title_index(chapters)
        assert index.get(_canonical_title("封面")) is chapters[0]

    def test_bare_generated_title_matches_numbered_template_title(self):
        """反向也要成立（哪侧带序号都可能）."""
        chapters = [{"title": "封面", "content": "x"}]
        index = _title_index(chapters)
        assert index.get(_canonical_title("一、封面")) is chapters[0]

    def test_first_winner_kept_on_duplicate_keys(self):
        """同名（归一化后）章节只保留先出现的 —— 与 setdefault 语义一致."""
        chapters = [{"title": "一、封面", "content": "先"}, {"title": "封面", "content": "后"}]
        index = _title_index(chapters)
        assert index[_canonical_title("封面")]["content"] == "先"

    def test_empty_titles_are_skipped(self):
        index = _title_index([{"title": ""}, {"title": "  "}, {"title": "封面"}])
        assert _canonical_title("") not in index
        assert len(index) == 1


class TestStrictRenderEndToEnd:
    """真跑一遍渲染，确认内容真的落到了对应小节下（不只是索引对了）."""

    FMT = {
        "document_structure": [{
            "number": "一", "title": "封面",
            "children": [
                {"number": "（一）", "title": "投标函", "type": "fixed_form"},
                {"number": "（二）", "title": "开标一览表", "type": "table"},
            ],
        }],
        "global_format_rules": {},
    }

    # 生成侧把序号并进了 title —— 与模板侧的裸 title 不一致，正是本次的坑
    CHAPTERS = [{
        "title": "一、封面",
        "content": "封面正文",
        "children": [
            {"title": "（一）投标函", "content": "投标函正文"},
            {"title": "（二）开标一览表", "content": "报价表正文"},
        ],
    }]

    @staticmethod
    def _render(fmt, chapters):
        from docx import Document
        from app.services import render_engine

        doc = Document()
        render_engine._render_body_strict(
            doc, chapters, fmt, render_engine.DEFAULT_STYLE)
        return "\n".join(p.text for p in doc.paragraphs)

    def test_child_content_lands_under_matching_child(self):
        text = self._render(self.FMT, self.CHAPTERS)
        assert "投标函正文" in text, "带序号的章节标题没匹配上裸标题的模板小节"
        assert "报价表正文" in text, "该走位置兜底了 —— 说明查表落空"

    def test_chapter_not_rendered_twice(self):
        """rendered_set 也用裸标题 —— 归一化前 "一、封面" 不在集合里，
        函数末尾的防御性兜底会把整章**再渲染一遍**（标题出现两次）。

        注意严格模式只渲染子小节、不渲染 part 自身的内容，所以数的是标题。
        """
        text = self._render(self.FMT, self.CHAPTERS)
        assert text.count("一、封面") == 1, "同一章被重复渲染（rendered_set 误判）"
        assert "封面正文" not in text, "整章被兜底逻辑重复渲染了一次"

    def test_bare_titles_also_match(self):
        """反向：生成侧不带序号、模板侧带，也要匹配上."""
        fmt = {"document_structure": [{
            "number": "一", "title": "一、封面",
            "children": [{"number": "（一）", "title": "（一）投标函"}],
        }]}
        chapters = [{"title": "封面", "children": [
            {"title": "投标函", "content": "投标函正文"}]}]
        assert "投标函正文" in self._render(fmt, chapters)
