"""Regression test: material images must actually be embedded in the exported .docx.

Bug context: commit d01a0ac ("chore: 8 阶段管线重构后清理 — render_engine 删 242 行
死代码") deleted the only two places that inserted images into the document:
the ``[IMG:...]`` / ``[IDPAIR:...]`` marker handling inside the content loop, and
the ``chapter_images[i]`` loop in the chapter renderer. What survived was a lone
guard that *skips* marker lines, so every material image — 营业执照、法人身份证、
资质证书、人员证书、合同扫描件 — was silently dropped from exported documents.

Upstream (``bid.py`` / ``materials_injection.py``) kept producing those markers and
kept passing ``chapter_images``; only the renderer stopped consuming them. No test
covered images (E2E asserts TOC field / chapters / size, never ``word/media``), so
production shipped image-less bid documents from 2026-08-23 until this fix.

These tests are that missing coverage — they must fail if image embedding is ever
removed again.
"""

from docx import Document
from PIL import Image as PILImage

from app.services import render_engine

STYLE = render_engine.DEFAULT_STYLE


def _make_png(tmp_path, name="license.png", size=(60, 40)):
    """Create a real PNG file so image insertion exercises the real code path."""
    path = tmp_path / name
    PILImage.new("RGB", size, (200, 30, 30)).save(path)
    return path


def _image_count(doc):
    """Number of images actually embedded in the document."""
    return len(doc.inline_shapes)


def _all_text(doc):
    texts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                texts.append(cell.text)
    return texts


# ── [IMG:path|label] marker ──────────────────────────────────────────────

def test_img_marker_embeds_image(tmp_path):
    """A [IMG:path|label] line renders the image instead of being skipped."""
    img = _make_png(tmp_path)
    doc = Document()

    render_engine._render_child_content(
        doc, f"[IMG:{img}|营业执照 — 云南领航保安服务有限公司]", STYLE
    )

    assert _image_count(doc) == 1


def test_img_marker_does_not_leak_as_text(tmp_path):
    """The raw marker must never appear as visible text in the document."""
    img = _make_png(tmp_path)
    doc = Document()

    render_engine._render_child_content(doc, f"[IMG:{img}|营业执照]", STYLE)

    for text in _all_text(doc):
        assert "[IMG:" not in text, f"marker leaked as text: {text!r}"


def test_img_marker_renders_label(tmp_path):
    """The label is shown as a caption under the image."""
    img = _make_png(tmp_path)
    doc = Document()

    render_engine._render_child_content(doc, f"[IMG:{img}|营业执照 — 测试公司]", STYLE)

    assert any("营业执照" in t for t in _all_text(doc))


# ── [IDPAIR:front|fl|back|bl] marker ─────────────────────────────────────

def test_idpair_marker_embeds_two_images(tmp_path):
    """An IDPAIR marker renders both sides of the ID card."""
    front = _make_png(tmp_path, "front.png")
    back = _make_png(tmp_path, "back.png")
    doc = Document()

    render_engine._render_child_content(
        doc,
        f"[IDPAIR:{front}|法定代表人身份证（正面）|{back}|法定代表人身份证（反面）]",
        STYLE,
    )

    assert _image_count(doc) == 2


# ── Robustness ───────────────────────────────────────────────────────────

# ── 固定格式章节（_render_file_section_content）同样吃标记 ────────────────
# 固定格式章节走的是另一条渲染分支：不做 markdown 解析、逐行当正文出。
# 「投标人基本资料」「法定代表人授权委托书」这类章节恰恰是材料的落点，
# 标号却只有 _render_child_content 认——注入的 [IMG:]/[IDPAIR:] 会被当成
# 正文原样打进标书（用户看到的是一行 uploads/company/xxx.png 的字）。

def test_img_marker_embeds_image_in_fixed_form_section(tmp_path):
    img = _make_png(tmp_path)
    doc = Document()

    render_engine._render_file_section_content(
        doc, f"投标人基本情况表\n[IMG:{img}|营业执照]\n", STYLE
    )

    assert _image_count(doc) == 1


def test_img_marker_does_not_leak_as_text_in_fixed_form_section(tmp_path):
    img = _make_png(tmp_path)
    doc = Document()

    render_engine._render_file_section_content(
        doc, f"[IMG:{img}|营业执照]\n", STYLE
    )

    assert not any("[IMG:" in t for t in _all_text(doc))


def test_idpair_marker_embeds_two_images_in_fixed_form_section(tmp_path):
    """法定代表人授权委托书末尾的身份证正反面：成对的 [IDPAIR:] 要出两张图."""
    front = _make_png(tmp_path, "front.png")
    back = _make_png(tmp_path, "back.png")
    doc = Document()
    content = (
        "法定代表人授权委托书\n"
        "本人 （姓名）系 （投标人名称）的法定代表人。\n"
        "年 月 日\n"
        "身份证正面扫描件\n"
        "身份证反面扫描件\n"
        f"[IDPAIR:{front}|法定代表人身份证（正面）|{back}|法定代表人身份证（反面）]\n"
    )

    render_engine._render_file_section_content(doc, content, STYLE)

    assert _image_count(doc) == 2
    assert not any("[IDPAIR:" in t for t in _all_text(doc))
    # 原始措辞照旧出
    assert any("身份证正面扫描件" in t for t in _all_text(doc))


def test_marker_path_with_underscores_survives_markdown_cleaning(tmp_path):
    """路径里的成对单下划线不得被 markdown 斜体正则剥掉。

    固定格式章节先清洗 markdown 再认标记，而 `_clean_lone_symbols` 的斜体正则
    `(?<!_)_(?!_)(.+?)(?<!_)_(?!_)` 会把 `…/test_a_b/license.png` 里的两个
    下划线当 `_强调_` 剥掉，路径变成 `…/testab/license.png`——文件找不到，
    图片静默丢弃。真实素材路径（`uploads/company/legal_rep_id_front.png` 这类
    多段下划线文件名）就会踩中。
    """
    sub = tmp_path / "legal_rep_front_scan"
    sub.mkdir()
    img = PILImage.new("RGB", (60, 40), (200, 30, 30))
    img.save(sub / "id_card_front.png")
    doc = Document()

    render_engine._render_file_section_content(
        doc,
        f"投标人基本情况表\n[IMG:{sub / 'id_card_front.png'}|营业执照]\n",
        STYLE,
    )

    assert _image_count(doc) == 1


def test_missing_image_file_is_skipped_without_error(tmp_path):
    """A marker pointing at a missing file must not crash the export."""
    doc = Document()

    render_engine._render_child_content(
        doc, f"[IMG:{tmp_path / 'nope.png'}|营业执照]", STYLE
    )

    assert _image_count(doc) == 0


def test_relative_upload_dir_path_resolves(tmp_path, monkeypatch):
    """Production stores relative paths; they must resolve against UPLOAD_DIR."""
    monkeypatch.setattr(render_engine.settings, "UPLOAD_DIR", str(tmp_path))
    ocr_dir = tmp_path / "ocr"
    ocr_dir.mkdir()
    PILImage.new("RGB", (60, 40), (10, 80, 200)).save(ocr_dir / "cert.png")

    doc = Document()
    render_engine._render_child_content(doc, "[IMG:ocr/cert.png|保安服务许可证]", STYLE)

    assert _image_count(doc) == 1


def test_marker_mixed_with_body_text(tmp_path):
    """Markers embedded in surrounding prose still render their image."""
    img = _make_png(tmp_path)
    doc = Document()
    content = (
        "投标人基本情况表\n"
        "\n"
        f"[IMG:{img}|营业执照]\n"
        "\n"
        "资质证书清单\n"
    )

    render_engine._render_child_content(doc, content, STYLE)

    assert _image_count(doc) == 1
    assert any("资质证书清单" in t for t in _all_text(doc))


# ── chapter_images parameter (materials_injection 的注入通道) ──────────────

def test_render_bid_to_docx_embeds_chapter_images(tmp_path, monkeypatch):
    """chapter_images[i] must be embedded into chapter i.

    materials_injection.py fills this list with images that have no inline
    [IMG:] marker (人员证书、合同扫描件等); render_bid_to_docx accepted the
    parameter but never read it, so those images vanished too.
    """
    monkeypatch.setattr(render_engine.settings, "OUTPUT_DIR", str(tmp_path))
    img = _make_png(tmp_path, "cert.png")

    out = render_engine.render_bid_to_docx(
        [{"title": "投标人基本资料", "content": "投标人基本情况表"}],
        "测试项目",
        chapter_images=[[{"path": str(img), "label": "保安服务许可证"}]],
    )

    assert _image_count(Document(out)) == 1


def test_chapter_images_do_not_leak_into_other_chapters(tmp_path, monkeypatch):
    """Images stay with their own chapter — index alignment must hold."""
    monkeypatch.setattr(render_engine.settings, "OUTPUT_DIR", str(tmp_path))
    img = _make_png(tmp_path, "cert.png")

    out = render_engine.render_bid_to_docx(
        [
            {"title": "第一章", "content": "正文一"},
            {"title": "第二章", "content": "正文二"},
        ],
        "测试项目",
        chapter_images=[[{"path": str(img), "label": "证书"}], []],
    )

    doc = Document(out)
    assert _image_count(doc) == 1


def test_render_bid_to_docx_without_images_still_works(tmp_path, monkeypatch):
    """The no-images path must keep working (chapter_images is optional)."""
    monkeypatch.setattr(render_engine.settings, "OUTPUT_DIR", str(tmp_path))

    out = render_engine.render_bid_to_docx(
        [{"title": "第一章", "content": "正文"}], "测试项目"
    )

    doc = Document(out)
    assert _image_count(doc) == 0
