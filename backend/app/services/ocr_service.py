"""宏曦标书 - OCR & Document Image Analysis Service.

Two strategies, tried in order:
1. VISION (primary): use GPT-4o / Qwen-VL to directly analyze the image
2. Tesseract (fallback): if no vision-capable model configured

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import base64
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
# Vision-based extraction (primary)
# ---------------------------------------------------------------------------


def _image_to_base64(image_path: str) -> str:
    """Read image file and return base64 string."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _build_vision_prompt(doc_type: str) -> str:
    """Build the prompt for vision model based on document type."""
    common = "请直接返回JSON对象，不要有其他文字。无法确定的字段留空字符串。"

    if doc_type == "business_license":
        return f"""请识别这张营业执照图片，提取以下信息：
- company_name: 企业名称/公司名称
- business_license_number: 统一社会信用代码（18位数字+字母）
- legal_rep_name: 法定代表人姓名
- address: 住所/经营场所/公司地址
- issue_date: 成立日期（转换为YYYY-MM-DD格式）

{common}"""

    elif doc_type == "id_card":
        return f"""请识别这张身份证图片，提取以下信息：
- name: 姓名
- id_number: 公民身份号码（18位，末位可能是X）
- address: 住址
- gender: 性别
- birth_date: 出生日期（转换为YYYY-MM-DD格式）

{common}"""

    else:
        return f"""请识别这张资质证书/许可证图片，提取以下信息：
- name: 证书/资质名称（如"保安服务许可证""营业执照""质量管理体系认证证书"）
- cert_number: 证书编号/许可号/注册号
- issuing_authority: 颁发机构/发证机关全称
- issue_date: 发证日期（转换为YYYY-MM-DD格式）
- expiry_date: 有效期截止日期（转换为YYYY-MM-DD格式）

{common}"""


async def _extract_with_vision(image_path: str, doc_type: str) -> Optional[Dict[str, Any]]:
    """Use a vision model to analyze the document image directly."""
    if not ai_adapter.supports_vision():
        return None

    try:
        image_b64 = _image_to_base64(image_path)
        prompt = _build_vision_prompt(doc_type)

        response = await ai_adapter.chat_completion_vision(
            image_base64=image_b64,
            prompt=prompt,
            temperature=0.1,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        result = json.loads(response)

        # Normalize date fields
        for key in ("issue_date", "expiry_date", "birth_date"):
            val = result.get(key, "")
            if val and re.match(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", str(val)):
                result[key] = re.sub(r"[年月/]", "-", str(val)).replace("日", "")

        # Normalize common field name variations
        if "business_license_number" not in result and "credit_code" in result:
            result["business_license_number"] = result["credit_code"]

        logger.info("Vision OCR: extracted %d fields from %s", len(result), doc_type)
        return result

    except ValueError as e:
        logger.info("No vision-capable model available: %s", e)
        return None
    except Exception as exc:
        logger.warning("Vision OCR failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Tesseract OCR (fallback)
# ---------------------------------------------------------------------------


def _run_tesseract(image_path: str, psm: int = 3) -> Optional[str]:
    try:
        import subprocess
        result = subprocess.run(
            ["tesseract", image_path, "stdout",
             "-l", "chi_sim+chi_tra+eng", "--psm", str(psm)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            lines = [l.strip() for l in result.stdout.strip().split("\n") if len(l.strip()) > 1]
            return "\n".join(lines) if lines else None
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Tesseract failed: %s", exc)
    return None


def extract_text_from_image(image_path: str) -> Optional[str]:
    """OCR via Tesseract (only used as fallback)."""
    for psm in (3, 6):
        text = _run_tesseract(image_path, psm)
        if text and len(text) >= 20:
            return text
    # Try both PSMs, return best
    best, best_len = None, 0
    for psm in (3, 6):
        t = _run_tesseract(image_path, psm)
        if t and len(t) > best_len:
            best, best_len = t, len(t)
    return best


# ---------------------------------------------------------------------------
# AI text parsing (only for Tesseract fallback path)
# ---------------------------------------------------------------------------


async def _parse_ocr_with_ai(ocr_text: str, doc_type: str) -> Dict[str, Any]:
    prompt = _build_vision_prompt(doc_type) + f"\n\n以下是从图片中OCR识别出的文字（可能有错漏），请基于这些文字提取信息：\n\n{ocr_text[:2500]}"

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": "你是专业证件信息提取助手。请结合OCR文字和上下文纠错，精确提取。不确定的字段留空。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1, max_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(response)
    except Exception as exc:
        logger.warning("AI OCR parse failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Master pipeline: Vision → Tesseract+AI fallback
# ---------------------------------------------------------------------------


async def analyze_document_image(
    file_bytes: bytes,
    filename: str,
    doc_type: str = "qualification",
) -> Dict[str, Any]:
    image_path = save_ocr_image(file_bytes, filename)

    # --- Strategy 1: Vision model ---
    vision_result = await _extract_with_vision(image_path, doc_type)
    if vision_result:
        vision_result["image_path"] = image_path
        vision_result["method"] = "vision"
        return vision_result

    # --- Strategy 2: Tesseract + AI ---
    ocr_text = extract_text_from_image(image_path)
    result: Dict[str, Any] = {
        "image_path": image_path,
        "ocr_text": ocr_text or "",
        "method": "tesseract",
    }

    if ocr_text and len(ocr_text.strip()) >= 10:
        parsed = await _parse_ocr_with_ai(ocr_text, doc_type)
        result.update(parsed)
    else:
        result.update({"name": "", "cert_number": "", "issuing_authority": "",
                        "issue_date": "", "expiry_date": ""})

    return result
