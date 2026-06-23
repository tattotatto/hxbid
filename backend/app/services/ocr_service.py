"""宏曦标书 - OCR & Document Image Analysis Service.

Extracts structured information from uploaded certificate/license images
using OCR (Tesseract) + AI parsing (DeepSeek).

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
    """Save uploaded image bytes and return relative path."""
    sub_path = UPLOAD_DIR / subdir
    sub_path.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix if filename else ".jpg"
    saved_name = f"{uuid.uuid4().hex}{ext}"
    filepath = sub_path / saved_name
    with open(filepath, "wb") as f:
        f.write(file_bytes)
    return str(filepath.relative_to(Path.cwd()))


# ---------------------------------------------------------------------------
# OCR via Tesseract (best-effort)
# ---------------------------------------------------------------------------


def _extract_text_with_tesseract(image_path: str) -> Optional[str]:
    """Try to extract text from an image using Tesseract OCR.

    Returns None if Tesseract is not installed or fails.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["tesseract", image_path, "stdout", "-l", "chi_sim+eng", "--psm", "3"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except FileNotFoundError:
        logger.debug("Tesseract not installed — OCR skipped")
    except Exception as exc:
        logger.warning("Tesseract OCR failed: %s", exc)
    return None


def _extract_text_with_pytesseract(image_path: str) -> Optional[str]:
    """Try pytesseract Python wrapper."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        if text.strip():
            return text.strip()
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("pytesseract failed: %s", exc)
    return None


def extract_text_from_image(image_path: str) -> Optional[str]:
    """Extract text from an image using available OCR methods.

    Returns the extracted text or None if no OCR is available.
    """
    # Try pytesseract first (Python API is nicer)
    text = _extract_text_with_pytesseract(image_path)
    if text:
        return text
    # Fall back to CLI tesseract
    return _extract_text_with_tesseract(image_path)


# ---------------------------------------------------------------------------
# AI-powered field extraction
# ---------------------------------------------------------------------------


async def parse_qualification_fields(ocr_text: str) -> Dict[str, Any]:
    """Use AI to parse OCR text into structured qualification fields.

    Args:
        ocr_text: Raw text extracted from a certificate/license image.

    Returns:
        Dict with keys: name, cert_number, issuing_authority, issue_date, expiry_date.
    """
    prompt = f"""请从以下证件/执照图片的OCR识别文字中提取关键信息，以JSON格式返回。

OCR识别文字：
{ocr_text[:2000]}

请提取以下字段（无法识别则留空字符串）：
- name: 资质/证书名称（如"保安服务许可证""营业执照"）
- cert_number: 证书编号/注册号
- issuing_authority: 颁发机构/登记机关
- issue_date: 发证日期（格式YYYY-MM-DD，如无法识别则留空）
- expiry_date: 到期日期（格式YYYY-MM-DD，如无法识别则留空）

直接返回JSON对象，不要有其他文字。"""

    try:
        messages = [
            {"role": "system", "content": "你是一个专业的证件信息提取助手。请精确提取证件上的结构化信息。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)

        # Normalize date formats
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
            "raw_text": ocr_text[:500],  # include snippet for user reference
        }
    except Exception as exc:
        logger.warning("AI field extraction failed: %s", exc)
        return {
            "name": "",
            "cert_number": "",
            "issuing_authority": "",
            "issue_date": "",
            "expiry_date": "",
            "raw_text": ocr_text[:500],
            "error": str(exc),
        }


async def parse_business_license_fields(ocr_text: str) -> Dict[str, Any]:
    """Parse OCR text from a business license image."""
    prompt = f"""请从以下营业执照OCR识别文字中提取关键信息，以JSON格式返回。

OCR识别文字：
{ocr_text[:2000]}

提取字段（无法识别则留空字符串）：
- company_name: 公司名称
- business_license_number: 统一社会信用代码/注册号
- legal_rep_name: 法定代表人
- address: 公司地址/经营场所
- issue_date: 成立日期（YYYY-MM-DD）

直接返回JSON。"""

    try:
        messages = [
            {"role": "system", "content": "你是一个专业的营业执照信息提取助手。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(response)
    except Exception as exc:
        logger.warning("Business license parsing failed: %s", exc)
        return {}


async def parse_id_card_fields(ocr_text: str) -> Dict[str, Any]:
    """Parse OCR text from an ID card image."""
    prompt = f"""请从以下身份证OCR识别文字中提取关键信息，以JSON格式返回。

OCR识别文字：
{ocr_text[:2000]}

提取字段（无法识别则留空字符串）：
- name: 姓名
- id_number: 身份证号码（18位）
- address: 住址

直接返回JSON。"""

    try:
        messages = [
            {"role": "system", "content": "你是一个专业的身份证信息提取助手。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages,
            temperature=0.1,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(response)
    except Exception as exc:
        logger.warning("ID card parsing failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Master OCR pipeline
# ---------------------------------------------------------------------------


async def analyze_document_image(
    file_bytes: bytes,
    filename: str,
    doc_type: str = "qualification",  # "qualification" | "business_license" | "id_card"
) -> Dict[str, Any]:
    """Full pipeline: save image → OCR → AI parse → return structured fields.

    Args:
        file_bytes: Raw image bytes.
        filename: Original filename.
        doc_type: Type of document to analyze.

    Returns:
        Dict with extracted fields + image_path + ocr_text.
    """
    # Save image
    image_path = save_ocr_image(file_bytes, filename)

    # OCR
    ocr_text = extract_text_from_image(image_path)

    result: Dict[str, Any] = {
        "image_path": image_path,
        "ocr_text": ocr_text or "",
        "ocr_available": ocr_text is not None and len(ocr_text or "") > 5,
    }

    if not ocr_text or len(ocr_text.strip()) < 10:
        # No useful OCR text — return empty fields
        result.update({
            "name": "", "cert_number": "", "issuing_authority": "",
            "issue_date": "", "expiry_date": "",
        })
        if doc_type == "business_license":
            result.update({
                "company_name": "", "business_license_number": "",
                "legal_rep_name": "", "address": "",
            })
        if doc_type == "id_card":
            result.update({"id_name": "", "id_number": "", "id_address": ""})
        return result

    # AI parsing based on document type
    if doc_type == "business_license":
        parsed = await parse_business_license_fields(ocr_text)
        result.update(parsed)
    elif doc_type == "id_card":
        parsed = await parse_id_card_fields(ocr_text)
        result.update(parsed)
    else:
        parsed = await parse_qualification_fields(ocr_text)
        result.update(parsed)

    return result
