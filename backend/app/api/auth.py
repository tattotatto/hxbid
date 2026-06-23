"""宏曦标书 - Authentication API Routes.

Provides register, login, and current-user endpoints.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.user import Token, UserCreate, UserLogin, UserRead
from app.utils.permissions import require_admin
from app.utils.security import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)

router = APIRouter()


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Register a new user account.

    The first registered user automatically becomes admin.
    Subsequent registrations require an existing admin to authorize.
    """
    # Check if this is the first user
    result = await db.execute(select(User).limit(1))
    is_first_user = result.scalar_one_or_none() is None

    if not is_first_user:
        # Require admin role for subsequent registrations
        from app.utils.permissions import require_admin
        # Re-verify as admin — the current_user dependency just confirms auth
        admin_check = await require_admin(current_user)
        if not admin_check:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can register new users",
            )

    existing = (
        await db.execute(select(User).where(User.username == data.username))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )
    user = User(
        username=data.username,
        password_hash=get_password_hash(data.password),
        display_name=data.display_name or data.username,
        role="admin" if is_first_user else "editor",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


@router.post("/login", response_model=Token)
async def login(
    data: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate and return a JWT access token."""
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is disabled",
        )
    token = create_access_token({"sub": user.id})
    return Token(access_token=token, user=UserRead.model_validate(user))


@router.get("/me", response_model=UserRead)
async def me(
    current_user: User = Depends(get_current_user),
):
    """Return the currently authenticated user."""
    return current_user
