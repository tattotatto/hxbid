"""宏曦标书 - Template CRUD API Routes.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.template import BidTemplate
from app.models.user import User
from app.schemas.template import TemplateCreate, TemplateRead, TemplateUpdate
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# Default style config matching render_engine.DEFAULT_STYLE
DEFAULT_STYLE_CONFIG = {
    "body_font_name": "宋体",
    "body_font_size_pt": 12,
    "body_line_spacing": 1.5,
    "heading1_font_name": "黑体",
    "heading1_font_size_pt": 16,
    "heading2_font_name": "黑体",
    "heading2_font_size_pt": 14,
    "margin_top_cm": 2.54,
    "margin_bottom_cm": 2.54,
    "margin_left_cm": 3.17,
    "margin_right_cm": 3.17,
    "header_text": "云南宏曦科技有限公司",
    "footer_text": "第 X 页 / 共 Y 页",
}


async def _ensure_default_template(db: AsyncSession):
    """Create the default template if no templates exist."""
    result = await db.execute(select(BidTemplate).limit(1))
    if result.scalar_one_or_none() is None:
        default = BidTemplate(
            name="国标默认模板",
            description="正文宋体小四 1.5倍行距，标题黑体，页边距国标。适用于大部分保安/物业服务类标书。",
            style_config_json=json.dumps(DEFAULT_STYLE_CONFIG, ensure_ascii=False),
            is_default=True,
        )
        db.add(default)
        await db.flush()
        logger.info("Seeded default bid template")


# ---------------------------------------------------------------------------
# GET /  — List all templates
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[TemplateRead])
async def list_templates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all available bid-document templates, ordered by creation date."""
    await _ensure_default_template(db)
    result = await db.execute(
        select(BidTemplate).order_by(BidTemplate.created_at.desc())
    )
    templates = result.scalars().all()
    return [
        TemplateRead(
            id=t.id,
            name=t.name,
            description=t.description,
            style_config=json.loads(t.style_config_json) if t.style_config_json else {},
            is_default=t.is_default,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in templates
    ]


# ---------------------------------------------------------------------------
# POST /  — Create a new template
# ---------------------------------------------------------------------------


@router.post("/", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(
    data: TemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new bid-document style template."""
    # If this is the first template, make it default
    result = await db.execute(select(BidTemplate).limit(1))
    is_first = result.scalar_one_or_none() is None

    template = BidTemplate(
        name=data.name,
        description=data.description,
        style_config_json=json.dumps(data.style_config, ensure_ascii=False),
        is_default=is_first or data.style_config.get("__set_default", False),
    )
    db.add(template)
    await db.flush()
    await db.refresh(template)

    return TemplateRead(
        id=template.id,
        name=template.name,
        description=template.description,
        style_config=json.loads(template.style_config_json) if template.style_config_json else {},
        is_default=template.is_default,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


# ---------------------------------------------------------------------------
# GET /{template_id}  — Get one template
# ---------------------------------------------------------------------------


@router.get("/{template_id}", response_model=TemplateRead)
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a single template by ID."""
    template = await db.get(BidTemplate, template_id)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return TemplateRead(
        id=template.id,
        name=template.name,
        description=template.description,
        style_config=json.loads(template.style_config_json) if template.style_config_json else {},
        is_default=template.is_default,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


# ---------------------------------------------------------------------------
# PUT /{template_id}  — Update a template
# ---------------------------------------------------------------------------


@router.put("/{template_id}", response_model=TemplateRead)
async def update_template(
    template_id: str,
    data: TemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a template's fields."""
    template = await db.get(BidTemplate, template_id)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    update_data = data.model_dump(exclude_unset=True)

    # Handle style_config separately — serialize to JSON
    if "style_config" in update_data:
        template.style_config_json = json.dumps(
            update_data.pop("style_config"), ensure_ascii=False
        )

    # Handle is_default — un-set other defaults
    if update_data.get("is_default"):
        await db.execute(
            update(BidTemplate).values(is_default=False)
        )

    for field, value in update_data.items():
        setattr(template, field, value)

    await db.flush()
    await db.refresh(template)

    return TemplateRead(
        id=template.id,
        name=template.name,
        description=template.description,
        style_config=json.loads(template.style_config_json) if template.style_config_json else {},
        is_default=template.is_default,
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


# ---------------------------------------------------------------------------
# DELETE /{template_id}  — Delete a template
# ---------------------------------------------------------------------------


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a template. The default template cannot be deleted."""
    template = await db.get(BidTemplate, template_id)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    if template.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the default template. Set another template as default first.",
        )

    await db.delete(template)
    await db.flush()
    return {"message": "Template deleted"}
