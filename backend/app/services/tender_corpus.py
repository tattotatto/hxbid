"""宏曦标书 - 招标全文语料的落盘与读取.

章节匹配器需要全文作为「格式章节里找不到」时的兜底语料，而全文此前只在
upload-and-parse 请求内存在。落成文件而不是新增 DB 列：全文可达数百 KB～
数 MB，塞进被频繁读取的 parsed_requirements_json 会拖慢所有读该字段的路径。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SUBDIR = "parsed"


def persist_full_text(upload_dir: str, stem: str, text: str) -> str | None:
    """把全文写到 ``{upload_dir}/parsed/{stem}.txt``，返回**相对**路径.

    失败只记 warning 并返回 None —— 全文兜底是可选增强，不能阻断上传解析。
    """
    if not text or not text.strip() or not stem:
        return None
    try:
        target_dir = Path(upload_dir) / _SUBDIR
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{stem}.txt").write_text(text, encoding="utf-8")
        return f"{_SUBDIR}/{stem}.txt"
    except (OSError, ValueError) as exc:
        logger.warning("Failed to persist full text (%s): %s", stem, exc)
        return None


def load_full_text(requirements: dict, upload_dir: str) -> str | None:
    """读回全文；没有、越界或读失败都返回 None（调用方据此跳过全文兜底）."""
    rel = (requirements or {}).get("full_text_path")
    if not rel:
        return None
    try:
        root = Path(upload_dir).resolve()
        resolved = (root / rel).resolve()
        # is_relative_to 而不是 startswith：后者会把 `…/uploads_evil/x`
        # 这类同前缀的兄弟目录误判为通过
        if not resolved.is_relative_to(root):
            logger.warning("Refused full-text path outside upload dir: %s", rel)
            return None
        if not resolved.is_file():
            return None
        return resolved.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        logger.warning("Failed to load full text (%s): %s", rel, exc)
        return None
