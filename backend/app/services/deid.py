"""宏曦标书 - PII De-identification Service.

Replace personally identifiable information (PII) with placeholders before
content enters AI prompt context, and restore placeholders after AI generation.

Core design rule:
    - BEFORE AI prompt:   deidentify_text(text) -> (safe_text, mapping)
    - AFTER  AI response: reidentify_docx(text, mapping)   -> original_text

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import re
from typing import Dict, Tuple


def deidentify_text(text: str) -> Tuple[str, Dict[str, str]]:
    """Replace PII with numbered placeholders.

    Supported PII types:
        - Chinese ID card numbers (18 digits)
        - Mainland China mobile phone numbers (11 digits)
        - Chinese names (2-4 chars after specific context chars)
        - Certificate-like numbers

    Args:
        text: Raw text that may contain PII.

    Returns:
        Tuple of (deidentified_text, mapping) where mapping maps placeholders
        back to original values for later re-identification.
    """
    mapping: Dict[str, str] = {}
    counter: list[int] = [0]

    def replace(match: re.Match, prefix: str) -> str:
        counter[0] += 1
        placeholder = f"【{prefix}{counter[0]}】"
        mapping[placeholder] = match.group(0)
        return placeholder

    # ID card numbers (18 digits, last may be X)
    text = re.sub(
        r'\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b',
        lambda m: replace(m, 'ID'),
        text,
    )

    # Phone numbers (Mainland China mobile: 1[3-9]xxxxxxxxx)
    text = re.sub(
        r'\b1[3-9]\d{9}\b',
        lambda m: replace(m, 'PHONE'),
        text,
    )

    # Names (Chinese, 2-4 chars after specific context characters)
    text = re.sub(
        r'(?<=[：:。，,])[一-鿿]{2,4}(?=[同志先生女士，。、])',
        lambda m: replace(m, 'NAME'),
        text,
    )

    # Certificate-like numbers
    text = re.sub(
        r'\b[A-Z]{2,}[\d\-]{6,}\b',
        lambda m: replace(m, 'CERT'),
        text,
    )

    return text, mapping


def reidentify_docx(text: str, mapping: Dict[str, str]) -> str:
    """Restore placeholders with original PII values.

    Should be called on AI-generated content before rendering to docx.

    Args:
        text: AI-generated text containing placeholders like 【ID1】, 【PHONE2】.
        mapping: The mapping dict returned by deidentify_text().

    Returns:
        Text with all placeholders replaced by original PII values.
    """
    for placeholder, original in mapping.items():
        text = text.replace(placeholder, original)
    return text
