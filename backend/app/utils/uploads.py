"""宏曦标书 - Uploaded file path resolution.

Stored upload paths (DB columns like ``company.business_license_image``) are
**relative to ``settings.UPLOAD_DIR``** — e.g. ``company/xxx.png`` — because that
is what ``/uploads/{path}`` expects. They are NOT relative to the process cwd:
in the container cwd is ``/app`` while ``UPLOAD_DIR`` is ``/app/uploads``, so
opening a stored path directly silently misses the file.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from pathlib import Path

from app.config import settings


def resolve_upload_path(image_path: str) -> Path | None:
    """Resolve a stored upload path to an existing file, or None.

    Handles both path conventions found in the database:
    - current: relative to ``UPLOAD_DIR`` (``company/xxx.png``)
    - legacy: carries a leading ``uploads/`` prefix (``uploads/company/xxx.png``)

    Paths resolving outside ``UPLOAD_DIR`` are rejected — callers hand user
    supplied values to OCR, which would otherwise read any file on disk.
    """
    if not image_path:
        return None

    raw = str(image_path).replace("\\", "/")
    if raw.startswith("uploads/"):
        raw = raw[len("uploads/"):]

    root = Path(settings.UPLOAD_DIR).resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / raw

    real = candidate.resolve()
    try:
        real.relative_to(root)
    except ValueError:
        return None

    return real if real.is_file() else None
