"""宏曦标书 - 章节提取、审核、对话编辑 API.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
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

# 用户可以在结构页选择的章节类型。mixed 由 AI 提取产生，保留展示但不可选。
USER_SELECTABLE_TYPES = ("fixed_form", "table", "attachment", "ai_generated")
_KNOWN_TYPES = set(USER_SELECTABLE_TYPES) | {"mixed"}

# 允许在结构页编辑的项目状态（「目录确认」之前）
_STRUCTURE_EDITABLE_STATUS = ("draft", "structure_ready")


def _normalize_chapter_tree(tree: list) -> list:
    """校验并归一化前端提交的章节树.

    - ``type`` 不在已知集合内 → 退回 ``ai_generated``（不报错，避免前端版本
      稍旧就整单失败）
    - ``order_index`` 一律按数组顺序重排，忽略前端传来的值（拖拽后的顺序就是
      数组顺序，这是唯一的真相）
    - ``match`` / ``attachments`` 原样保留
    """
    out = []
    for node in tree or []:
        if not isinstance(node, dict):
            continue
        ch_type = node.get("type") or "ai_generated"
        if ch_type not in _KNOWN_TYPES:
            ch_type = "ai_generated"
        normalized = {
            "order_index": len(out),
            "number": node.get("number", ""),
            "title": (node.get("title") or "").strip(),
            "type": ch_type,
            "required": bool(node.get("required", False)),
            "format_notes": node.get("format_notes"),
            "scoring_context": node.get("scoring_context"),
            "table_columns": node.get("table_columns"),
            "source": node.get("source"),
        }
        if node.get("match"):
            normalized["match"] = node["match"]
        normalized["attachments"] = list(node.get("attachments") or [])
        normalized["children"] = _normalize_chapter_tree(node.get("children") or [])
        out.append(normalized)
    return out


def _generation_chapter_type(raw: str) -> str:
    """把章节类型归一化成生成管线认识的四种.

    ``mixed`` 是 AI 提取的产物，生成管线只处理 ``fixed_form`` / ``table`` /
    ``attachment`` / ``ai_generated`` —— ``mixed`` 落进"两个循环都进不去"的空档，
    最终渲染成「（待补充…）」占位文本。落库时归到 ``ai_generated``。
    （``chapter_structure_json`` 里仍保留 ``mixed``，结构页照常展示。）
    """
    return raw if raw in USER_SELECTABLE_TYPES else "ai_generated"


def _build_chapter_meta(
    ch_data: dict, part: dict | None, global_rules: dict | None = None,
) -> str:
    """组装 ProjectChapter.chapter_meta_json.

    ``match`` / ``attachments`` 是 2026-09-27 新增的键：前者是结构页固化的
    原文匹配引用（生成阶段据此切片），后者是附件清单。老项目没有这两个键，
    读取侧一律兜底（match 视作 status=na，attachments 视作 []）。
    """
    meta = {
        "number": ch_data.get("number", ""),
        "format_notes": ch_data.get("format_notes", ""),
        "scoring_context": ch_data.get("scoring_context", ""),
        "table_columns": ch_data.get("table_columns", []),
        "attachments": list(ch_data.get("attachments") or []),
    }
    if ch_data.get("match"):
        meta["match"] = ch_data["match"]
    if part:
        # 合并招标文件格式模板中的表/签章/序号约束，供生成阶段严格遵循
        meta["table_schema"] = part.get("table_schema", [])
        meta["fixed_text_segments"] = part.get("fixed_text_segments", [])
        meta["signature_block"] = part.get("signature_block", {})
        meta["numbering_style"] = (global_rules or {}).get(
            "numbering_style", "chinese_legal",
        )
    return json.dumps(meta, ensure_ascii=False)


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

    # unplaced 只记日志：/chapters/lock 是 admin 兜底路径，用户可见的是 /outline/confirm
    created, auto_added, validation, added_from_rubric, _unplaced_from_rubric = await _materialise_chapters(project, db)
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
    # 确认时被剔除的附件 label（资源库行已删 / 路径越界 / 内容重复）
    pruned_attachments: list[str] = []
    # 评标办法有、但确认的目录里找不到归属章节的内容项 —— **没有**加进目录，
    # 必须报给用户让他决定加到哪（只挂不建）
    unplaced_rubric_items: list[str] = []


class OutlineConfirmRequest(BaseModel):
    chapters: list[dict] | None = None


# 确认目录被拒时给**用户**看的原因。原先是一句
# 「请先 POST /extract-chapters，再走确认流程」—— API 指令，对用户毫无意义，
# 而且他往往没做错任何事（例如生成已启动、或项目早过了这一关）。
_CONFIRM_BLOCKED_REASON = {
    "generating": "标书正在生成中，暂时无法确认目录。请等生成结束，或重新生成后再试。",
    "collecting": "项目已在信息搜集阶段（目录确认过了）。如需改目录，请先重新提取章节。",
    "review": "标书已生成，目录已锁定。如需改目录，请先重新提取章节。",
    "exported": "标书已导出，目录已锁定。如需改目录，请先重新提取章节。",
    "archived": "项目已归档，无法确认目录。",
    "draft": "项目还没提取过章节，请先重新提取章节。",
}


def _confirm_blocked_reason(status: str) -> str:
    """返回确认目录被拒时给用户看的原因."""
    return _CONFIRM_BLOCKED_REASON.get(
        status, f"当前项目状态 {status or '(空)'} 不允许确认目录。",
    )


async def _apply_submitted_tree(project, chapters: list[dict], db: AsyncSession) -> list[str]:
    """把前端提交的整棵树落库，返回**被剔除的附件 label**.

    返回值必须透给调用方：剔除是静默发生的（资源库行被删 / 路径越界 / 内容重复），
    用户界面上仍显示原来的附件数，不说一声他会到生成时才发现那一页是空的。
    """
    tree = _normalize_chapter_tree(chapters)
    pruned = await _prune_attachments(tree, db)
    project.chapter_structure_json = json.dumps(tree, ensure_ascii=False)
    await db.flush()
    return pruned


@router.post("/{project_id}/outline/confirm", response_model=OutlineConfirmResponse)
async def confirm_outline(
    project_id: str,
    payload: OutlineConfirmRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """冻结用户审阅/编辑后的章节结构并推进到「信息搜集」阶段.

    这是「目录确认门」唯一合法的状态出口：从 structure_ready 推进到 collecting。
    物化 ProjectChapter 行的逻辑与 /chapters/lock 共享 _materialise_chapters。

    body 可选带整棵树——带了就先存再物化，避免"用户改了忘保存"导致白改。
    不带时行为与历史完全一致。
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
            detail=_confirm_blocked_reason(project.status),
        )

    pruned: list[str] = []
    if payload is not None and payload.chapters:
        pruned = await _apply_submitted_tree(project, payload.chapters, db)

    chapters_json = project.chapter_structure_json
    if not chapters_json or chapters_json in ("[]", "{}", ""):
        raise HTTPException(
            status_code=400,
            detail="章节数据为空，请先 POST /extract-chapters",
        )

    created, auto_added, _validation, added_from_rubric, unplaced_from_rubric = await _materialise_chapters(project, db)
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
        pruned_attachments=pruned,
        unplaced_rubric_items=unplaced_from_rubric,
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
        (created_chapters, auto_added_titles, validation_report, added_from_rubric,
         unplaced_from_rubric)

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
        ch_type = _generation_chapter_type(ch_data.get("type", "ai_generated"))
        chapter = _make_chapter(
            title=ch_data.get("title", ""),
            order_index=ch_data.get("order_index", 0),
            ch_type=ch_type,
            meta=_build_chapter_meta(
                ch_data,
                _find_structure_part(ch_data.get("title", "")),
                global_rules,
            ),
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
                ch_type = _generation_chapter_type(part.get("type", "ai_generated"))
                placeholder = _make_chapter(
                    title=part_title,
                    order_index=0,  # 下方统一重新编号
                    ch_type=ch_type,
                    meta=_build_chapter_meta({}, part, global_rules),
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
    unplaced_from_rubric: list[str] = []       # 找不到归属、未加进目录的条目标题
    writeback_children: dict[str, list] = {}   # 顶层章节 title -> 追加的补入叶子（回写 chapter_structure_json）
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
            # attach 归属只看顶层章节标题；子标题仅参与缺口检测（key_terms 命中）
            # —— 否则维度名只出现在子标题时 attach 会指向不存在的顶层章节，补入节点静默丢失
            # **只挂不建**：匹配不到已有章节的条目进 unplaced，不新建顶层章节
            attach, added_from_rubric, unplaced_from_rubric = build_rubric_nodes(
                missing, [c.title for c in created])
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
            # 未消费的 attach 键（两个缺失项的维度键同时命中同一章节标题时 attach_key_for
            # 只消费首命中 → 剩余叶子既不挂章节也没别处可去）。
            # 按「只挂不建」进 unplaced 报给用户，**不新建顶层章节**。added_from_rubric
            # 保持真实（只记真正挂上去的），applied=True 仍成立。
            if attach:
                leftover = list(attach)
                for _k in leftover:
                    unplaced_from_rubric.extend(
                        str(n.get("title") or "") for n in attach.pop(_k)
                    )
                logger.warning(
                    "评标办法 attach 键未全部消费（维度键冲突命中同一章节），已报为待人工处理: %s",
                    leftover,
                )
            if unplaced_from_rubric:
                logger.warning(
                    "评标办法有 %d 个内容项在确认的目录里找不到归属章节，未自动加入（只挂不建）: %s",
                    len(unplaced_from_rubric), unplaced_from_rubric,
                )
            # 幂等：补入成功后置 applied，rubric 内容再变动时才清除
            rubric["applied"] = True
            project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)

            # ── 回写 chapter_structure_json（spec §6.3：children_json / chapter_structure_json
            #    节点同样带 source 标记）——已有章节扩子节点 ──
            if writeback_children:
                struct = [c for c in chapters if isinstance(c, dict)]
                for part in struct:
                    payload = writeback_children.pop(part.get("title", ""), None)
                    if payload is not None:
                        part["children"] = list(part.get("children") or []) + payload
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

    return created, auto_added, validation, added_from_rubric, unplaced_from_rubric


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

    # ── 补齐未匹配章节的匹配结果（读时派生，**不落库**）──
    # 没有这一步，页面加载时每个固定格式/表格章节都显示红「未匹配」，
    # 而它们其实大多能匹配上（匹配原本只在改标题/改类型时才跑）。
    if chapters and not project.chapters:
        from app.services.tender_corpus import load_full_text

        try:
            _match_reqs = json.loads(project.parsed_requirements_json or "{}")
        except json.JSONDecodeError:
            _match_reqs = {}
        chapters = _enrich_missing_matches(
            chapters, _match_reqs, load_full_text(_match_reqs, settings.UPLOAD_DIR),
        )

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

        # 生成期感知（spec §6.1）：结构化评分指标喂标题细化提示
        try:
            rubric = json.loads(project.scoring_rubric_json or "{}")
        except json.JSONDecodeError:
            rubric = {}
        if rubric.get("items"):
            requirements = {**requirements, "scoring_rubric": rubric}

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
    matched: bool = False


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

        updated, matched = save_section_content(
            chapter.children_json, data.section_path, data.content
        )
    except Exception as exc:
        logger.exception("Section save failed")
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}")

    # 路径对不上就什么都没存，不能回 success: true —— 否则前端"保存成功"、
    # 用户切走切回发现内容没变，还查不出原因。
    if not matched:
        raise HTTPException(
            status_code=404,
            detail=f"小节路径不存在，内容未保存: {'/'.join(data.section_path)}",
        )

    chapter.children_json = updated
    await db.commit()
    return SectionSaveResponse(success=True, matched=True)


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


# ---------------------------------------------------------------------------
# 章节结构保存（目录确认页的手动编辑落库通道）
# ---------------------------------------------------------------------------

async def _prune_attachments(tree: list, db: AsyncSession) -> list[str]:
    """剔除失效附件，返回被剔除的 label 列表.

    剔除三种情况：
      - 路径越出 ``UPLOAD_DIR``（防目录穿越）
      - 引用的资源库行已被删除
      - 与前面节点重复（按**内容 md5** 判重，与 materials_injection 的
        ``drop_already_embedded`` 同一口径）

    剔除而非整单拒绝：用户可能填了十个附件、其中一个被删了，整单报错会让
    他白填一遍。
    """
    from app.models.contract import Contract
    from app.models.personnel import PersonnelCertificate
    from app.models.qualification import Qualification
    from app.services.render_engine import image_content_key

    _MODEL_BY_KIND = {
        "qualification": Qualification,
        "personnel_cert": PersonnelCertificate,
        "contract": Contract,
    }

    upload_root = Path(settings.UPLOAD_DIR).resolve()
    pruned: list[str] = []
    seen_content: set[str] = set()

    async def _walk_async(nodes: list) -> None:
        for node in nodes or []:
            kept = []
            for att in node.get("attachments") or []:
                label = att.get("label") or att.get("id") or "(未命名)"
                kind = att.get("kind")
                path = att.get("path") or ""

                try:
                    resolved = (upload_root / path).resolve()
                    # is_relative_to 而不是 startswith：后者会把
                    # `…/uploads_evil/x` 这类同前缀的兄弟目录误判为通过
                    if not resolved.is_relative_to(upload_root):
                        pruned.append(label)
                        continue
                except (OSError, ValueError):
                    pruned.append(label)
                    continue

                if kind in _MODEL_BY_KIND:
                    model = _MODEL_BY_KIND[kind]
                    found = (
                        await db.execute(select(model.id).where(model.id == att.get("id")))
                    ).scalars().all()
                    if not found:
                        pruned.append(label)
                        continue

                key = image_content_key(path)
                if key in seen_content:
                    pruned.append(label)
                    continue
                seen_content.add(key)

                kept.append(att)
            node["attachments"] = kept
            await _walk_async(node.get("children") or [])

    await _walk_async(tree)
    return pruned


class ChapterStructureSaveRequest(BaseModel):
    chapters: list[dict] = []


class ChapterStructureSaveResponse(BaseModel):
    success: bool = False
    chapters_count: int = 0
    pruned_attachments: list[str] = []


@router.put("/{project_id}/chapter-structure", response_model=ChapterStructureSaveResponse)
async def save_chapter_structure(
    project_id: str,
    payload: ChapterStructureSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存用户在目录确认页编辑后的章节结构.

    服务端**不重新匹配**——``match`` 是前端从 /chapter-structure/match 拿到的
    结果原样带回的，服务端只做结构校验与附件有效性剔除。
    """
    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status not in _STRUCTURE_EDITABLE_STATUS:
        raise HTTPException(
            status_code=409,
            detail=f"项目已进入 {project.status} 阶段，不能再修改章节结构",
        )

    if not payload.chapters:
        raise HTTPException(status_code=400, detail="章节结构不能为空")

    tree = _normalize_chapter_tree(payload.chapters)
    pruned = await _prune_attachments(tree, db)

    project.chapter_structure_json = json.dumps(tree, ensure_ascii=False)
    await db.commit()

    logger.info(
        "Chapter structure saved for project %s: %d top-level chapters, %d attachments pruned",
        project_id, len(tree), len(pruned),
    )
    return ChapterStructureSaveResponse(
        success=True, chapters_count=len(tree), pruned_attachments=pruned,
    )


# ---------------------------------------------------------------------------
# 章节试匹配（结构页实时预览命中的招标原文）
# ---------------------------------------------------------------------------

# 匹配预览的原文片段上限——结构页只是给人看的，没必要把整个小节（可能上万字）
# 走一遍网络；超出截断并在响应里标记，前端据此提示。
PREVIEW_MAX_CHARS = 4000


def _load_match_inputs(project) -> tuple[dict, str | None]:
    """取出匹配需要的语料：requirements + 全文（可能为 None）."""
    from app.services.tender_corpus import load_full_text

    try:
        requirements = json.loads(project.parsed_requirements_json or "{}")
    except json.JSONDecodeError:
        requirements = {}
    return requirements, load_full_text(requirements, settings.UPLOAD_DIR)


def _to_match_payload(
    result, corpus: str, tables: list[dict] | None = None,
    corpus_available: bool = True,
) -> dict:
    """把 MatchResult 转成前端要的形状，附上预览片段.

    表格章节的 ``best`` 可能为空（标题文案对不上小节、只按表头选出了表），
    那不是"没匹配上"——所以额外给出 ``table_preview``，让抽屉有东西可显示。
    """
    from app.services.template_filler import rows_to_markdown

    def _cand(c) -> dict:
        preview = corpus[c.start:c.end]
        return {
            "title": c.title,
            "raw": c.raw,
            "level": c.level,
            "start": c.start,
            "end": c.end,
            "score": round(c.score, 4),
            "page": c.page,
            "preview": preview[:PREVIEW_MAX_CHARS],
            "preview_truncated": len(preview) > PREVIEW_MAX_CHARS,
        }

    table_preview = None
    if result.source == "table" and result.table_index is not None:
        rows = tables or []
        if 0 <= result.table_index < len(rows):
            table_preview = rows_to_markdown(
                [list(r) for r in (rows[result.table_index].get("rows") or [])][:20]
            )

    best = result.best
    return {
        # ── 扁平记录：生成阶段就是照这些键切片（spec §4）──
        # 必须**顶层**给出，不能只塞在 best 里：`_resolve_section_text` 读的是
        # 顶层 `start`/`end`。曾经只放在 best 里，导致每一次生成都落回"按标题重
        # 匹配"，用户手选的候选被丢弃、每个章节还多一条假告警。
        "status": result.status,
        "source": result.source,
        "matched_title": best.title if best else None,
        "page": best.page if best else None,
        "start": best.start if best else None,
        "end": best.end if best else None,
        "score": round(best.score, 4) if best else None,
        "table_index": result.table_index,
        # 服务端一律给 auto；用户从候选里点选后由前端改成 manual（见 §4.1）
        "picked": "auto",
        "corpus_hash": result.corpus_hash,
        # 语料是否可用：false = 招标文件整体没有可匹配的文本（扫描件等）。
        # 这时"未匹配"的原因不是标题没写对，页面要给的是整体说明而不是
        # 「建议改标题」——改标题也没用，候选恒为空。
        "corpus_available": corpus_available,

        # ── 供结构页渲染：带原文片段 ──
        "best": _cand(best) if best else None,
        "candidates": [_cand(c) for c in result.candidates],
        "table_preview": table_preview,
    }


def _enrich_missing_matches(
    chapters: list, requirements: dict, full_text: str | None,
) -> list:
    """给**还没有、或已失效**的 match 的固定格式/表格章节补上匹配结果.

    线上反馈：「不点修改就显示未匹配，实际上是可以匹配的」。
    匹配原本只在「改标题 / 改类型」时触发（前端 ``onMatchRequest``），
    **加载时根本不跑** —— 刚提取/刚打开的结构，每个固定格式/表格章节都挂着
    红「未匹配」，用户得逐个点一次「重命名→保存」才看得到真实状态。
    页面在说谎，而这正是"哪里会出问题"的唯一告知渠道。

    在这里按需补算（与 ``rubric_cover`` 一样属于读时派生，**不落库**）：
    - 已有 match 且 ``corpus_hash`` 与对应语料一致 → 不动（幂等，且不会
      覆盖用户手选 ``picked="manual"`` 的结果）
    - ``ai_generated`` / ``attachment`` / ``mixed`` → 不参与匹配，跳过
    """
    from app.services.tender_section_matcher import corpus_hash, match_tender_section

    fmt_text = requirements.get("format_section_text")
    tables = requirements.get("format_tables") or []
    page_map = requirements.get("format_page_map") or []
    corpus_available = bool(fmt_text or full_text)

    def _existing_is_fresh(existing: dict) -> bool:
        if not existing:
            return False
        source_corpus = full_text if existing.get("source") == "full_text" else fmt_text
        if not source_corpus:
            return False
        return existing.get("corpus_hash") == corpus_hash(source_corpus)

    def _walk(nodes) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            if node.get("type") in ("fixed_form", "table"):
                if not _existing_is_fresh(node.get("match") or {}):
                    result = match_tender_section(
                        node.get("title") or "",
                        chapter_type=node["type"],
                        format_section_text=fmt_text,
                        full_text=full_text,
                        format_tables=tables,
                        format_page_map=page_map,
                    )
                    corpus = (full_text or "") if result.source == "full_text" else (fmt_text or "")
                    node["match"] = _to_match_payload(
                        result, corpus, tables, corpus_available=corpus_available,
                    )
            _walk(node.get("children"))

    _walk(chapters)
    return chapters


class ChapterMatchRequest(BaseModel):
    title: str = ""
    type: str = "fixed_form"


@router.post("/{project_id}/chapter-structure/match")
async def match_chapter_section(
    project_id: str,
    payload: ChapterMatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """试匹配：给定标题与类型，返回命中的招标原文片段或候选列表.

    纯读操作，不改任何状态。前端在标题/类型变化后防抖调用它来刷新徽标。
    """
    from app.services.tender_section_matcher import match_tender_section

    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status not in _STRUCTURE_EDITABLE_STATUS:
        raise HTTPException(
            status_code=409,
            detail=f"项目已进入 {project.status} 阶段，不能再试匹配章节",
        )

    requirements, full_text = _load_match_inputs(project)
    match_result = match_tender_section(
        payload.title,
        chapter_type=payload.type,
        format_section_text=requirements.get("format_section_text"),
        full_text=full_text,
        format_tables=requirements.get("format_tables") or [],
        format_page_map=requirements.get("format_page_map") or [],
    )

    # 预览片段取自实际用到的语料
    if match_result.source == "full_text":
        corpus = full_text or ""
    else:
        corpus = requirements.get("format_section_text") or ""
    return _to_match_payload(
        match_result, corpus, requirements.get("format_tables") or [],
        corpus_available=bool(requirements.get("format_section_text") or full_text),
    )


# ---------------------------------------------------------------------------
# 本项目附件上传（资源库里没有的文件：保证金凭证、基本账户证明等）
# ---------------------------------------------------------------------------

ALLOWED_ATTACHMENT_EXT = (".png", ".jpg", ".jpeg", ".pdf")


def _is_allowed_attachment(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in ALLOWED_ATTACHMENT_EXT


def _safe_attachment_name(filename: str) -> str:
    """只取 basename 并统一小写扩展名，防目录穿越."""
    name = Path(filename or "file").name
    stem = Path(name).stem or "file"
    return f"{stem}{Path(name).suffix.lower()}"


@router.post("/{project_id}/attachments/upload")
async def upload_project_attachment(
    project_id: str,
    file: UploadFile = File(...),
    label: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传本项目专用附件，返回一条可塞进章节节点的附件记录.

    只负责落盘并返回记录；清单本身住在章节树里，由前端塞进节点后随
    ``PUT /chapter-structure`` 统一保存（结构树节点没有稳定 id，按编号寻址
    会在用户改编号后错位）。

    注意 ``label`` 必须是 ``Form()``：它是前端 FormData 发的标量字段，不标就是
    query 参数，收不到、**且不报错**，静默退化成 file.filename。
    """
    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status not in _STRUCTURE_EDITABLE_STATUS:
        raise HTTPException(
            status_code=409,
            detail=f"项目已进入 {project.status} 阶段，不能再添加附件",
        )

    if not _is_allowed_attachment(file.filename or ""):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型，仅接受 {'/'.join(ALLOWED_ATTACHMENT_EXT)}",
        )

    upload_dir = Path(settings.UPLOAD_DIR) / f"project_{project_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_attachment_name(file.filename or "file")
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    (upload_dir / stored_name).write_bytes(await file.read())

    rel_path = f"project_{project_id}/{stored_name}"
    logger.info("Attachment uploaded for project %s: %s", project_id, rel_path)
    return {
        "kind": "upload",
        "id": uuid.uuid4().hex,
        "label": label or Path(safe_name).stem,
        "path": rel_path,
    }
