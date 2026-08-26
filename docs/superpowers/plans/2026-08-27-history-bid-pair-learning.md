# 历史标书配对学习 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将资源库「历史标书」改为「招标文件 + 标书」成对上传,上传后自动分析招标文件、对比标书,产出结构化学习报告并索引进 RAG 供生成时参考。

**Architecture:** 新增 `bid_lessons` 表存储配对与学习结果;新增 `bid_learning.py` 服务承载异步分析流水线(分析招标 → 对比标书 → 生成报告 → 对齐索引进向量库);新增 `/bid-lessons` API 路由;`rag.py` 增加按相似招标要求召回已学习对齐对的检索;生成管线 `ai_pipeline` 把学习参考并入 `reference_sections`;前端 `HistoryBids.tsx` 改写为双文件上传 + 状态列表 + 报告查看。

**Tech Stack:** FastAPI(Async SQLAlchemy)、python-docx、pdfplumber、ChromaDB vector_store、ai_adapter(deepseek-v4-pro)、React + antd + axios。

**Spec:** `docs/superpowers/specs/2026-08-27-history-bid-pair-learning-design.md`

## Global Constraints

- 生产模型为 deepseek-v4-pro(推理模型),max_tokens 会被 reasoning_tokens 先行消耗——所有 AI 调用必须给足 max_tokens(≥8192),参照 ai_adapter 空响应防御(ai_adapter.py:154 起)。
- 大文本入模前必须截断:沿用 `ai_pipeline.MAX_INPUT_CHARS = 15000`。
- 异步分析必须排队串行(并发 1),避免打爆 token 预算。
- 复用现有 `parse_document`、`parse_bid_requirements`、`format_extractor.extract_format_from_document`、`vector_store.index_chapter`,不重复造轮子。
- 后端改 app 代码后部署需重建镜像(`docker compose up -d --build backend`)。
- 老的单文件历史条目不展示但保留数据(列表过滤,不删除)。

---

### Task 1: BidLesson 数据模型 + Alembic 迁移 + 注册

**Files:**
- Create: `backend/app/models/bid_lesson.py`
- Modify: `backend/app/models/__init__.py`(注册导出)
- Create: `backend/alembic/versions/20260827_0006_add_bid_lesson.py`
- Test: `backend/tests/test_bid_lesson_model.py`

**Interfaces:**
- Consumes: `app.database.Base`、`users`/`bid_projects` 外键约定(参考 `project.py`)。
- Produces: ORM 类 `BidLesson`,字段:`id,name,source_type,project_id,tender_path,bid_path,status,error,lesson_json,created_by,created_at,updated_at`。后续 Task 2/3 依赖这些列名。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_bid_lesson_model.py`:

```python
"""BidLesson 模型列定义冒烟测试(无需 DB,只校验元数据)。"""

from app.models.bid_lesson import BidLesson


def test_bid_lesson_required_columns():
    cols = {c.name for c in BidLesson.__table__.columns}
    expected = {
        "id", "name", "source_type", "project_id",
        "tender_path", "bid_path", "status", "error",
        "lesson_json", "created_by", "created_at", "updated_at",
    }
    assert expected.issubset(cols)


def test_bid_lesson_defaults():
    assert BidLesson.__table__.c.status.default.arg == "pending"
    assert BidLesson.__table__.c.source_type.default.arg == "upload"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/test_bid_lesson_model.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'app.models.bid_lesson'`

- [ ] **Step 3: 创建模型**

`backend/app/models/bid_lesson.py`:

```python
"""宏曦标书 - 历史标书配对学习 Model."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BidLesson(Base):
    """历史标书「招标 + 标书」配对及其学习报告."""

    __tablename__ = "bid_lessons"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="upload"
    )  # upload | project
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bid_projects.id", ondelete="SET NULL"), nullable=True
    )
    tender_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    bid_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lesson_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<BidLesson(id={self.id!r}, name={self.name!r}, status={self.status!r})>"
```

- [ ] **Step 4: 在 models/__init__.py 注册**

在 `backend/app/models/__init__.py` 顶部 import 区加 `from app.models.bid_lesson import BidLesson`,并加入 `__all__`。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/test_bid_lesson_model.py -v`
Expected: PASS(2 passed)

- [ ] **Step 6: 创建 Alembic 迁移**

`backend/alembic/versions/20260827_0006_add_bid_lesson.py`(仿照 `20260813_0005_add_target_pages.py` 结构):

```python
"""add bid_lessons table

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bid_lessons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False, server_default="upload"),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("bid_projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tender_path", sa.String(500), nullable=False, server_default=""),
        sa.Column("bid_path", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("lesson_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("bid_lessons")
```

- [ ] **Step 7: 提交**

```bash
git add backend/app/models/bid_lesson.py backend/app/models/__init__.py backend/alembic/versions/20260827_0006_add_bid_lesson.py backend/tests/test_bid_lesson_model.py
git commit -m "feat: bid_lessons 表 + 迁移 + 模型注册"
```

---

### Task 2: `bid_learning.py` 分析服务

**Files:**
- Create: `backend/app/services/bid_learning.py`
- Test: `backend/tests/test_bid_learning.py`

**Interfaces:**
- Consumes: `app.database.async_session`、`app.models.bid_lesson.BidLesson`、`app.models.project.BidProject, ProjectChapter`、`app.services.document_parser.parse_document(file_path) -> str`、`app.services.ai_pipeline.parse_bid_requirements(document_text) -> dict`、`app.services.format_extractor.extract_format_from_document(full_text, ai_adapter) -> dict|None`、`app.services.ai_adapter.ai_adapter.chat_completion(messages, temperature=, max_tokens=, response_format={"type":"json_object"}) -> str`、`app.services.vector_store.vector_store.index_chapter(chapter_id, project_id, title, content, metadata) -> bool`。
- Produces:
  - `_truncate(text, limit=12000) -> str`
  - `_build_alignment_chunks(pair_id, name, lesson) -> list[dict]`(纯函数,供测试)
  - `index_lesson_alignment(pair_id, name, lesson) -> int`
  - `_assemble_project_bid_text(db, project_id) -> str`
  - `run_analysis(pair_id) -> None`(异步编排,串行信号量 `_analysis_sem = asyncio.Semaphore(1)`)
  - 模块级 `_analysis_sem`

- [ ] **Step 1: 写失败测试(纯函数)**

创建 `backend/tests/test_bid_learning.py`:

```python
"""bid_learning 纯函数测试:对齐 chunk 构建与截断。"""

from app.services.bid_learning import _build_alignment_chunks, _truncate


def test_truncate_respects_limit():
    text = "a" * 5000
    assert len(_truncate(text, 1000)) == 1000


def test_build_alignment_chunks_skips_missing():
    lesson = {
        "requirements_coverage": [
            {
                "requirement": "须有保安服务许可证",
                "category": "qualification",
                "bid_response": "我们持有保安服务许可证",
                "quality": "good",
                "lesson": "把资质放在资格审查醒目位置",
            },
            {"requirement": "须提供近三年业绩", "category": "qualification", "bid_response": "", "quality": "missing", "lesson": ""},
        ]
    }
    chunks = _build_alignment_chunks("p1", "某项目标书", lesson)
    assert len(chunks) == 1
    c = chunks[0]
    assert c["metadata"]["source"] == "lesson"
    assert c["metadata"]["pair_id"] == "p1"
    assert "保安服务许可证" in c["content"]
    assert c["title"].startswith("招标要求：")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/test_bid_learning.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'app.services.bid_learning'`

- [ ] **Step 3: 实现 `bid_learning.py`**

```python
"""宏曦标书 - 历史标书配对学习分析服务.

流程:读取招标文件+标书 → 分析招标文件(需求+格式) → 对比标书产学习报告 →
存 lesson_json → 把「要求→应答」对齐对索引进向量库。
"""

import asyncio
import json
import logging

from app.database import async_session
from app.models.bid_lesson import BidLesson
from app.models.project import BidProject, ProjectChapter
from app.services.ai_adapter import ai_adapter
from app.services.ai_pipeline import parse_bid_requirements
from app.services.document_parser import parse_document
from app.services.format_extractor import extract_format_from_document
from app.services.vector_store import vector_store
from sqlalchemy import select

logger = logging.getLogger(__name__)

# 串行信号量:一次只分析一个配对,避免瞬时打爆 AI token 预算
_analysis_sem = asyncio.Semaphore(1)

# 对比输入上限:招标分析 + 标书正文都要截断
_COMPARE_INPUT_LIMIT = 15000
_BID_TEXT_LIMIT = 12000


def _truncate(text: str, limit: int = 12000) -> str:
    if text is None:
        return ""
    return text[:limit]


async def _analyze_tender(tender_text: str) -> dict:
    """分析招标文件:需求(复用 parse_bid_requirements)+ 格式(复用 extract_format_from_document)."""
    requirements = await parse_bid_requirements(tender_text)
    format_template = None
    try:
        format_template = await extract_format_from_document(tender_text, ai_adapter)
    except Exception as e:
        logger.warning("format extraction skipped: %s", e)
    return {"requirements": requirements, "format_template": format_template}


async def _compare_bid(tender_analysis: dict, bid_text: str) -> dict:
    """对比标书:招标要求逐条 → 标书应答 → 质量判定 + 写法要点."""
    requirements = tender_analysis.get("requirements", {})
    format_template = tender_analysis.get("format_template") or {}
    bid_text = _truncate(bid_text, _BID_TEXT_LIMIT)

    user_prompt = f"""你是资深投标专家。请对比「招标文件要求」与「已中标的标书文本」,
学习标书应该怎么写,并以JSON返回结构化结果。

招标文件要求(JSON):
{json.dumps(requirements, ensure_ascii=False)[:6000]}

招标文件格式模板(JSON,可为空):
{json.dumps(format_template, ensure_ascii=False)[:6000]}

标书正文:
{bid_text}

返回JSON结构(直接返回JSON对象,不要任何额外文字):
{{
  "tender_meta": {{"project_name": "项目名", "summary": "对招标文件要点的概括"}},
  "requirements_coverage": [
    {{
      "requirement": "招标文件的具体要求原文",
      "category": "qualification|technical|commercial|format",
      "bid_response": "标书中对应的应答内容(原文摘录,可空)",
      "how_addressed": "标书是怎么组织来应答这条的(如放在资格审查、用表格、用承诺等)",
      "quality": "good|partial|missing",
      "lesson": "这条要求应该怎么写/放哪,一句话写法要点"
    }}
  ],
  "structure_mapping": [
    {{"tender_section": "招标文件要求的章节", "bid_section": "标书对应章节", "format_ok": true, "notes": "说明"}}
  ],
  "writing_style": {{"overall": "总体行文风格", "patterns": ["惯用句式/结构"], "strengths": ["值得借鉴的优点"]}},
  "lessons": ["标书应该怎么写的关键要点(3-6条)"]
}}

要求:
- requirements_coverage 覆盖招标文件所有明确要求(资质/人员/业绩/技术/商务/格式)。
- bid_response 只摘录标书原文,缺失时为空字符串。
- quality=missing 时 lesson 简述为什么缺失、应怎么补。
- 所有字段必须存在,未提及用空字符串或空数组。"""

    messages = [
        {"role": "system", "content": "你是严谨的投标文件撰写专家。"},
        {"role": "user", "content": user_prompt},
    ]
    response = await ai_adapter.chat_completion(
        messages=messages,
        temperature=0.3,
        max_tokens=8192,
        response_format={"type": "json_object"},
    )
    try:
        result = json.loads(response)
    except json.JSONDecodeError:
        return {
            "tender_meta": {},
            "requirements_coverage": [],
            "structure_mapping": [],
            "writing_style": {},
            "lessons": [],
        }
    for key in (
        "tender_meta", "requirements_coverage",
        "structure_mapping", "writing_style", "lessons",
    ):
        result.setdefault(key, {} if key in ("tender_meta", "writing_style") else [])
    return result


def _build_alignment_chunks(pair_id: str, name: str, lesson: dict) -> list[dict]:
    """把 requirements_coverage 转成 RAG 对齐片段.纯函数.

    每个 quality != missing 且有应答的条目 → 一个 chunk,
    metadata 打 source=lesson,便于生成时按相似要求召回。
    """
    chunks = []
    for item in lesson.get("requirements_coverage", []) or []:
        req = (item.get("requirement") or "").strip()
        resp = (item.get("bid_response") or "").strip()
        if not req or not resp or item.get("quality") == "missing":
            continue
        lesson_tip = (item.get("lesson") or "").strip()
        content = f"招标要求：{req}\n标书应答：{resp}"
        if lesson_tip:
            content += f"\n写法要点：{lesson_tip}"
        chunks.append({
            "title": f"招标要求：{req[:60]} → 标书应答",
            "content": content,
            "metadata": {
                "source": "lesson",
                "pair_id": pair_id,
                "pair_name": name,
                "category": item.get("category", ""),
            },
        })
    return chunks


def index_lesson_alignment(pair_id: str, name: str, lesson: dict) -> int:
    """把对齐片段索引进向量库,返回成功条数."""
    if not vector_store.is_available():
        return 0
    chunks = _build_alignment_chunks(pair_id, name, lesson)
    indexed = 0
    for i, chunk in enumerate(chunks):
        ok = vector_store.index_chapter(
            chapter_id=f"{pair_id}_lesson_{i}",
            project_id=pair_id,
            title=chunk["title"],
            content=chunk["content"],
            metadata=chunk["metadata"],
        )
        if ok:
            indexed += 1
    return indexed


async def _assemble_project_bid_text(db, project_id: str) -> str:
    """系统项目的标书文本:由章节最终内容组装."""
    rows = await db.execute(
        select(ProjectChapter)
        .where(ProjectChapter.project_id == project_id)
        .order_by(ProjectChapter.order_index)
    )
    parts = []
    for ch in rows.scalars().all():
        body = ch.final_content or ch.ai_generated_content or ""
        if body.strip():
            parts.append(f"{ch.title}\n{body}")
    return "\n\n".join(parts)


def _read_document_text(path: str) -> str:
    if not path:
        return ""
    from pathlib import Path
    p = Path(path)
    if not p.is_file():
        return ""
    return parse_document(str(p))


async def run_analysis(pair_id: str) -> None:
    """异步编排:分析招标→对比标书→存报告→索引进向量库.串行执行."""
    async with _analysis_sem:
        async with async_session() as db:
            pair = await db.get(BidLesson, pair_id)
            if not pair:
                return
            pair.status = "analyzing"
            pair.error = ""
            await db.commit()
            try:
                # 招标文件文本
                tender_text = _read_document_text(pair.tender_path)
                if not tender_text.strip():
                    raise ValueError("招标文件解析为空")
                # 标书文本:上传对用文件,系统项目用章节组装
                if pair.source_type == "project" and not pair.bid_path:
                    bid_text = await _assemble_project_bid_text(db, pair.project_id)
                else:
                    bid_text = _read_document_text(pair.bid_path)
                if not bid_text.strip():
                    raise ValueError("标书解析为空")

                tender_analysis = await _analyze_tender(_truncate(tender_text, _COMPARE_INPUT_LIMIT))
                lesson = await _compare_bid(tender_analysis, bid_text)
                pair.lesson_json = json.dumps(lesson, ensure_ascii=False)
                pair.status = "ready"
                await db.commit()

                indexed = index_lesson_alignment(pair.id, pair.name, lesson)
                logger.info("lesson %s ready, indexed %d chunks", pair.id, indexed)
            except Exception as exc:  # noqa: BLE001
                logger.exception("lesson analysis failed for %s", pair_id)
                pair.status = "failed"
                pair.error = str(exc)
                await db.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/test_bid_learning.py -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/bid_learning.py backend/tests/test_bid_learning.py
git commit -m "feat: bid_learning 分析服务 — 分析招标/对比标书/对齐索引"
```

---

### Task 3: `/bid-lessons` API 路由

**Files:**
- Create: `backend/app/api/bid_lesson.py`
- Modify: `backend/app/api/router.py`(注册路由)
- Modify: `backend/app/api/bid.py`(清理旧 upload-history 前端引用,后端接口保留)
- Test: `backend/tests/test_bid_lesson_api.py`(导入与路由注册 sanity)

**Interfaces:**
- Consumes: `app.utils.security.get_current_user`、`app.utils.permissions.require_editor`、`app.database.get_db`、`BidLesson`、`BidProject`、`bid_learning.run_analysis/_assemble_project_bid_text`、`settings.UPLOAD_DIR`。
- Produces:
  - `POST /bid-lessons/upload` — form: `tender_file`, `bid_file`, `name`(可选)→ `{id, name, status}`
  - `GET /bid-lessons` → `{items: [{id,name,source_type,status,error,created_at,project_name}], system_lessons: int}`
  - `GET /bid-lessons/{id}` → `{id,name,source_type,status,error,lesson,created_at}`
  - `POST /bid-lessons/{id}/relearn` → 重新触发分析
  - `DELETE /bid-lessons/{id}` → `{ok: true}`

- [ ] **Step 1: 写失败测试(导入 + 路由注册)**

创建 `backend/tests/test_bid_lesson_api.py`:

```python
"""bid-lessons 路由注册 sanity 测试."""

from app.api.router import api_router


def test_bid_lessons_routes_registered():
    paths = {route.path for route in api_router.routes}
    assert "/bid-lessons/upload" in paths
    assert "/bid-lessons/{id}" in paths
    assert "/bid-lessons/{id}/relearn" in paths
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/test_bid_lesson_api.py -v`
Expected: FAIL(路由不存在)

- [ ] **Step 3: 实现路由**

`backend/app/api/bid_lesson.py`:

```python
"""宏曦标书 - 历史标书配对学习 API."""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.bid_lesson import BidLesson
from app.models.project import BidProject
from app.services.bid_learning import run_analysis
from app.utils.permissions import require_editor
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()

_RELEVANT_STATUSES = ("won", "lost", "exported", "archived")


def _save_upload(upload_file: UploadFile) -> str:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(upload_file.filename).suffix if upload_file.filename else ".tmp"
    saved = upload_dir / f"{uuid.uuid4().hex}{ext}"
    with open(saved, "wb") as f:
        f.write(upload_file.file.read())
    return str(saved.absolute())


@router.post("/upload")
async def upload_pair(
    tender_file: UploadFile = File(...),
    bid_file: UploadFile = File(...),
    name: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_editor),
):
    """上传「招标文件 + 标书」配对,自动启动分析."""
    if not tender_file.filename or not bid_file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须同时上传招标文件和标书")
    tender_path = _save_upload(tender_file)
    bid_path = _save_upload(bid_file)
    pair = BidLesson(
        name=name or Path(tender_file.filename).stem,
        source_type="upload",
        tender_path=tender_path,
        bid_path=bid_path,
        status="pending",
        created_by=current_user.id,
    )
    db.add(pair)
    await db.flush()
    await db.commit()
    asyncio.create_task(run_analysis(pair.id))
    return {"id": pair.id, "name": pair.name, "status": pair.status}


@router.get("")
async def list_lessons(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """列出历史标书配对(含系统完成项目,懒创建配对并自动分析)."""
    lessons = (await db.execute(select(BidLesson).order_by(BidLesson.created_at.desc()))).scalars().all()

    # 系统完成项目 → 懒创建配对
    projects = (await db.execute(
        select(BidProject).where(BidProject.status.in_(_RELEVANT_STATUSES))
    )).scalars().all()
    existing = {l.project_id for l in lessons if l.source_type == "project" and l.project_id}
    created = []
    for p in projects:
        if p.id in existing:
            continue
        if not p.original_file_path:
            continue  # 没有招标文件的项目不纳入
        pair = BidLesson(
            name=p.name,
            source_type="project",
            project_id=p.id,
            tender_path=p.original_file_path,
            status="pending",
        )
        db.add(pair)
        created.append(pair)
    if created:
        await db.flush()
        await db.commit()
        for pair in created:
            asyncio.create_task(run_analysis(pair.id))
        lessons = lessons + created

    items = [
        {
            "id": l.id,
            "name": l.name,
            "source_type": l.source_type,
            "status": l.status,
            "error": l.error,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in lessons
    ]
    return {"items": items, "system_lessons": len(created)}


@router.get("/{pair_id}")
async def get_lesson(
    pair_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    pair = await db.get(BidLesson, pair_id)
    if not pair:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pair not found")
    try:
        lesson = json.loads(pair.lesson_json)
    except json.JSONDecodeError:
        lesson = {}
    return {
        "id": pair.id,
        "name": pair.name,
        "source_type": pair.source_type,
        "status": pair.status,
        "error": pair.error,
        "lesson": lesson,
        "created_at": pair.created_at.isoformat() if pair.created_at else None,
    }


@router.post("/{pair_id}/relearn")
async def relearn_pair(
    pair_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_editor),
):
    pair = await db.get(BidLesson, pair_id)
    if not pair:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pair not found")
    pair.status = "pending"
    pair.error = ""
    pair.lesson_json = "{}"
    await db.commit()
    asyncio.create_task(run_analysis(pair.id))
    return {"id": pair.id, "status": "pending"}


@router.delete("/{pair_id}")
async def delete_pair(
    pair_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_editor),
):
    pair = await db.get(BidLesson, pair_id)
    if not pair:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pair not found")
    from app.services.vector_store import vector_store
    vector_store.delete_project(pair_id)
    await db.delete(pair)
    await db.commit()
    return {"ok": True}
```

- [ ] **Step 4: 注册路由**

在 `backend/app/api/router.py` import 区加 `from app.api.bid_lesson import router as bid_lesson_router`,并在 `api_router` 加一行:

```python
api_router.include_router(bid_lesson_router, prefix="/bid-lessons", tags=["历史标书"])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/test_bid_lesson_api.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/bid_lesson.py backend/app/api/router.py backend/tests/test_bid_lesson_api.py
git commit -m "feat: /bid-lessons 配对上传/列表/详情/重学/删除 API"
```

---

### Task 4: RAG 消费 — 生成时召回学习参考

**Files:**
- Modify: `backend/app/services/rag.py`(新增 `retrieve_lesson_references`)
- Modify: `backend/app/services/ai_pipeline.py`(`_fetch_rag` 合并学习参考)
- Test: `backend/tests/test_rag_lesson.py`

**Interfaces:**
- Consumes: `app.services.vector_store.vector_store.search_similar(...)`、`ai_pipeline._fetch_rag` 结构。
- Produces:
  - `rag.retrieve_lesson_references(requirements: dict, project_id: str, n_results: int = 3) -> list[dict]`
    - 返回 `[{title, content, metadata}]`,metadata.source == "lesson"。

- [ ] **Step 1: 写失败测试(用假 vector_store 验证过滤与排序)**

创建 `backend/tests/test_rag_lesson.py`:

```python
"""retrieve_lesson_references 过滤逻辑测试(注入假 vector_store)."""

from unittest.mock import patch
from app.services import rag


class FakeStore:
    def is_available(self):
        return True

    def search_similar(self, query, n_results):
        return [
            {"title": "a", "content": "x", "distance": 0.3,
             "metadata": {"source": "lesson", "pair_id": "p1"}},
            {"title": "b", "content": "y", "distance": 0.5,
             "metadata": {"source": "history", "project_id": "proj1"}},
        ]


@patch("app.services.rag.vector_store", FakeStore())
def test_retrieve_lesson_references_filters_source():
    result = rag.retrieve_lesson_references({"service_requirements": ["保安服务"]}, "cur", n_results=5)
    assert all(r["metadata"].get("source") == "lesson" for r in result)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/test_rag_lesson.py -v`
Expected: FAIL(`retrieve_lesson_references` 未定义)

- [ ] **Step 3: 实现 `retrieve_lesson_references`**

在 `backend/app/services/rag.py` 末尾(在 `assemble_chapter_context` 之后)新增:

```python
async def retrieve_lesson_references(
    requirements: dict,
    project_id: str,
    n_results: int = 3,
) -> List[Dict[str, Any]]:
    """按相似招标要求召回已学习的「要求→应答」对齐对.

    从 requirements 提取关键词构建查询,只保留 source=lesson 的结果,
    供生成时把「这条要求当时是怎么应答的」注入参考。
    """
    if not vector_store.is_available():
        return []

    queries = _build_query_variants("投标要求应答写法", requirements)
    results: List[Dict[str, Any]] = []
    for q in queries:
        hits = vector_store.search_similar(q, n_results=n_results)
        for h in hits:
            md = h.get("metadata") or {}
            if md.get("source") != "lesson":
                continue
            if str(md.get("pair_id", "")) == str(project_id):
                continue  # 避免自召回
            results.append(h)
    # 按距离去重
    seen: set[str] = set()
    deduped = []
    for r in sorted(results, key=lambda x: x.get("distance", 1.0)):
        key = (r.get("metadata", {}).get("pair_id", ""), r.get("content", "")[:80])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped[:n_results]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/test_rag_lesson.py -v`
Expected: PASS

- [ ] **Step 5: 把学习参考并入生成**

在 `backend/app/services/ai_pipeline.py` 的 `_fetch_rag`(约 1912-1932 行)中,把学习参考追加到同一组结果:

```python
    async def _fetch_rag(leaf: dict) -> list:
        try:
            from app.services.rag import retrieve_lesson_references, retrieve_similar_chapters
            async with async_session() as rag_db:
                similar = await retrieve_similar_chapters(
                    leaf.get("title", ""), requirements, project_id, n_results=5
                )
                lessons = await retrieve_lesson_references(
                    requirements, project_id, n_results=3
                )
                refs = [s.get("content", "") for s in similar if s.get("content")]
                refs += [s.get("content", "") for s in lessons if s.get("content")]
                return refs
        except Exception:
            return []
```

注:实际编辑时,在现有 `_fetch_rag` 的 try 块内追加 lessons 召回与合并;保留原有异常吞掉逻辑。修改后 `_fetch_rag` 内部 `requirements`/`project_id` 沿用外层作用域变量。

- [ ] **Step 6: 全量回归 + 提交**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 全部通过(除既有的 `test_outline_format::test_format_template_to_prompt_text` 环境性失败,已在改前确认存在)

```bash
git add backend/app/services/rag.py backend/app/services/ai_pipeline.py backend/tests/test_rag_lesson.py
git commit -m "feat: 生成时召回 bid_lesson 学习参考"
```

---

### Task 5: 前端 `HistoryBids.tsx` 改写

**Files:**
- Rewrite: `frontend/src/pages/resources/HistoryBids.tsx`
- Modify: `frontend/src/api/client.ts`(无需改,直接用现有 client)

**Interfaces:**
- Consumes API:`POST /bid-lessons/upload`、`GET /bid-lessons`、`GET /bid-lessons/{id}`、`POST /bid-lessons/{id}/relearn`、`DELETE /bid-lessons/{id}`。
- Produces:可用的历史标书页面(双文件上传 + 状态列表 + 报告抽屉)。

- [ ] **Step 1: 写前端页面(tsc 类型检查为测试)**

整体重写 `frontend/src/pages/resources/HistoryBids.tsx`,要点:

1. **状态管理**:`list`(配对数组)、`loading`、`uploading`、`uploadProgress`、`reportOpen/reportData`、`pollingTimer`。
2. **上传弹窗**:两个 `Upload` 组件(招标文件 + 标书),各自 `beforeUpload` 记录文件名/文件对象、返回 false 阻止自动上传;两者都选且文件名非空才能点「上传并分析」;`customRequest` 或手动 XHR 把两个文件放同一 FormData 字段 `tender_file`/`bid_file`,POST 到 `/bid-lessons/upload`,带进度条。
3. **列表列**:`名称`、`来源`(上传/系统项目 Tag)、`状态`(分析中=spinner Tag、已学习=success、失败=error)、`创建时间`、`操作`(查看报告 / 重试 / 删除)。
4. **轮询**:列表加载后,若存在 status∈{pending,analyzing} 的项,用 `setInterval` 每 5s 重新 `GET /bid-lessons`,直到无进行中项(组件卸载清理 timer)。
5. **报告抽屉**:`Drawer` 展示 `lesson` JSON:`tender_meta.summary`、`requirements_coverage` 表格(要求/应答/质量/写法要点)、`structure_mapping` 表格、`writing_style`、`lessons` 要点列表。
6. **删除**:`Modal.confirm` 后 `DELETE`,成功后刷新列表。
7. **重试**:状态 failed 的行显示「重试」→ `POST /{id}/relearn`。

参考现有 `Contracts.tsx` 的 antd 用法与 `HistoryBids.tsx` 现有的 XHR 上传进度模式。上传成功文案:「已上传,正在分析招标文件并对比标书...」。

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npm run build`
Expected: `tsc -b && vite build` 通过,无类型错误。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/resources/HistoryBids.tsx
git commit -m "feat(ui): 历史标书改版 — 双文件上传 + 学习状态 + 报告抽屉"
```

---

### Task 6: e2e_smoke 扩展 + 端到端验证

**Files:**
- Modify: `backend/scripts/e2e_smoke.py`

**Interfaces:**
- Consumes:`POST /bid-lessons/upload`、`GET /bid-lessons`、`GET /bid-lessons/{id}`。
- Produces:新冒烟检查「历史标书配对学习」流程。

- [ ] **Step 1: 在 e2e_smoke.py 增加配对学习检查段**

在 `# ---- 报告 ----` 之前插入一段(仿照 docx 校验段的 try/except + checks.append 模式):

```python
    # ---- 历史标书配对学习 ----
    print("\n上传招标+标书配对并等待分析...")
    try:
        from io import BytesIO
        from docx import Document as DocxDocument

        # 生成一对小的 docx 作为测试素材
        def _make_docx(text: str) -> bytes:
            doc = DocxDocument()
            for line in text.split("\n"):
                doc.add_paragraph(line)
            buf = BytesIO()
            doc.save(buf)
            return buf.getvalue()

        tender_doc = _make_docx(
            "招标文件\n第一章 投标邀请\n项目名称：示例监控项目\n第二章 投标人须知\n"
            "资质要求：须持有保安服务许可证。\n技术需求：须具备 7×24 小时响应能力。\n"
            "投标文件组成：（一）开标一览表（二）投标函（三）法定代表人身份证明书\n"
            "评标办法：综合评分法\n"
        )
        bid_doc = _make_docx(
            "投标文件\n（一）开标一览表\n项目名称：示例监控项目\n（二）投标函\n我方承诺满足全部要求。\n"
            "（三）法定代表人身份证明书\n我方持有保安服务许可证。\n技术方案\n本方案提供 7×24 小时响应。\n"
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
            up = await client.post(
                f"{API_BASE}/bid-lessons/upload",
                headers=headers,
                files={
                    "tender_file": ("tender.docx", tender_doc, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                    "bid_file": ("bid.docx", bid_doc, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                },
                data={"name": "E2E-配对学习测试"},
            )
            up.raise_for_status()
            pair_id = up.json()["id"]
            checks.append(("配对上传成功", True))
            print(f"  - 配对 id: {pair_id}")

            # 轮询等待分析完成(最多 5 分钟)
            lesson_ok = False
            for _ in range(60):
                await asyncio.sleep(5)
                detail = await client.get(f"{API_BASE}/bid-lessons/{pair_id}", headers=headers)
                detail.raise_for_status()
                data = detail.json()
                if data["status"] == "ready":
                    lesson = data.get("lesson") or {}
                    lesson_ok = bool(lesson.get("requirements_coverage")) and bool(lesson.get("lessons"))
                    break
                if data["status"] == "failed":
                    print(f"  - 配对分析失败: {data.get('error')}")
                    break
            checks.append(("配对学习报告 ready 且含需求覆盖/要点", lesson_ok))
            print(f"  - 配对分析状态: {'ready' if lesson_ok else '未完成'}")
    except Exception as exc:
        print(f"  ⚠️  配对学习校验异常: {exc}")
        checks.append(("历史标书配对学习", False))
```

- [ ] **Step 2: 语法检查**

Run: `cd backend && ./venv/Scripts/python.exe -m py_compile scripts/e2e_smoke.py`
Expected: 无错误

- [ ] **Step 3: 提交**

```bash
git add backend/scripts/e2e_smoke.py
git commit -m "test(e2e): 历史标书配对上传与学习报告冒烟检查"
```

---

### Task 7: 最终验证与收尾

- [ ] **Step 1: 全量后端单测**

Run: `cd backend && PYTHONPATH=/d/2026/投标软件/backend ./venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 除既有的 `test_outline_format::test_format_template_to_prompt_text`(改前已确认的环境性失败)外全部通过。

- [ ] **Step 2: 前端构建**

Run: `cd frontend && npm run build`
Expected: 通过。

- [ ] **Step 3: 复核设计覆盖**

对照 `docs/superpowers/specs/2026-08-27-history-bid-pair-learning-design.md` 逐项确认:
- 数据模型 ✓(Task 1)
- 分析流水线(分析招标→对比标书→报告+索引)✓(Task 2)
- RAG 消费 ✓(Task 4)
- API + 前端 ✓(Task 3/5)
- 测试与 e2e ✓(Task 6)

- [ ] **Step 4: 提交剩余**

```bash
git add -A
git commit -m "chore: 历史标书配对学习收尾"
```

## 自审结论

- **Spec 覆盖**:设计文档 5 节均有对应任务(Task1 模型 / Task2 流水线 / Task3+5 API+前端 / Task4 RAG / Task6+7 测试)。
- **占位符**:所有代码步骤均为完整可执行代码,无 TBD。
- **类型一致性**:`BidLesson` 列名在 Task1/2/3 间一致;`retrieve_lesson_references` 在 Task4 Step3 定义、Step5 引用,签名一致;`run_analysis`/`_assemble_project_bid_text`/`_build_alignment_chunks` 在 Task2 定义、Task3 引用一致。
