"""宏曦标书 - Word排版渲染引擎.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.

Converts structured bid chapters into a fully formatted .docx with cover page,
headers, footers, and proper Chinese typography. Also provides PDF export via
LibreOffice headless (best-effort) and a default-template factory.
"""

import os
import re
import time
import uuid
import subprocess
from datetime import date
from pathlib import Path

from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from app.config import settings

# ── Default style constants (Chinese bid-document spec) ─────────────────

DEFAULT_STYLE = {
    "body_font_name": "宋体",          # 宋体
    "body_font_size": Pt(12),                   # 小四
    "body_line_spacing": 1.5,
    "heading1_font_name": "黑体",       # 黑体
    "heading1_font_size": Pt(16),               # 三号
    "heading2_font_name": "黑体",       # 黑体
    "heading2_font_size": Pt(14),               # 四号
    "margin_top": Cm(2.54),
    "margin_bottom": Cm(2.54),
    "margin_left": Cm(3.17),
    "margin_right": Cm(3.17),
    "header_text": "云南宏曦科技有限公司",
}


# ── Internal helpers ──────────────────────────────────────────────────

def _set_run_font(run, font_name, font_size, bold=False):
    """Set Western + East-Asian font, size, and bold on a ``Run``."""
    run.font.name = font_name
    run.font.size = font_size
    run.bold = bold

    # Ensure the w:eastAsia attribute is set for CJK rendering
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:eastAsia"), font_name)


def _set_paragraph_spacing(paragraph, line_spacing=1.5):
    """Set line spacing on a paragraph (1.5x is the standard for Chinese bids)."""
    pf = paragraph.paragraph_format
    pf.line_spacing = line_spacing


def _add_page_number(paragraph):
    """Insert a bare PAGE field into *paragraph* using low-level OxmlElement calls.

    Produces the equivalent of Word's ``{ PAGE }`` field so that each page
    footer displays the current page number.
    """
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # -- fldChar begin --
    run_begin = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    run_begin._element.append(fld_begin)

    # -- instrText --
    run_instr = paragraph.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    run_instr._element.append(instr)

    # -- fldChar end --
    run_end = paragraph.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run_end._element.append(fld_end)


def _add_cover_page(doc, project_name, style):
    """Append a regulated Chinese bid cover page to *doc*."""
    # ── Leading vertical space ──
    for _ in range(6):
        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(0)
        spacer.paragraph_format.space_before = Pt(0)

    # ── Project name (22 pt 黑体 bold, centred) ──
    p_name = doc.add_paragraph()
    p_name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_name = p_name.add_run(project_name)
    _set_run_font(r_name, style["heading1_font_name"], Pt(22), bold=True)

    doc.add_paragraph()  # blank line

    # ── "投标文件" (18 pt 黑体, centred) ──
    p_bid = doc.add_paragraph()
    p_bid.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_bid = p_bid.add_run("投标文件")  # 投标文件
    _set_run_font(r_bid, style["heading1_font_name"], Pt(18), bold=False)

    doc.add_paragraph()
    doc.add_paragraph()

    # ── Company name (16 pt 黑体 bold, centred) ──
    p_company = doc.add_paragraph()
    p_company.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_company = p_company.add_run("云南宏曦科技有限公司")
    _set_run_font(r_company, style["heading1_font_name"], Pt(16), bold=True)

    doc.add_paragraph()

    # ── Date ──
    p_date = doc.add_paragraph()
    p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_date = p_date.add_run(date.today().strftime("%Y年%m月%d日"))
    _set_run_font(r_date, style["heading1_font_name"], Pt(14), bold=False)

    # Page break so chapter 1 starts on a fresh page
    doc.add_page_break()


def _safe_filename(name):
    """Turn *name* into a filesystem-safe slug (no path separators, etc.)."""
    safe = re.sub(r'[<>:"/\\|?*]', "_", name)
    safe = re.sub(r"\s+", "_", safe)
    safe = safe.strip("._")
    return safe if safe else "document"


# ── Public API ────────────────────────────────────────────────────────

def render_bid_to_docx(chapters, project_name, style_config=None):
    """Render bid chapters into a formatted ``.docx`` file.

    Parameters
    ----------
    chapters : list[dict]
        Each dict must have ``"title"`` (str) and ``"content"`` (str).
    project_name : str
        Used on the cover page and in the output filename.
    style_config : dict | None
        Optional overrides merged on top of ``DEFAULT_STYLE``.

    Returns
    -------
    str
        Absolute path to the generated ``.docx`` file.
    """
    style = dict(DEFAULT_STYLE)
    if style_config:
        style.update(style_config)

    doc = Document()

    # ── Page setup (default section for the whole document) ──
    section = doc.sections[0]
    section.top_margin = style["margin_top"]
    section.bottom_margin = style["margin_bottom"]
    section.left_margin = style["margin_left"]
    section.right_margin = style["margin_right"]

    # ── Header ──
    header = section.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h_run = hp.add_run(style["header_text"])
    _set_run_font(h_run, style["heading1_font_name"], Pt(9))

    # ── Footer (centred page number via PAGE field) ──
    footer = section.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0]
    _add_page_number(fp)

    # ── Cover page ──
    _add_cover_page(doc, project_name, style)

    # ── Body: each chapter ──
    for i, chapter in enumerate(chapters):
        # Heading – 黑体 16pt (三号) bold, level-1 style
        h = doc.add_heading(chapter["title"], level=1)
        for run in h.runs:
            _set_run_font(run, style["heading1_font_name"], style["heading1_font_size"], bold=True)

        # Content paragraphs – 宋体 12pt (小四), 1.5× line spacing
        content = chapter.get("content", "")
        for para_text in content.split("\n"):
            para_text = para_text.strip()
            if para_text:
                p = doc.add_paragraph()
                _set_paragraph_spacing(p, style["body_line_spacing"])
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                # First-line indent (2 chars ≈ 24 pt for 12-pt text)
                pf = p.paragraph_format
                pf.first_line_indent = Pt(24)
                run = p.add_run(para_text)
                _set_run_font(run, style["body_font_name"], style["body_font_size"])
            else:
                # Preserve blank-line paragraph separators
                doc.add_paragraph()

        # Page-break between chapters (not after the last one)
        if i < len(chapters) - 1:
            doc.add_page_break()

    # ── Save to OUTPUT_DIR ──
    output_dir = Path(settings.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    uid8 = uuid.uuid4().hex[:8]
    ts = int(time.time())
    file_path = output_dir / f"{_safe_filename(project_name)}_{uid8}_{ts}.docx"

    doc.save(str(file_path))
    return str(file_path.absolute())


def export_to_pdf(docx_path):
    """Convert a ``.docx`` to PDF via LibreOffice headless (best-effort).

    Parameters
    ----------
    docx_path : str
        Path to the ``.docx`` file to convert.

    Returns
    -------
    str | None
        Absolute path to the generated PDF, or ``None`` if conversion failed
        (e.g. LibreOffice is not installed or times out).
    """
    output_dir = str(Path(docx_path).parent)

    try:
        subprocess.run(
            [
                "libreoffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                output_dir,
                str(docx_path),
            ],
            check=True,
            timeout=60,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None

    # Locate the generated PDF (name matches the docx stem)
    pdf_path = Path(output_dir) / (Path(docx_path).stem + ".pdf")
    if pdf_path.exists():
        return str(pdf_path.absolute())

    # Fallback: grab any fresh PDF in the output directory
    for f in sorted(Path(output_dir).glob("*.pdf"), key=os.path.getmtime, reverse=True):
        return str(f.absolute())

    return None


def create_default_template():
    """Create a minimal ``default.docx`` template in ``settings.TEMPLATE_DIR``.

    Returns
    -------
    str
        Absolute path to the created template file.
    """
    template_dir = Path(settings.TEMPLATE_DIR)
    template_dir.mkdir(parents=True, exist_ok=True)

    doc = Document()

    # Apply standard bid margins
    section = doc.sections[0]
    section.top_margin = DEFAULT_STYLE["margin_top"]
    section.bottom_margin = DEFAULT_STYLE["margin_bottom"]
    section.left_margin = DEFAULT_STYLE["margin_left"]
    section.right_margin = DEFAULT_STYLE["margin_right"]

    # Placeholder heading
    p = doc.add_paragraph("投标文件模板")  # 投标文件模板
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    template_path = template_dir / "default.docx"
    doc.save(str(template_path))

    return str(template_path.absolute())
