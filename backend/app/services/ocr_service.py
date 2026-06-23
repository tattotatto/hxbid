"""宏曦标书 - OCR & Document Image Analysis Service.

Strategy (optimized for Chinese certificate/license images):
1. Try original image first — Tesseract handles clean scans well
2. Only if text is sparse, apply conservative preprocessing as fallback
3. AI parses OCR output with error-correction awareness

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.services.ai_adapter import ai_adapter

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(settings.UPLOAD_DIR)


# ---------------------------------------------------------------------------
# Image saving
# ---------------------------------------------------------------------------


def save_ocr_image(file_bytes: bytes, filename: str, subdir: str = "ocr") -> str:
    sub_path = UPLOAD_DIR / subdir
    sub_path.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix if filename else ".jpg"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    filepath = sub_path / saved_name
    with open(filepath, "wb") as f:
        f.write(file_bytes)
    return str(filepath.relative_to(Path.cwd()))


# ---------------------------------------------------------------------------
# Conservative image preprocessing (only as fallback)
# ---------------------------------------------------------------------------


def _preprocess_fallback(image_path: str) -> Optional[str]:
    """Conservative enhancement: only grayscale + moderate contrast.
    No binarization — avoids destroying thin Chinese strokes.
    """
    try:
        from PIL import Image, ImageEnhance

        img = Image.open(image_path)
        if img.mode != "L":
            img = img.convert("L")

        # Moderate contrast boost only
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)

        # Resize if too small
        if img.width < 800:
            ratio = 800 / img.width
            img = img.resize((800, int(img.height * ratio)), Image.LANCZOS)

        preproc_path = image_path.rsplit(".", 1)[0] + "_fb.png"
        img.save(preproc_path, "PNG")
        return preproc_path
    except Exception as exc:
        logger.warning("Fallback preprocessing failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


def _run_tesseract(image_path: str, psm: int = 3) -> Optional[str]:
    try:
        import subprocess
        result = subprocess.run(
            ["tesseract", image_path, "stdout",
             "-l", "chi_sim+chi_tra+eng",
             "--psm", str(psm)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 1]
            return "\n".join(lines) if lines else None
    except FileNotFoundError:
        logger.debug("Tesseract not installed")
    except Exception as exc:
        logger.warning("Tesseract (PSM=%d) failed: %s", psm, exc)
    return None


def _ocr_with_pytesseract(image_path: str, psm: int = 3) -> Optional[str]:
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path)
        config = f"--psm {psm} -l chi_sim+chi_tra+eng"
        text = pytesseract.image_to_string(img, config=config)
        lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 1]
        return "\n".join(lines) if lines else None
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pytesseract (PSM=%d) failed: %s", psm, exc)
    return None


# ---------------------------------------------------------------------------
# Smart OCR: original first, fallback only if needed
# ---------------------------------------------------------------------------


def extract_text_from_image(image_path: str) -> Optional[str]:
    """OCR with original-first strategy.

    1. Try original image with PSM 3 (auto) — best for clean document scans
    2. Try original with PSM 6 (uniform block) — good for certificate layouts
    3. Only if both yield < 30 chars, apply conservative preprocessing and retry
    """
    # --- Pass 1: Original image ---
    for psm in (3, 6):
        text = _ocr_with_pytesseract(image_path, psm)
        if not text:
            text = _run_tesseract(image_path, psm)
        if text and len(text) >= 30:
            logger.info("OCR: %d chars from original (PSM=%d)", len(text), psm)
            return text

    # If we got some text but not much, return what we have
    best_text = None
    best_len = 0
    for psm in (3, 6):
        text = _ocr_with_pytesseract(image_path, psm)
        if not text:
            text = _run_tesseract(image_path, psm)
        if text and len(text) > best_len:
            best_text, best_len = text, len(text)

    # --- Pass 2: Conservative fallback (only if original gave < 30 chars) ---
    if best_len < 30:
        fb_path = _preprocess_fallback(image_path)
        if fb_path:
            for psm in (3, 6):
                text = _ocr_with_pytesseract(fb_path, psm)
                if not text:
                    text = _run_tesseract(fb_path, psm)
                if text and len(text) > best_len:
                    best_text, best_len = text, len(text)

    if best_text:
        logger.info("OCR: %d chars returned", best_len)
    return best_text


# ---------------------------------------------------------------------------
# AI field extraction — with OCR error awareness
# ---------------------------------------------------------------------------


_OCR_HINTS = """注意：OCR识别中文证件时可能有以下错误，请结合上下文纠正：
- 形近字如"宫"→"官"、"称"→"秤"、"码"→"玛"、"份"→"分"
- 数字0↔字母O、9↔g、1↔l 混淆
- 漏字、多字、断行错位"""


async def parse_qualification_fields(ocr_text: str) -> Dict[str, Any]:
    prompt = f"""{_OCR_HINTS}

从以下证件OCR文字中提取信息，以JSON返回：
{{"name":"证件名称","cert_number":"编号","issuing_authority":"颁发机构","issue_date":"发证日期YYYY-MM-DD","expiry_date":"到期日YYYY-MM-DD"}}
无法确定的字段留空字符串。

OCR文字：
{ocr_text[:2500]}"""

    try:
        messages = [
            {"role": "system", "content": "你是证件信息提取专家。请精确提取，不确定的字段留空。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages, temperature=0.1, max_tokens=500,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)
        for date_field in ("issue_date", "expiry_date"):
            val = result.get(date_field, "")
            if val and re.match(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", str(val)):
                result[date_field] = re.sub(r"[年月/]", "-", str(val)).replace("日", "")
        return {
            "name": str(result.get("name", "")).strip(),
            "cert_number": str(result.get("cert_number", "")).strip(),
            "issuing_authority": str(result.get("issuing_authority", "")).strip(),
            "issue_date": str(result.get("issue_date", "")).strip(),
            "expiry_date": str(result.get("expiry_date", "")).strip(),
            "raw_text": ocr_text[:500],
        }
    except Exception as exc:
        logger.warning("Qualification parse failed: %s", exc)
        return {"name": "", "cert_number": "", "issuing_authority": "",
                "issue_date": "", "expiry_date": "", "raw_text": ocr_text[:500]}


async def parse_business_license_fields(ocr_text: str) -> Dict[str, Any]:
    prompt = f"""{_OCR_HINTS}

从营业执照OCR文字中提取，JSON格式：
{{"company_name":"企业名称","business_license_number":"统一社会信用代码(18位)","legal_rep_name":"法定代表人姓名","address":"住所/经营场所","issue_date":"成立日期YYYY-MM-DD"}}
无法确定则留空。

OCR文字：
{ocr_text[:2500]}"""

    try:
        messages = [
            {"role": "system", "content": "你是营业执照信息提取专家。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages, temperature=0.1, max_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(response)
    except Exception as exc:
        logger.warning("Business license parse failed: %s", exc)
        return {}


async def parse_id_card_fields(ocr_text: str) -> Dict[str, Any]:
    prompt = f"""{_OCR_HINTS}

从身份证OCR文字中提取，JSON格式：
{{"name":"姓名","id_number":"身份证号(18位)","address":"住址","gender":"性别","birth_date":"出生日期YYYY-MM-DD"}}
无法确定则留空。

OCR文字：
{ocr_text[:2500]}"""

    try:
        messages = [
            {"role": "system", "content": "你是身份证信息提取专家。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages, temperature=0.1, max_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(response)
    except Exception as exc:
        logger.warning("ID card parse failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Master pipeline
# ---------------------------------------------------------------------------


async def analyze_document_image(
    file_bytes: bytes,
    filename: str,
    doc_type: str = "qualification",
) -> Dict[str, Any]:
    image_path = save_ocr_image(file_bytes, filename)
    ocr_text = extract_text_from_image(image_path)

    result: Dict[str, Any] = {
        "image_path": image_path,
        "ocr_text": ocr_text or "",
        "ocr_available": ocr_text is not None and len(ocr_text or "") > 5,
    }

    if not ocr_text or len(ocr_text.strip()) < 10:
        empty = {"name": "", "cert_number": "", "issuing_authority": "",
                 "issue_date": "", "expiry_date": ""}
        if doc_type == "business_license":
            empty.update({"company_name": "", "business_license_number": "",
                          "legal_rep_name": "", "address": ""})
        if doc_type == "id_card":
            empty.update({"name": "", "id_number": "", "address": ""})
        result.update(empty)
        return result

    if doc_type == "business_license":
        parsed = await parse_business_license_fields(ocr_text)
    elif doc_type == "id_card":
        parsed = await parse_id_card_fields(ocr_text)
    else:
        parsed = await parse_qualification_fields(ocr_text)

    result.update(parsed)
    return result
