"""Regression test: HTML chapter content must not leak literal tags into the .docx.

Bug context: ``BidEditor.tsx`` saves ``editor.getHTML()`` — the chapter body it
hands to ``PUT /projects/{id}/chapters/{chapter_id}`` is HTML (``<p>…<br>…</p>``),
which lands in ``project_chapters.final_content``. The render engine, however,
splits content on ``\\n`` and knows nothing about HTML. Editor HTML carries **no**
newlines at all, so an edited chapter collapsed into a single line and every tag
was printed verbatim:

    一、投标函<p>二、投标函<br>投标函<br>致： 玉溪大红山矿业有限公司<br>…

Only chapters edited through BidEditor are affected — ``TreeEditor`` rebuilds the
chapter as plain text (verified against the DB: 投标函 had 0 newlines and ``<p>``
tags; 技术标 had 339 newlines and no tags).

These tests must fail if the HTML normalisation is ever removed again.
"""

from docx import Document

from app.services import render_engine

# Shaped after the real row in ``project_chapters.final_content``.
BID_LETTER_HTML = (
    "<p>二、投标函<br>投标函<br>致： 玉溪大红山矿业有限公司<br>"
    "根据贵方 玉溪大红山矿业有限公司保安业务及矿区井口、生活区游泳池值守服务业务项目 "
    "招标文件（招标编号为 0721-2366A965-S07-762/01），我方针对本项目的投标总报价为："
    "1377.2262万元人民币，含税（大写：人民币壹仟佰柒拾柒万贰仟贰陆拾贰元整）</p>"
    "<p>日期： 2026年09月15日<br>投标人联系方式<br>"
    "地址：云南省昆明市高新技术开发区城市新宸商务大厦A幢3层310号邮编：</p>"
)


def _render(tmp_path, monkeypatch, content, title="投标函"):
    """Render one chapter and return the resulting document."""
    monkeypatch.setattr(render_engine.settings, "OUTPUT_DIR", str(tmp_path))
    out = render_engine.render_bid_to_docx(
        [{"title": title, "content": content}], "测试项目"
    )
    return Document(out)


def _all_text(doc):
    texts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                texts.append(cell.text)
    return texts


# ── HTML 章节 ────────────────────────────────────────────────────────────

def test_html_content_renders_without_literal_tags(tmp_path, monkeypatch):
    """Editor HTML must reach the document as text, not as markup."""
    doc = _render(tmp_path, monkeypatch, BID_LETTER_HTML)

    text = "\n".join(_all_text(doc))
    for tag in ("<p>", "</p>", "<br>", "</br>", "<br/>", "<div>"):
        assert tag not in text, f"literal {tag} leaked into the document"


def test_html_br_becomes_separate_lines(tmp_path, monkeypatch):
    """<br> is a soft line break — each line must land in its own paragraph.

    This is the whole point of normalising: the renderer's unit of layout is the
    line, so <br> has to become a newline or the letter collapses into one blob.
    """
    doc = _render(
        tmp_path, monkeypatch, "<p>第一行文字<br>第二行文字</p>", title="第一章"
    )

    paragraphs = [p.text for p in doc.paragraphs]
    assert "第一行文字" in paragraphs
    assert "第二行文字" in paragraphs
    assert "第一行文字第二行文字" not in paragraphs


def test_html_closing_paragraph_starts_new_paragraph(tmp_path, monkeypatch):
    """</p><p> separates paragraphs as well."""
    doc = _render(
        tmp_path, monkeypatch, "<p>上段落</p><p>下段落</p>", title="第一章"
    )

    paragraphs = [p.text for p in doc.paragraphs]
    assert "上段落" in paragraphs
    assert "下段落" in paragraphs


def test_html_entities_are_unescaped(tmp_path, monkeypatch):
    """TipTap escapes typed characters — &amp; must show up as & in the .docx."""
    doc = _render(
        tmp_path, monkeypatch, "<p>云南领航保安服务有限公司&amp;分公司</p>", title="第一章"
    )

    assert "云南领航保安服务有限公司&分公司" in [p.text for p in doc.paragraphs]


# ── 纯文本章节（防回归）─────────────────────────────────────────────────

def test_plain_text_content_still_renders_per_line(tmp_path, monkeypatch):
    """Plain-text chapters (TreeEditor / pipeline output) must be untouched."""
    doc = _render(tmp_path, monkeypatch, "第一行\n第二行", title="第一章")

    paragraphs = [p.text for p in doc.paragraphs]
    assert "第一行" in paragraphs
    assert "第二行" in paragraphs


def test_angle_brackets_in_plain_text_are_not_html(tmp_path, monkeypatch):
    """A plain-text chapter may legitimately contain < and > — leave it alone."""
    content = "投标报价＜100万元，本项目 <5 家投标人参与"
    doc = _render(tmp_path, monkeypatch, content, title="第一章")

    assert content in [p.text for p in doc.paragraphs]


def test_html_with_angle_brackets_in_body_keeps_them(tmp_path, monkeypatch):
    """Tags are stripped but normal < > used as text inside HTML survive."""
    doc = _render(
        tmp_path, monkeypatch, "<p>报价 ＜100万元</p>", title="第一章"
    )

    assert "报价 ＜100万元" in [p.text for p in doc.paragraphs]