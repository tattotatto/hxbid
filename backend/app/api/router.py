"""宏曦标书 - API Router Aggregation.

Collects all sub-routers and exposes them under the single api_router.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from fastapi import APIRouter

from app.api.auth import router as auth_router

from app.api.personnel import router as personnel_router
from app.api.qualifications import router as qual_router
# from app.api.projects import router as projects_router
# from app.api.bid import router as bid_router

api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["认证"])
api_router.include_router(qual_router, prefix="/qualifications", tags=["资质管理"])
api_router.include_router(personnel_router, prefix="/personnel", tags=["人员管理"])
# api_router.include_router(projects_router, prefix="/projects", tags=["项目管理"])
# api_router.include_router(bid_router, prefix="/bid", tags=["AI生成"])
