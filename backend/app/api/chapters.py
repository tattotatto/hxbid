"""宏曦标书 - 章节提取、审核、对话编辑 API.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.project import BidProject, ProjectChapter
from app.models.user import User
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ExtractChaptersResponse(BaseModel):
    chapters: list = []
    health: dict = {}
    source_pages: list = []
    error: str = ""


class ChapterChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: str | None = None


class ChapterChatResponse(BaseModel):
    reply: str = ""
    chapters: list = []
    conversation_id: str = ""


class LockChaptersResponse(BaseModel):
    success: bool = False
    chapters_count: int = 0
    message: str = ""


# ---------------------------------------------------------------------------
# POST /{project_id}/extract-chapters
# ---------------------------------------------------------------------------

@router.post("/{project_id}/extract-chapters", response_model=ExtractChaptersResponse)
async def extract_chapters(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从招标文件 PDF 提取并解析投标文件章节结构.

    流程：
    1. 加载项目
    2. 从 PDF 定位第六章并提取文本
    3. 编码健康检查
    4. AI 解析为结构化章节列表
    5. 保存到 chapter_structure_json
    6. 返回章节列表给前端
    """
    # Load project
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not project.original_file_path:
        raise HTTPException(status_code=400, detail="未上传招标文件，请先上传")

    # Extract chapters from PDF
    try:
        from app.services.chapter_extractor import extract_chapters_from_pdf
        from app.services.ai_adapter import ai_adapter as ai

        extraction_result = await extract_chapters_from_pdf(
            pdf_path=project.original_file_path,
            ai_adapter=ai,
        )

        chapters = extraction_result["chapters"]
        health = extraction_result["health"]
        source_pages = extraction_result["source_pages"]

        # 兜底：AI 提取结果过少时，用格式模板 document_structure 补全必需章节
        from app.services.chapter_extractor import merge_format_template_fallback
        format_template = {}
        try:
            format_template = json.loads(project.format_template_json) if project.format_template_json else {}
        except json.JSONDecodeError:
            format_template = {}
        if len(chapters) < 3 and format_template.get("document_structure"):
            chapters, auto_added = merge_format_template_fallback(chapters, format_template)
            if auto_added:
                logger.info(
                    "Chapter extraction fallback: merged %d required chapters for project %s",
                    len(auto_added), project_id,
                )

        # Save to project
        project.chapter_structure_json = json.dumps(chapters, ensure_ascii=False)
        # 提取成功 → 进入「目录待确认」门。前端会跳到 /outline 让用户审阅/编辑/对话修改，
        # 用户在 outline 页面点击「确认并继续」才会物化 ProjectChapter 并推进到 collecting。
        project.status = "structure_ready"
        await db.commit()

        logger.info(
            "Extracted %d chapters for project %s (health: cjk_ratio=%.2f)",
            len(chapters), project_id, health.get("cjk_ratio", 0),
        )

        return ExtractChaptersResponse(
            chapters=chapters,
            health=health,
            source_pages=source_pages,
        )

    except ValueError as exc:
        # Encoding health failure — explicit rejection
        logger.warning("Chapter extraction rejected for project %s: %s", project_id, exc)
        return ExtractChaptersResponse(
            chapters=[],
            health={"healthy": False, "message": str(exc)},
            source_pages=[],
            error=str(exc),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="招标文件不存在，请重新上传")
    except Exception as exc:
        logger.exception("Chapter extraction failed for project %s", project_id)
        raise HTTPException(status_code=500, detail=f"章节提取失败: {exc}")


# ---------------------------------------------------------------------------
# POST /{project_id}/chapters/chat
# ---------------------------------------------------------------------------

@router.post("/{project_id}/chapters/chat", response_model=ChapterChatResponse)
async def chat_edit_chapters(
    project_id: str,
    data: ChapterChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对话式编辑章节结构.

    发送修改建议，AI 返回修改后的章节列表。
    支持多轮对话（传 conversation_id 保持上下文）。
    """
    # Load project
    result = await db.execute(
        select(BidProject).where(BidProject.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 「目录确认门」guard：仅在 structure_ready 状态允许对话修改顶层章节结构。
    # 锁定后由 outline/confirm 物化 ProjectChapter，章节结构进入 children_json 维度。
    if project.status != "structure_ready":
        raise HTTPException(
            status_code=400,
            detail=(
                f"当前项目状态 {project.status} 不允许编辑章节结构。"
                "请先确认目录（POST /outline/confirm）后再操作章节内容。"
            ),
        )

    # Get current chapters
    chapters_json = project.chapter_structure_json
    if not chapters_json or chapters_json in ("[]", "{}", ""):
        raise HTTPException(
            status_code=400,
            detail="请先提取章节（POST /extract-chapters）再进行编辑",
        )

    # Chat edit
    try:
        from app.services.chapter_chat import chat_edit_chapters as do_chat
        from app.services.ai_adapter import ai_adapter as ai

        result = await do_chat(
            chapters_json=chapters_json,
            user_message=data.message,
            conversation_id=data.conversation_id,
            ai_adapter=ai,
        )

        # Save updated chapters
        project.chapter_structure_json = json.dumps(
            result["chapters"], ensure_ascii=False,
        )
        await db.commit()

        return ChapterChatResponse(
            reply=result["reply"],
            chapters=result["chapters"],
            conversation_id=result["conversation_id"],
        )

    except Exception as exc:
        logger.exception("Chapter chat edit failed")
        raise HTTPException(status_code=500, detail=f"章节编辑失败: {exc}")


# ---------------------------------------------------------------------------
# POST /{project_id}/chapters/lock
# ---------------------------------------------------------------------------

@router.post("/{project_id}/chapters/lock", response_model=LockChaptersResponse)
async def lock_chapters(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """锁定章节结构并创建 ProjectChapter 记录.

    锁定后：
    - chapter_structure_json 不可再通过 chat 修改
    - 为每个章节创建 ProjectChapter 记录
    - ai_generated 类型的章节进入待细化状态
    - 项目状态从 collecting 推进
    """
    # Load project
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 「目录确认门」guard：UI 流程必须先调 outline/confirm 才能物化 ProjectChapter。
    # lock 保留为 admin fallback，server 侧也要求状态对齐。
    if project.status != "structure_ready":
        raise HTTPException(
            status_code=400,
            detail=(
                f"当前项目状态 {project.status} 不允许锁定。"
                "请走标准流程：先 POST /outline/confirm。"
            ),
        )

    chapters_json = project.chapter_structure_json
    if not chapters_json or chapters_json in ("[]", "{}", ""):
        raise HTTPException(
            status_code=400,
            detail="请先提取章节（POST /extract-chapters）再进行锁定",
        )

    created, auto_added, validation, added_from_rubric = await _materialise_chapters(project, db)
    await db.commit()

    logger.info(
        "Locked %d chapters for project %s (auto-added: %s, rubric-added: %s)",
        len(created), project_id, auto_added, added_from_rubric,
    )

    message = f"已锁定 {len(created)} 个章节。文件/表格类型章节可直接生成，AI撰写章节请先细化标题。"
    if auto_added:
        message += f" 已按招标文件格式自动补充必需章节：{'、'.join(auto_added)}。"
    if added_from_rubric:
        message += f" 已按评标办法自动补充：{'、'.join(added_from_rubric)}。"
    if validation.get("coverage_notes"):
        message += f" 有 {len(validation['coverage_notes'])} 个评分项未在章节标题中体现，建议细化标题时覆盖。"

    # Update project status — admin fallback 路径，UI 不直接调用
    project.status = "collecting"
    await db.commit()

    return LockChaptersResponse(
        success=True,
        chapters_count=len(created),
        message=message,
    )


# ---------------------------------------------------------------------------
# POST /{project_id}/outline/confirm
# ---------------------------------------------------------------------------

class OutlineConfirmResponse(BaseModel):
    success: bool = False
    chapters_count: int = 0
    status: str = ""
    added_from_rubric: list[str] = []


@router.post("/{project_id}/outline/confirm", response_model=OutlineConfirmResponse)
async def confirm_outline(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """冻结用户审阅/编辑后的章节结构并推进到「信息搜集」阶段.

    这是「目录确认门」唯一合法的状态出口：从 structure_ready 推进到 collecting。
    物化 ProjectChapter 行的逻辑与 /chapters/lock 共享 _materialise_chapters。
    """
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status != "structure_ready":
        raise HTTPException(
            status_code=400,
            detail=(
                f"当前项目状态 {project.status} 不允许确认目录。"
                "请先 POST /extract-chapters，再走确认流程。"
            ),
        )

    chapters_json = project.chapter_structure_json
    if not chapters_json or chapters_json in ("[]", "{}", ""):
        raise HTTPException(
            status_code=400,
            detail="章节数据为空，请先 POST /extract-chapters",
        )

    created, auto_added, _validation, added_from_rubric = await _materialise_chapters(project, db)
    project.status = "collecting"
    await db.commit()

    logger.info(
        "Outline confirmed for project %s: %d chapters (auto-added: %s, rubric-added: %s)",
        project_id, len(created), auto_added, added_from_rubric,
    )

    return OutlineConfirmResponse(
        success=True,
        chapters_count=len(created),
        status="collecting",
        added_from_rubric=added_from_rubric,
    )


# ---------------------------------------------------------------------------
# Module-level helper shared by /chapters/lock and /outline/confirm
# ---------------------------------------------------------------------------

async def _materialise_chapters(
    project: BidProject,
    db: AsyncSession,
) -> tuple[list[ProjectChapter], list[str], dict, list[str]]:
    """根据 project.chapter_structure_json + format_template + 评标办法 物化 ProjectChapter 行.

    Returns:
        (created_chapters, auto_added_titles, validation_report, added_from_rubric)

    第 4 个返回值 added_from_rubric：由评标办法内容型指标自动补充的标题列表（空 = 未补）。
    行为与历史 /chapters/lock 一致；调用方负责 db.commit() 与 project.status 推进。
    """
    try:
        chapters = json.loads(project.chapter_structure_json or "[]")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="章节数据格式错误")

    if not isinstance(chapters, list):
        raise HTTPException(status_code=400, detail="章节数据格式错误：应为数组")

    format_template = {}
    try:
        format_template = json.loads(project.format_template_json) if project.format_template_json else {}
    except json.JSONDecodeError:
        format_template = {}

    # ── 评标办法指标：先加载，供覆盖校验（validate_chapter_structure）与补全共用 ──
    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        rubric = {}

    # Delete existing chapters
    for ch in list(project.chapters):
        await db.delete(ch)
    await db.flush()

    structure = format_template.get("document_structure", []) or []
    global_rules = format_template.get("global_format_rules", {}) or {}

    def _find_structure_part(title: str):
        for part in structure:
            part_title = part.get("title", "")
            if part_title and (part_title in title or title in part_title):
                return part
        return None

    def _build_meta(ch_data: dict, part: dict | None) -> str:
        meta = {
            "number": ch_data.get("number", ""),
            "format_notes": ch_data.get("format_notes", ""),
            "scoring_context": ch_data.get("scoring_context", ""),
            "table_columns": ch_data.get("table_columns", []),
        }
        if part:
            # 合并招标文件格式模板中的表/签章/序号约束，供生成阶段严格遵循
            meta["table_schema"] = part.get("table_schema", [])
            meta["fixed_text_segments"] = part.get("fixed_text_segments", [])
            meta["signature_block"] = part.get("signature_block", {})
            meta["numbering_style"] = global_rules.get("numbering_style", "chinese_legal")
        return json.dumps(meta, ensure_ascii=False)

    def _make_chapter(
        title: str,
        order_index: int,
        ch_type: str,
        meta: str,
        children: list,
    ) -> ProjectChapter:
        return ProjectChapter(
            project_id=project.id,
            title=title,
            order_index=order_index,
            status="pending",
            chapter_type=ch_type,
            chapter_meta_json=meta,
            children_json=json.dumps(children, ensure_ascii=False),
            review_status="locked" if ch_type in ("fixed_form", "table", "attachment") else "refining",
        )

    # Create ProjectChapter records（含格式模板元数据合并）
    created: list[ProjectChapter] = []
    for ch_data in chapters:
        ch_type = ch_data.get("type", "ai_generated")
        chapter = _make_chapter(
            title=ch_data.get("title", ""),
            order_index=ch_data.get("order_index", 0),
            ch_type=ch_type,
            meta=_build_meta(ch_data, _find_structure_part(ch_data.get("title", ""))),
            children=ch_data.get("children", []),
        )
        db.add(chapter)
        created.append(chapter)

    # ── 章节对账：校验必需章节齐全 + 按模板顺序自动补充缺失项 ──
    requirements = json.loads(project.parsed_requirements_json) if project.parsed_requirements_json else {}
    from app.services.format_verifier import validate_chapter_structure
    validation = validate_chapter_structure(chapters, format_template, requirements, rubric=rubric)
    auto_added: list[str] = []

    if structure and validation.get("missing_required"):
        consumed: set[str] = set()
        final_order: list[ProjectChapter] = []
        for part in structure:
            part_title = part.get("title", "")
            if not part_title:
                continue
            match = next(
                (c for c in created if c.id not in consumed and part_title in c.title),
                None,
            )
            if match:
                final_order.append(match)
                consumed.add(match.id)
            elif part.get("required", True):
                ch_type = part.get("type", "ai_generated")
                placeholder = _make_chapter(
                    title=part_title,
                    order_index=0,  # 下方统一重新编号
                    ch_type=ch_type,
                    meta=_build_meta({}, part),
                    children=part.get("children", []),
                )
                db.add(placeholder)
                final_order.append(placeholder)
                auto_added.append(part_title)
        # 追加模板之外的自由章节（用户额外锁定）
        for c in created:
            if c.id not in consumed:
                final_order.append(c)
        # 按模板顺序重新编号
        for i, c in enumerate(final_order):
            c.order_index = i
        created = final_order

    # ── 评标办法补全：内容型缺失指标自动补章，标记「来自评标办法」，幂等防重 ----------
    added_from_rubric: list[str] = []
    writeback_children: dict[str, list] = {}   # 顶层章节 title -> 追加的补入叶子（回写 chapter_structure_json）
    writeback_new_top: list[dict] = []         # 无 dimension 章节时新建的顶层节点（回写 chapter_structure_json）
    fresh_rubric_items = rubric.get("items")
    if fresh_rubric_items and not rubric.get("applied"):
        from app.services.rubric_gap import gap_detect, build_rubric_nodes

        def _child_titles(nodes) -> list[str]:
            out = []
            for n in nodes or []:
                out.append(str(n.get("title") or ""))
                out.extend(_child_titles(n.get("children")))
            return out

        def _load_children(ch) -> list:
            try:
                return json.loads(ch.children_json or "[]")
            except json.JSONDecodeError:
                return []

        all_titles = [c.title for c in created] + [
            t for c in created for t in _child_titles(_load_children(c))
        ]
        missing = gap_detect(rubric, all_titles)
        if missing:
            from app.services.rubric_gap import attach_key_for
            # attach/new_top 归属只看顶层章节标题；子标题仅参与缺口检测（key_terms 命中）
            # —— 否则维度名只出现在子标题时 attach 会指向不存在的顶层章节，补入节点静默丢失
            new_top, attach, added_from_rubric = build_rubric_nodes(missing, [c.title for c in created])
            # 已存在 dimension 章节 -> 追加小节 + 刷新 children_json
            # （章节标题可能带序号前缀如「三、技术部分」，用双向包含匹配维度 key；
            #  首命中即消费该维度补入节点，后续同维度章节不再重复挂）
            for ch in created:
                key = attach_key_for(ch.title, attach)
                if key is not None:
                    payload = attach.pop(key)
                    try:
                        children = json.loads(ch.children_json or "[]")
                    except json.JSONDecodeError:
                        children = []
                    children.extend(payload)
                    ch.children_json = json.dumps(children, ensure_ascii=False)
                    writeback_children[ch.title] = payload
            # 无 dimension 章节 -> 新建顶层（走同一 _make_chapter，正常 token 预算分配）
            for node in new_top:
                chapter = _make_chapter(
                    title=node.get("title", ""),
                    order_index=0,
                    ch_type="ai_generated",
                    meta=_build_meta(node, None),
                    children=node.get("children", []),
                )
                db.add(chapter)
                created.append(chapter)
                writeback_new_top.append(node)
            # 幂等：补入成功后置 applied，rubric 内容再变动时才清除
            rubric["applied"] = True
            project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)

            # ── 回写 chapter_structure_json（spec §6.3：children_json / chapter_structure_json
            #    节点同样带 source 标记）——已有章节扩子节点、无 dimension 章节追加顶层节点 ──
            if writeback_children or writeback_new_top:
                struct = [c for c in chapters if isinstance(c, dict)]
                for part in struct:
                    payload = writeback_children.pop(part.get("title", ""), None)
                    if payload is not None:
                        part["children"] = list(part.get("children") or []) + payload
                for n in writeback_new_top:
                    n.setdefault("order_index", len(struct))
                    struct.append(n)
                # placeholder 章节（由 format 模板补入、structure_json 无对应节点）→
                # 镜像补为 struct 顶层节点，保证 §6.3 结构一致性；正常路径此循环为空
                for title, payload in writeback_children.items():
                    struct.append({
                        "title": title,
                        "type": "ai_generated",
                        "source": "scoring_rubric",
                        "children": payload,
                        "order_index": len(struct),
                    })
                    logger.warning(
                        "Rubric attach target %r absent from chapter_structure_json — mirrored as top-level",
                        title,
                    )
                project.chapter_structure_json = json.dumps(struct, ensure_ascii=False)
    # 统一重编号（模板补入 / rubric 补入后 order_index 连续）
    for i, c in enumerate(created):
        c.order_index = i

    return created, auto_added, validation, added_from_rubric


# ---------------------------------------------------------------------------
# GET /{project_id}/chapters
# ---------------------------------------------------------------------------

@router.get("/{project_id}/chapters")
async def get_chapters(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目的章节结构（未锁定返回 structure_json，已锁定返回 ProjectChapter 列表）."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 解析 chapter_structure_json（unlocked 形态；locked 分支用 ProjectChapter 行）
    try:
        chapters = json.loads(project.chapter_structure_json) if project.chapter_structure_json else []
    except json.JSONDecodeError:
        chapters = []

    # ── 评标办法覆盖预览（未落库，仅供确认页「评标办法覆盖」提示条）──
    def _collect_titles(items) -> list[str]:
        out = []
        for n in items or []:
            if isinstance(n, dict):
                out.append(str(n.get("title") or ""))
                out.extend(_collect_titles(n.get("children")))
        return out

    try:
        preview_rubric = json.loads(project.scoring_rubric_json or "{}") if project.scoring_rubric_json else {}
    except json.JSONDecodeError:
        preview_rubric = {}
    rubric_cover = None
    if preview_rubric.get("items"):
        if project.chapters:
            all_titles = []
            for ch in sorted(project.chapters, key=lambda c: c.order_index):
                all_titles.append(ch.title)
                try:
                    all_titles.extend(_collect_titles(json.loads(ch.children_json or "[]")))
                except json.JSONDecodeError:
                    pass
        else:
            all_titles = _collect_titles(chapters)
        from app.services.rubric_gap import gap_detect
        missing = gap_detect(preview_rubric, all_titles)
        rubric_cover = {
            "status": preview_rubric.get("status"),
            "applied": bool(preview_rubric.get("applied")),
            "missing": [
                {"dimension": m["dimension"], "name": m["item"].get("name", "")}
                for m in missing
            ],
        }

    # locked 分支（提前 return，rubric_cover 已算好）
    if project.chapters:
        return {
            "locked": True,
            "chapters": [
                {
                    "id": ch.id,
                    "title": ch.title,
                    "order_index": ch.order_index,
                    # 同时给 type 和 chapter_type —— 前端 OutlineEditor 用 type，
                    # 历史 ProjectChapter 字段叫 chapter_type，避免误判为「暂无章节」。
                    "type": ch.chapter_type,
                    "chapter_type": ch.chapter_type,
                    "chapter_meta": json.loads(ch.chapter_meta_json) if ch.chapter_meta_json else {},
                    "children": json.loads(ch.children_json) if ch.children_json else [],
                    "review_status": ch.review_status,
                    "status": ch.status,
                }
                for ch in sorted(project.chapters, key=lambda c: c.order_index)
            ],
            "rubric_cover": rubric_cover,
        }

    # chapter_structure_json 已经包含 type 字段（来自 extract-chapters / chat 输出），
    # 这里不需要改键名。
    return {
        "locked": False,
        "chapters": chapters,
        "rubric_cover": rubric_cover,
    }


# ---------------------------------------------------------------------------
# Title Refinement schemas & endpoints
# ---------------------------------------------------------------------------

class RefineTitlesResponse(BaseModel):
    chapter_id: str = ""
    chapter_title: str = ""
    children: list = []
    leaf_count: int = 0
    error: str = ""


class RefineChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class RefineChatResponse(BaseModel):
    reply: str = ""
    children: list = []


class LockTitlesResponse(BaseModel):
    success: bool = False
    leaf_count: int = 0
    message: str = ""


@router.post("/{project_id}/chapters/{chapter_id}/refine", response_model=RefineTitlesResponse)
async def refine_chapter_titles(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对 AI 撰写章节进行标题细化，生成 3-4 级子标题树."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Find the chapter
    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    if chapter.chapter_type != "ai_generated":
        raise HTTPException(
            status_code=400,
            detail=f"只有 AI 撰写类型的章节需要标题细化，当前章节类型为 {chapter.chapter_type}",
        )

    try:
        from app.services.title_refiner import refine_chapter_titles as do_refine
        from app.services.ai_adapter import ai_adapter as ai

        chapter_meta = json.loads(chapter.chapter_meta_json) if chapter.chapter_meta_json else {}
        requirements = json.loads(project.parsed_requirements_json) if project.parsed_requirements_json else {}

        children = await do_refine(
            chapter_title=chapter.title,
            chapter_meta=chapter_meta,
            requirements=requirements,
            ai_adapter=ai,
            target_pages=project.target_pages or settings.GENERATION_TARGET_PAGES_DEFAULT,
        )

        # Save to chapter
        chapter.children_json = json.dumps(children, ensure_ascii=False)
        chapter.review_status = "refining"
        await db.commit()

        # Count leaves
        def count_leaves(nodes):
            c = 0
            for n in nodes:
                if n.get("children"):
                    c += count_leaves(n["children"])
                else:
                    c += 1
            return c

        leaf_count = count_leaves(children)

        return RefineTitlesResponse(
            chapter_id=chapter_id,
            chapter_title=chapter.title,
            children=children,
            leaf_count=leaf_count,
        )

    except Exception as exc:
        logger.exception("Title refinement failed")
        raise HTTPException(status_code=500, detail=f"标题细化失败: {exc}")


@router.post("/{project_id}/chapters/{chapter_id}/refine/chat", response_model=RefineChatResponse)
async def chat_refine_titles(
    project_id: str,
    chapter_id: str,
    data: RefineChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对话式修改子标题树."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        from app.services.title_refiner import chat_refine_titles as do_chat
        from app.services.ai_adapter import ai_adapter as ai

        result = await do_chat(
            children_json=chapter.children_json,
            chapter_title=chapter.title,
            user_message=data.message,
            ai_adapter=ai,
        )

        # Save updated children
        chapter.children_json = json.dumps(result["children"], ensure_ascii=False)
        await db.commit()

        return RefineChatResponse(
            reply=result["reply"],
            children=result["children"],
        )

    except Exception as exc:
        logger.exception("Title refinement chat failed")
        raise HTTPException(status_code=500, detail=f"标题对话修改失败: {exc}")


@router.post("/{project_id}/chapters/{chapter_id}/refine/lock", response_model=LockTitlesResponse)
async def lock_refined_titles(
    project_id: str,
    chapter_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """锁定细化后的标题，将叶子节点转为生成任务."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        children = json.loads(chapter.children_json) if chapter.children_json else []
    except json.JSONDecodeError:
        children = []

    if not children:
        raise HTTPException(
            status_code=400,
            detail="请先细化标题（POST /refine）再锁定",
        )

    from app.services.title_refiner import annotate_tree_depths, count_leaves

    # 持久化嵌套目录树（保留层级），标注 depth；生成时再按需扁平化为任务
    annotated = annotate_tree_depths(children, 1)
    chapter.children_json = json.dumps(annotated, ensure_ascii=False)
    chapter.review_status = "generating"
    await db.commit()

    leaf_count = count_leaves(annotated)
    return LockTitlesResponse(
        success=True,
        leaf_count=leaf_count,
        message=f"已锁定 {chapter.title} 的子标题，共 {leaf_count} 个生成任务。",
    )


# ---------------------------------------------------------------------------
# Section-level modify & regenerate
# ---------------------------------------------------------------------------

class SectionModifyRequest(BaseModel):
    section_path: list = Field(..., min_length=1)
    current_content: str = ""
    instruction: str = Field(..., min_length=1, max_length=2000)


class SectionModifyResponse(BaseModel):
    modified_content: str = ""
    diff_summary: str = ""


class SectionRegenerateRequest(BaseModel):
    section_path: list = Field(..., min_length=1)
    token_budget_hint: str = "medium"


class SectionSaveRequest(BaseModel):
    section_path: list = Field(..., min_length=1)
    content: str = ""


class SectionSaveResponse(BaseModel):
    success: bool = False


class SectionChatRequest(BaseModel):
    section_path: list = Field(..., min_length=1)
    current_content: str = ""
    messages: list = []
    instruction: str = ""


class SectionChatResponse(BaseModel):
    reply: str = ""
    revised_content: str = ""


async def _materials_guidance_for_section(
    section_title: str,
    project_id: str,
    db: AsyncSession,
) -> str:
    """按节标题组装素材上下文，供单节修改/重新生成时注入提示.

    抓取已收集的公司资质 / 人员 / 历史合同 / 公司信息，经
    assemble_section_materials 按标题关键词命中并截断。任何失败返回空串，
    不阻断主流程。
    """
    try:
        from app.services.collection import get_collected_resources
        from app.services.materials_context import assemble_section_materials

        collected = await get_collected_resources(project_id, db)
        return assemble_section_materials(
            section_title,
            qualifications=collected.get("qualifications", []) if collected else None,
            personnel=collected.get("personnel", []) if collected else None,
            contracts=collected.get("contracts", []) if collected else None,
            company=collected.get("company") if collected else None,
        )
    except Exception as exc:
        logger.warning("Materials guidance build failed for '%s': %s", section_title, exc)
        return ""


@router.post("/{project_id}/chapters/{chapter_id}/sections/modify", response_model=SectionModifyResponse)
async def modify_section(
    project_id: str,
    chapter_id: str,
    data: SectionModifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """AI 针对性修改单个节的内容."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    section_title = data.section_path[-1] if data.section_path else chapter.title
    materials_guidance = await _materials_guidance_for_section(section_title, project_id, db)

    try:
        from app.services.section_editor import modify_section as do_modify
        from app.services.ai_adapter import ai_adapter as ai

        result = await do_modify(
            chapter_title=chapter.title,
            section_path=data.section_path,
            current_content=data.current_content,
            instruction=data.instruction,
            children_json=chapter.children_json,
            materials_guidance=materials_guidance,
            ai_adapter=ai,
        )
        return SectionModifyResponse(**result)

    except Exception as exc:
        logger.exception("Section modify failed")
        raise HTTPException(status_code=500, detail=f"AI 修改失败: {exc}")


@router.post("/{project_id}/chapters/{chapter_id}/sections/regenerate")
async def regenerate_section(
    project_id: str,
    chapter_id: str,
    data: SectionRegenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重新生成单个节的内容，SSE 流式返回."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    requirements = json.loads(project.parsed_requirements_json) if project.parsed_requirements_json else {}

    # Gather company profile
    company_profile = None
    try:
        from app.services.collection import get_collected_resources
        collected = await get_collected_resources(project_id, db)
        company_profile = collected.get("company") if collected else None
    except Exception:
        pass

    section_title = data.section_path[-1] if data.section_path else chapter.title
    materials_guidance = await _materials_guidance_for_section(section_title, project_id, db)

    async def event_generator():
        try:
            from app.services.section_editor import regenerate_section as do_regenerate
            from app.services.ai_adapter import ai_adapter as ai

            full = ""
            async for chunk in do_regenerate(
                chapter_title=chapter.title,
                section_path=data.section_path,
                token_budget_hint=data.token_budget_hint,
                requirements=requirements,
                children_json=chapter.children_json,
                company_profile=company_profile,
                materials_guidance=materials_guidance,
                ai_adapter=ai,
            ):
                full += chunk
                yield {
                    "event": "chunk",
                    "data": json.dumps({"text": chunk}, ensure_ascii=False),
                }

            yield {
                "event": "done",
                "data": json.dumps({
                    "content": full,
                    "section_path": data.section_path,
                }, ensure_ascii=False),
            }

        except Exception as exc:
            yield {
                "event": "error",
                "data": json.dumps({"message": str(exc)}, ensure_ascii=False),
            }

    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(event_generator())


@router.post("/{project_id}/chapters/{chapter_id}/sections/save", response_model=SectionSaveResponse)
async def save_section(
    project_id: str,
    chapter_id: str,
    data: SectionSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存单个节的内容到 children_json 树中."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        from app.services.section_editor import save_section_content

        updated = save_section_content(chapter.children_json, data.section_path, data.content)
        chapter.children_json = updated
        await db.commit()

        return SectionSaveResponse(success=True)

    except Exception as exc:
        logger.exception("Section save failed")
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}")


@router.post("/{project_id}/chapters/{chapter_id}/sections/chat", response_model=SectionChatResponse)
async def chat_section(
    project_id: str,
    chapter_id: str,
    data: SectionChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """章节多轮对话，返回 AI 回复与修改后的内容."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        from app.services.section_editor import chat_section as do_chat
        from app.services.ai_adapter import ai_adapter as ai

        section_title = data.section_path[-1] if data.section_path else chapter.title
        materials_guidance = await _materials_guidance_for_section(section_title, project_id, db)

        result = await do_chat(
            chapter_title=chapter.title,
            section_path=data.section_path,
            current_content=data.current_content,
            messages=data.messages,
            ai_adapter=ai,
            materials_guidance=materials_guidance,
        )
        return SectionChatResponse(**result)
    except RuntimeError as exc:
        logger.exception("Section chat failed")
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("Section chat failed")
        raise HTTPException(status_code=500, detail=f"章节对话失败: {exc}")


@router.get("/{project_id}/chapters/{chapter_id}/sections")
async def get_section(
    project_id: str,
    chapter_id: str,
    section_path: str = "",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个节的内容."""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    chapter = next((ch for ch in project.chapters if ch.id == chapter_id), None)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    try:
        path = json.loads(section_path) if section_path else []
    except json.JSONDecodeError:
        path = []

    from app.services.section_editor import get_section_content

    content = get_section_content(chapter.children_json, path)
    return {"content": content, "section_path": path}
