"""宏曦标书 - Admin API Routes (User Management).

Admin-only endpoints for managing users, roles, and system configuration.
All routes require the 'admin' role.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate
from app.services.ai_adapter import ai_adapter
from app.utils.permissions import require_admin, require_editor

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /users  — List all users
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[UserRead])
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """List all registered users (admin only)."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()


# ---------------------------------------------------------------------------
# GET /users/{user_id}  — Get a single user
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}", response_model=UserRead)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Get a single user by ID (admin only)."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


# ---------------------------------------------------------------------------
# PUT /users/{user_id}  — Update user role / active status
# ---------------------------------------------------------------------------


@router.put("/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: str,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Update a user's display_name, role, or is_active status (admin only).

    An admin cannot change their own role or deactivate themselves.
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == admin.id:
        # Admins can't lock themselves out
        if data.role is not None and data.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change your own role",
            )
        if data.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deactivate yourself",
            )

    if data.role is not None and data.role not in ("admin", "editor", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be one of: admin, editor, viewer",
        )

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    await db.flush()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# DELETE /users/{user_id}  — Delete a user
# ---------------------------------------------------------------------------


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Delete a user (admin only). Cannot delete yourself."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself",
        )

    await db.delete(user)
    await db.flush()
    return {"message": "User deleted"}


# ---------------------------------------------------------------------------
# AI Model management endpoints
# ---------------------------------------------------------------------------


@router.get("/ai/providers")
async def list_providers(
    current_user: User = Depends(require_editor),
):
    """List available AI providers with their models and configured status."""
    return ai_adapter.list_providers()


@router.put("/ai/model")
async def set_model_override(
    data: dict,
    current_user: User = Depends(require_editor),
):
    """Update the AI model override at runtime.

    Request: {"model": "gpt-4.1"}  or  {"model": ""} to clear.
    """
    model = data.get("model", "")
    ai_adapter.set_model_override(model)
    return {
        "message": "Model override updated",
        "model": model or None,
        "effective_model": ai_adapter.get_model(),
    }


@router.post("/ai/test")
async def test_provider(
    data: dict,
    current_user: User = Depends(require_editor),
):
    """Test connectivity to an AI provider.

    Request: {"provider": "deepseek" | "openai" | "tongyi", "model": "custom-model" (optional)}
    """
    provider = data.get("provider", "deepseek")
    if provider not in ("deepseek", "openai", "tongyi"):
        return {"ok": False, "error": f"Unknown provider: {provider}"}
    custom_model = data.get("model")
    return await ai_adapter.test_connection(provider, model=custom_model)
