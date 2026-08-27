"""宏曦标书 - 评分指标 API（评标办法驱动的目录补全 + 自我评分的数据源）.

数据模型见 docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §4。
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.project import BidProject
from app.models.user import User
from app.services.ai_adapter import ai_adapter
from app.services.score_engine import run_scoring
from app.services.scoring_rubric import extract_rubric, normalize_rubric, validate_rubric
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


class RubricParseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)


class RubricUpdateRequest(BaseModel):
    rubric: dict = Field(..., description="编辑/核对后的评分指标（§4.1 结构）")


async def _get_bid_project(project_id: str, db: AsyncSession) -> BidProject:
    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/scoring-rubric")
async def get_scoring_rubric(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取评分指标 + status（found/none/manual）."""
    project = await _get_bid_project(project_id, db)
    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        rubric = {}
    return {"rubric": rubric}


@router.post("/{project_id}/scoring-rubric/parse")
async def parse_scoring_rubric(
    project_id: str,
    data: RubricParseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动粘贴评标办法文本 → AI 提取指标 → 落库（status="found"，失败则 "none"）."""
    project = await _get_bid_project(project_id, db)
    rubric = await extract_rubric(data.text, ai_adapter)
    rubric = normalize_rubric(rubric)  # 最后一次归一，保证结构完整
    project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)
    await db.commit()
    return {"rubric": rubric, "problems": validate_rubric(rubric)}


@router.put("/{project_id}/scoring-rubric")
async def update_scoring_rubric(
    project_id: str,
    data: RubricUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存核对/编辑后的指标（status 置 "manual"）；items 变动时 applied 置 False，
    使目录补全进入可再次触发状态（幂等防重复补的撤销条件，见 spec §6.3）。"""
    project = await _get_bid_project(project_id, db)
    try:
        prev = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        prev = {}
    try:
        rubric = normalize_rubric(data.rubric, force_status="manual")
        if prev.get("items") != rubric.get("items"):
            rubric["applied"] = False
        problems = validate_rubric(rubric)
    except (ValueError, TypeError):
        # 用户手工编辑的数据未经 AI 归一，畸形分值（如 points="15 分"）会触发
        # normalize/validate 的 int() 转换异常 —— 一律按 400 业务错误返回，杜绝 500。
        raise HTTPException(status_code=400, detail="评分指标数据格式有误，请检查分值填写")
    project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)
    await db.commit()
    return {"rubric": rubric, "problems": problems}


@router.post("/{project_id}/score")
async def rescore_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动重评：读已落库内容（final_content||ai_generated_content + children 小节标题）
    重跑判卷，覆盖报告。与导出同一份数据（spec §7.2）。"""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        rubric = {}
    if rubric.get("status") not in ("found", "manual") or not rubric.get("items"):
        raise HTTPException(
            status_code=400,
            detail="该项目未检测到可用评分指标（状态 none），请先粘贴/编辑评分办法",
        )

    def _load_chapters() -> list[dict]:
        out = []
        for ch in sorted(project.chapters, key=lambda c: c.order_index):
            content = ch.final_content or ch.ai_generated_content or ""
            try:
                children = json.loads(ch.children_json or "[]")
            except json.JSONDecodeError:
                children = []
            titles: list[str] = []
            def _walk(nodes):
                for n in nodes or []:
                    titles.append(str(n.get("title") or ""))
                    _walk(n.get("children"))
            _walk(children)
            if titles:
                content = f"{content}\n\n小节：\n" + "\n".join(titles)
            out.append({"title": ch.title, "content": content})
        return out

    report = await run_scoring(rubric, _load_chapters(), ai_adapter)
    project.scoring_report_json = json.dumps(report, ensure_ascii=False)
    await db.commit()
    return report


@router.get("/{project_id}/scoring-report")
async def get_scoring_report(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取最新评分报告（重进项目展示）。"""
    project = await _get_bid_project(project_id, db)
    try:
        return json.loads(project.scoring_report_json or "{}")
    except json.JSONDecodeError:
        return {}
