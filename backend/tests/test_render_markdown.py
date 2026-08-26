"""Regression test: AI-generated markdown markers (* # _ -) must never leak
into the final Word document.

Bug context: commit d01a0ac removed the old render loop's inline-markdown
cleaning, and the new ``_render_child_content`` renders body lines raw, so
``**bold**``, ``*italic*``, ``# headings``, ``- bullet`` markers reappeared
in generated .docx files. See commit 0ef54ea for the original fix.
"""

from docx import Document

from app.services import render_engine

STYLE = render_engine.DEFAULT_STYLE


def _all_text(doc):
    """Collect every string that will appear in the saved .docx."""
    texts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                texts.append(cell.text)
    return texts


def _assert_no_markdown_markers(doc, label):
    for text in _all_text(doc):
        assert "*" not in text, f"[{label}] '*' leaked: {text!r}"
        assert "#" not in text, f"[{label}] '#' leaked: {text!r}"


def test_body_paragraphs_strip_inline_markdown():
    doc = Document()
    content = (
        "# 总体概述\n"
        "\n"
        "本项目建设**网络架构**，采用*安全加密*技术，并遵循_国家标准_。\n"
        "系统支持 7×24 小时运行，容灾等级符合要求。\n"
        "\n"
        "## 技术要求\n"
        "\n"
        "- 支持高可用部署\n"
        "* 具备容灾能力\n"
        "1. 第一阶段实施\n"
        "2. 第二阶段验收\n"
    )
    render_engine._render_child_content(doc, content, STYLE)
    _assert_no_markdown_markers(doc, "child-content")


def test_table_cells_strip_markdown():
    doc = Document()
    content = (
        "表1：主要技术指标\n"
        "| 指标名称 | 指标值 |\n"
        "|---|---|\n"
        "| **响应时间** | ≤2s |\n"
        "| 并发用户 | *1000* |\n"
    )
    render_engine._render_child_content(doc, content, STYLE)
    _assert_no_markdown_markers(doc, "table")
