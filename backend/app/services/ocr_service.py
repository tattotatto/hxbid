"""宏曦标书 - OCR & Document Image Analysis Service.

Extracts structured information from uploaded certificate/license images
using preprocessing-enhanced OCR (Tesseract) + AI parsing (DeepSeek).

Key improvements for Chinese document accuracy:
- Image preprocessing: grayscale, contrast, binarization, sharpening
- Multipass OCR: tries multiple PSM modes and picks the best result
- AI error correction: prompts tuned to handle OCR garbling of Chinese

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
# Image preprocessing (Pillow)
# ---------------------------------------------------------------------------


def _preprocess_image(image_path: str) -> Optional[str]:
    """Enhance image for better OCR: grayscale, contrast, sharpen, binarize.

    Returns path to the preprocessed image, or None on failure.
    """
    try:
        from PIL import Image, ImageEnhance, ImageFilter

        img = Image.open(image_path)

        # Convert to grayscale
        if img.mode != "L":
            img = img.convert("L")

        # Increase contrast (factor > 1.0)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)

        # Sharpen
        img = img.filter(ImageFilter.SHARPEN)

        # Binarize using adaptive threshold approach:
        # Convert to pure black & white with an aggressive threshold
        # This removes noise and makes text crisp for Tesseract
        img = img.point(lambda x: 0 if x < 140 else 255)

        # Resize if too small (ensure at least 1000px width for good OCR)
        if img.width < 1000:
            ratio = 1000 / img.width
            new_size = (1000, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        # Save preprocessed version
        preproc_path = image_path.rsplit(".", 1)[0] + "_ocr.png"
        img.save(preproc_path, "PNG")
        return preproc_path

    except Exception as exc:
        logger.warning("Image preprocessing failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# OCR via Tesseract
# ---------------------------------------------------------------------------


def _run_tesseract(image_path: str, psm: int = 3) -> Optional[str]:
    """Run Tesseract with given PSM mode on an image.

    Args:
        image_path: Path to the image file.
        psm: Page Segmentation Mode (3=auto, 6=uniform block, 4=single column).

    Returns extracted text or None.
    """
    try:
        import subprocess
        result = subprocess.run(
            [
                "tesseract", image_path, "stdout",
                "-l", "chi_sim+chi_tra+eng",
                "--psm", str(psm),
                "-c", "tessedit_char_whitelist=",  # no whitelist — allow all
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            text = result.stdout.strip()
            # Filter out lines that are just noise
            lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 1]
            return "\n".join(lines) if lines else None
    except FileNotFoundError:
        logger.debug("Tesseract not installed")
    except Exception as exc:
        logger.warning("Tesseract (PSM=%d) failed: %s", psm, exc)
    return None


def _ocr_with_pytesseract(image_path: str, psm: int = 3) -> Optional[str]:
    """OCR using pytesseract Python wrapper with custom config."""
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
# Multipass OCR — try multiple strategies, pick best
# ---------------------------------------------------------------------------


def extract_text_from_image(image_path: str) -> Optional[str]:
    """Extract text using multipass OCR with preprocessing.

    Strategy:
    1. Preprocess image (contrast + binarize + sharpen)
    2. Run OCR on BOTH original AND preprocessed images
    3. Try PSM 3 (auto) and PSM 6 (uniform block) on each
    4. Return the result with the most text
    """
    best_text: Optional[str] = None
    best_len = 0

    preproc_path = _preprocess_image(image_path)
    images_to_try = [image_path]
    if preproc_path:
        images_to_try.append(preproc_path)

    for img_path in images_to_try:
        for psm in (3, 6, 4):
            # Try pytesseract first, then CLI
            text = _ocr_with_pytesseract(img_path, psm)
            if not text:
                text = _run_tesseract(img_path, psm)

            if text and len(text) > best_len:
                best_text = text
                best_len = len(text)

    if best_text:
        logger.info("OCR extracted %d chars (best PSM)", best_len)

    return best_text


# ---------------------------------------------------------------------------
# Enhanced AI field extraction — handles OCR errors
# ---------------------------------------------------------------------------


_COMMON_OCR_ERRORS = """
常见OCR识别错误提示（供你修正参考）：
- 中文形近字混淆：如"宫"→"官", "名"→"各", "称"→"秤", "码"→"玛"
- 数字/字母混淆：如"0"↔"O", "1"↔"l", "9"↔"g", "S"↔"5"
- 统一社会信用代码格式：18位数字+大写字母，如 91530100MA6K3XXXXX
- 身份证号格式：18位数字，末位可能是X
- 日期格式：通常为"2020年01月01日"或"2020-01-01"
- OCR可能漏字、多字、错字，请结合上下文推断正确内容
"""


async def parse_qualification_fields(ocr_text: str) -> Dict[str, Any]:
    """Use AI to parse OCR text into structured qualification fields."""
    prompt = f"""请从以下证件OCR文字中提取结构化信息。

{_COMMON_OCR_ERRORS}

OCR原始文字：
{ocr_text[:2500]}

请提取（无法确定则留空）：
- name: 证件/资质名称（例：保安服务许可证、营业执照、质量管理体系认证证书）
- cert_number: 证书编号/许可号/注册号（注意：OCR易将数字0和字母O混淆，请凭经验推断）
- issuing_authority: 颁发机构/发证机关/登记机关的全称
- issue_date: 发证日期（转换为YYYY-MM-DD格式）
- expiry_date: 有效期截止日期（转换为YYYY-MM-DD格式，注意区分"有效期至"和"签发日期"）

直接返回JSON对象。"""

    try:
        messages = [
            {"role": "system", "content": "你是证件信息提取专家。请根据上下文纠正OCR错误，精确提取每项信息。不确定的字段宁可留空也不乱填。"},
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
        logger.warning("AI field extraction failed: %s", exc)
        return {"name": "", "cert_number": "", "issuing_authority": "",
                "issue_date": "", "expiry_date": "", "raw_text": ocr_text[:500], "error": str(exc)}


async def parse_business_license_fields(ocr_text: str) -> Dict[str, Any]:
    """Parse OCR text from a business license image."""
    prompt = f"""请从以下营业执照OCR文字中提取关键信息。

{_COMMON_OCR_ERRORS}

OCR原始文字：
{ocr_text[:2500]}

提取字段（无法确定则留空）：
- company_name: 企业名称（注意：通常在第一行，格式如"XX有限公司"/"XX有限责任公司"）
- business_license_number: 统一社会信用代码（18位，通常标有"统一社会信用代码"字样）
- legal_rep_name: 法定代表人姓名（标有"法定代表人"字样的后面2-3个中文字）
- address: 住所/经营场所地址（完整的地址信息）
- issue_date: 成立日期（YYYY-MM-DD，注意区分"成立日期"和"核准日期"）

直接返回JSON。"""

    try:
        messages = [
            {"role": "system", "content": "你是营业执照信息提取专家。请精确提取每项信息，利用上下文纠正OCR错误。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages, temperature=0.1, max_tokens=500,
            response_format={"type": "json_object"},
        )
        return json.loads(response)
    except Exception as exc:
        logger.warning("Business license parsing failed: %s", exc)
        return {}


async def parse_id_card_fields(ocr_text: str) -> Dict[str, Any]:
    """Parse OCR text from an ID card image."""
    prompt = f"""请从以下身份证OCR文字中提取关键信息。

{_COMMON_OCR_ERRORS}
身份证OCR常见问题：正面有姓名、性别、民族、出生日期、住址、身份证号；背面有签发机关和有效期限。

OCR原始文字：
{ocr_text[:2500]}

提取字段（无法确定则留空）：
- name: 姓名（2-4个中文字，通常在"姓名"字样后面）
- id_number: 公民身份号码（18位数字，末位可能为X）
- address: 住址（完整地址）
- gender: 性别（男/女）
- birth_date: 出生日期（YYYY-MM-DD，通常紧挨姓名下方）

直接返回JSON。"""

    try:
        messages = [
            {"role": "system", "content": "你是身份证信息提取专家。请精确提取。"},
            {"role": "user", "content": prompt},
        ]
        response = await ai_adapter.chat_completion(
            messages=messages, temperature=0.1, max_tokens=500,
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
    doc_type: str = "qualification",
) -> Dict[str, Any]:
    """Full pipeline: save → preprocess → multipass OCR → AI parse.

    Args:
        file_bytes: Raw image bytes.
        filename: Original filename.
        doc_type: "qualification" | "business_license" | "id_card".
    """
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
