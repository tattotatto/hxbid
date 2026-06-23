"""宏曦标书 - Role-Based Permission Dependencies.

FastAPI dependency functions that enforce role-based access control
on top of JWT authentication.

Usage::

    @router.delete("/{id}")
    async def delete_resource(
        id: str,
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(require_admin),
    ):
        ...

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import Depends, HTTPException, status

from app.models.user import User
from app.utils.security import get_current_user

# Role hierarchy (from highest to lowest privilege)
ROLE_HIERARCHY = {
    "admin": 3,
    "editor": 2,
    "viewer": 1,
}


def _require_role(min_role_name: str):
    """Factory: create a FastAPI dependency that requires at least *min_role_name*.

    Args:
        min_role_name: One of 'admin', 'editor', 'viewer'.

    Returns:
        An async dependency callable that returns the authenticated User
        if their role meets the minimum requirement, or raises 403.
    """
    min_level = ROLE_HIERARCHY.get(min_role_name, 0)

    async def dependency(
        current_user: User = Depends(get_current_user),
    ) -> User:
        if not current_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is disabled",
            )
        user_level = ROLE_HIERARCHY.get(current_user.role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {min_role_name} role or higher",
            )
        return current_user

    return dependency


# Convenience dependencies — import these directly in route modules.
require_admin = _require_role("admin")
require_editor = _require_role("editor")
require_viewer = _require_role("viewer")
