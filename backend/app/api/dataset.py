"""宏曦标书 - Dataset Export API Routes.

Endpoints for exporting fine-tuning datasets in standard formats.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.dataset_exporter import (
    fetch_training_samples,
    get_dataset_stats,
    to_alpaca_format,
    to_chatml_format,
    to_jsonl_format,
    to_sharegpt_format,
)
from app.utils.permissions import require_admin

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /dataset/stats
# ---------------------------------------------------------------------------


@router.get("/stats")
async def dataset_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Return statistics about available fine-tuning data."""
    return await get_dataset_stats(db)


# ---------------------------------------------------------------------------
# GET /dataset/export
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_dataset(
    format: str = Query("jsonl", description="Export format: jsonl, alpaca, sharegpt, chatml"),
    bid_result: str = Query("", description="Filter: won, lost, or empty for all"),
    min_edit_chars: int = Query(50, ge=0, le=1000),
    max_samples: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Export fine-tuning dataset in the specified format.

    Formats:
    - jsonl: OpenAI fine-tuning API format (one JSON per line)
    - alpaca: {instruction, input, output} JSON array
    - sharegpt: {conversations: [{from, value}]} JSON array
    - chatml: <|im_start|>...<|im_end|> plain text
    """
    bid_filter = bid_result if bid_result in ("won", "lost") else None

    samples = await fetch_training_samples(
        db=db,
        min_edit_chars=min_edit_chars,
        max_samples=max_samples,
        bid_result_filter=bid_filter,
    )

    if not samples:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No training samples found. Edit and finalize chapters first.",
        )

    import json

    if format == "jsonl":
        content = to_jsonl_format(samples)
        return PlainTextResponse(content, media_type="application/x-ndjson")
    elif format == "alpaca":
        data = to_alpaca_format(samples)
        return PlainTextResponse(
            json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json",
        )
    elif format == "sharegpt":
        data = to_sharegpt_format(samples)
        return PlainTextResponse(
            json.dumps(data, ensure_ascii=False, indent=2),
            media_type="application/json",
        )
    elif format == "chatml":
        content = to_chatml_format(samples)
        return PlainTextResponse(content, media_type="text/plain")
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown format: {format}. Use jsonl, alpaca, sharegpt, or chatml.",
        )


# ---------------------------------------------------------------------------
# GET /dataset/preview
# ---------------------------------------------------------------------------


@router.get("/preview")
async def preview_dataset(
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Preview a few training samples (human-readable)."""
    samples = await fetch_training_samples(
        db=db,
        min_edit_chars=20,
        max_samples=limit,
    )

    return [
        {
            "chapter_title": s.chapter_title,
            "project_name": s.project_name,
            "bid_result": s.bid_result,
            "input_preview": s.input_text[:200] + "…" if len(s.input_text) > 200 else s.input_text,
            "output_preview": s.output_text[:200] + "…" if len(s.output_text) > 200 else s.output_text,
            "edit_score": s.edit_score,
            "quality_score": s.quality_score,
        }
        for s in samples
    ]
