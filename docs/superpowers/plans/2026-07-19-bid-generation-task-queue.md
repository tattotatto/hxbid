# 标书生成任务队列改造 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将标书生成从"线程池一次性批量生成"改为"主循环逐节生成+增量持久化+断点续传"，解决2000+页标书生成卡死/全丢的问题。

**Architecture:** 去掉 `ThreadPoolExecutor`，在 SSE event generator 的 async 上下文中直接逐节调用 AI。每节完成立即 flush DB。新增 `generation_state_json` 字段追踪每节状态，支持中断恢复。

**Tech Stack:** Python/FastAPI/SQLAlchemy async + React/TypeScript/Ant Design

## Global Constraints

- 所有 AI 调用必须使用 `ai_adapter` 单例（AsyncOpenAI client），不可跨线程/跨事件循环
- 每个叶子小节生成后必须立即 `await db.flush()` + `await db.commit()`
- 单个小节失败不阻塞后续小节
- SSE 事件格式保持与现有前端兼容
- 历史合同注入逻辑：有明确业绩要求→注入对应章节；无要求→注入"其他内容"章节

---

## File Structure

| 文件 | 角色 | 改动类型 |
|------|------|---------|
| `backend/app/config.py` | 新增生成配置项 | Modify |
| `backend/app/models/project.py` | BidProject 加 generation_state_json 字段 | Modify |
| `backend/app/services/ai_pipeline.py` | 重写 generate_bid_with_deep_outline，去线程池 | Modify |
| `backend/app/services/subsection_generator.py` | 去 generate_section_tree 递归，输出单节生成函数 | Modify |
| `backend/app/services/rag.py` | 历史合同按需求匹配逻辑 | Modify |
| `backend/app/api/bid.py` | /generate 适配新流程，新增 /generate/retry-failed | Modify |
| `frontend/src/components/GenerationProgress.tsx` | 树形大纲+逐节进度 | Modify |
| `frontend/src/pages/project/ProjectWorkflow.tsx` | 集成新进度组件+断点续传按钮 | Modify |

---

### Task 1: 添加配置项

**Files:**
- Modify: `backend/app/config.py`

**Interfaces:**
- Produces: `settings.GENERATION_MAX_RETRIES`, `settings.GENERATION_RETRY_DELAY_BASE`, `settings.GENERATION_SECTION_TIMEOUT`

- [ ] **Step 1: 在 Settings 类中添加三个新配置项**

在 `backend/app/config.py` 的 `Settings` 类中，`GENERATION_TOKEN_BUDGET_TOTAL` 后面添加：

```python
# 逐节生成重试配置
GENERATION_MAX_RETRIES: int = 2
GENERATION_RETRY_DELAY_BASE: float = 1.0
GENERATION_SECTION_TIMEOUT: int = 180  # 单个小节最大秒数
```

- [ ] **Step 2: 验证配置可读取**

```bash
cd backend && python -c "from app.config import settings; print(settings.GENERATION_MAX_RETRIES)"
```

Expected: `2`

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "feat: add per-section generation retry config

- GENERATION_MAX_RETRIES=2
- GENERATION_RETRY_DELAY_BASE=1.0
- GENERATION_SECTION_TIMEOUT=180"
```

---

### Task 2: BidProject 模型加 generation_state_json 字段

**Files:**
- Modify: `backend/app/models/project.py`

**Interfaces:**
- Produces: `BidProject.generation_state_json: Mapped[str]` — Text column, default `"{}"`

- [ ] **Step 1: 添加字段**

在 `backend/app/models/project.py` 的 `BidProject` 类中，`outline_json` 字段后面添加：

```python
generation_state_json: Mapped[str] = mapped_column(
    Text,
    nullable=False,
    default="{}",
)
```

- [ ] **Step 2: 验证模型加载**

```bash
cd backend && python -c "from app.models.project import BidProject; print(BidProject.__tablename__)"
```

Expected: `bid_projects`

- [ ] **Step 3: 创建数据库迁移（如果使用 Alembic）**

如果有 Alembic，生成迁移：
```bash
cd backend && alembic revision --autogenerate -m "add generation_state_json to bid_projects" && alembic upgrade head
```

如果没有 Alembic（项目使用 `create_all`），在应用启动时 SQLAlchemy 会自动添加列。通过直接执行 SQL 确保列存在：

```bash
cd backend && python -c "
from app.database import engine_sync
from sqlalchemy import text
with engine_sync.connect() as conn:
    conn.execute(text('ALTER TABLE bid_projects ADD COLUMN IF NOT EXISTS generation_state_json TEXT NOT NULL DEFAULT \'{}\''))
    conn.commit()
print('Column added or already exists')
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/project.py
git commit -m "feat: add generation_state_json to BidProject for incremental generation tracking"
```

---

### Task 3: 重写 ai_pipeline.py — 去掉线程池，逐节生成

**Files:**
- Modify: `backend/app/services/ai_pipeline.py`

**Interfaces:**
- Consumes: `settings.GENERATION_MAX_RETRIES`, `settings.GENERATION_RETRY_DELAY_BASE`
- Produces: `generate_bid_with_deep_outline()` — 重写为无线程池版本
- Produces: `_generate_single_section_with_retry()` — 单节生成+重试
- Produces: `_init_generation_state()` — 初始化 generation_state_json
- Produces: `_update_generation_state()` — 更新单个节点的生成状态

- [ ] **Step 1: 添加重试和状态管理辅助函数**

在 `ai_pipeline.py` 文件底部（`generate_bid_with_deep_outline` 之前）添加：

```python
import time
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Generation state helpers
# ---------------------------------------------------------------------------

def _init_generation_state(leaves: List[dict]) -> dict:
    """Initialize generation_state_json from flattened leaf list."""
    sections: Dict[str, dict] = {}
    for leaf in leaves:
        path_key = " > ".join(leaf.get("path", []))
        sections[path_key] = {
            "status": "pending",
            "content": None,
            "char_count": 0,
            "retries": 0,
            "error": None,
            "generated_at": None,
        }
    return {
        "status": "generating",
        "total_leaves": len(leaves),
        "completed_leaves": 0,
        "sections": sections,
    }


def _update_generation_state(
    state: dict,
    path_key: str,
    status: str,
    content: str | None = None,
    error: str | None = None,
    retries: int = 0,
):
    """Update a single section's state in generation_state_json."""
    if path_key not in state["sections"]:
        state["sections"][path_key] = {}
    sec = state["sections"][path_key]
    sec["status"] = status
    if content is not None:
        sec["content"] = content
        sec["char_count"] = len(content)
        sec["generated_at"] = datetime.datetime.now().isoformat()
    if error is not None:
        sec["error"] = error
        sec["retries"] = retries

    # Recalculate completed count
    completed = sum(
        1 for s in state["sections"].values()
        if s.get("status") == "done"
    )
    state["completed_leaves"] = completed
    if completed >= state["total_leaves"]:
        state["status"] = "completed"
```

- [ ] **Step 2: 添加带重试的单节生成函数**

```python
async def _generate_single_section_with_retry(
    leaf: dict,
    requirements: dict,
    company_profile: dict | None,
    reference_sections: List[str],
    max_retries: int = 2,
    retry_delay_base: float = 1.0,
) -> tuple[str | None, str | None]:
    """Generate a single leaf section with retry.

    Returns:
        (content, error): content is None on failure; error is None on success.
    """
    from app.services.subsection_generator import generate_section

    title = leaf.get("title", "")
    path = leaf.get("path", [])
    depth = leaf.get("depth", 0)
    max_tokens = leaf.get("max_tokens", 4096)
    sibling_summaries = leaf.get("sibling_summaries", [])

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            full_content = ""
            async for chunk in generate_section(
                section_title=title,
                section_path=path,
                depth=depth,
                requirements=requirements,
                max_tokens=max_tokens,
                sibling_summaries=sibling_summaries[:8],
                reference_sections=reference_sections,
                company_profile=company_profile,
            ):
                full_content += chunk

            if not full_content.strip():
                raise ValueError("AI returned empty content")

            return full_content, None

        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                "Section '%s' attempt %d/%d failed: %s",
                title, attempt + 1, max_retries + 1, last_error,
            )
            if attempt < max_retries:
                delay = retry_delay_base * (2 ** attempt)
                await asyncio.sleep(delay)

    return None, last_error
```

- [ ] **Step 3: 重写 generate_bid_with_deep_outline — 去掉线程池**

完整替换 `generate_bid_with_deep_outline` 函数（行653-909）。关键改动：

```python
async def generate_bid_with_deep_outline(
    requirements: dict,
    company_profile: dict | None = None,
    matched_qualifications: list | None = None,
    matched_personnel: list | None = None,
    project_id: str = "",
    db=None,
    progress_callback: Callable | None = None,
) -> AsyncIterator[dict]:
    """Generate a complete bid document — incremental per-section generation.

    No ThreadPoolExecutor. Each leaf section is generated sequentially in
    the main async context. Each section is persisted immediately.
    """
    from app.services.outline_engine import generate_deep_outline
    from app.services.subsection_generator import prepare_outline_tree, get_outline_stats
    from app.services.content_assembler import build_final_chapters_payload
    from app.services.reference_analyzer import get_reference_outlines
    from app.services.rag import retrieve_similar_chapters
    from app.services.token_budget import collect_leaf_sections
    from app.models.project import BidProject

    # ── Phase 1: Build reference outlines ──
    reference_outlines = []
    if db:
        try:
            reference_outlines = await get_reference_outlines(db)
        except Exception as exc:
            logger.warning("Failed to load reference outlines: %s", exc)

    # ── Phase 2: Generate deep outline ──
    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "outline",
            "message": "正在生成深度大纲结构...",
        }, ensure_ascii=False),
    }

    tender_text = ""
    try:
        if requirements:
            tender_text = json.dumps(requirements, ensure_ascii=False)
    except Exception:
        pass

    deep_outline = await generate_deep_outline(
        requirements=requirements,
        reference_outlines=reference_outlines,
        tender_text=tender_text,
        min_leaves=settings.GENERATION_MIN_LEAF_SECTIONS,
        max_leaves=settings.GENERATION_MAX_LEAF_SECTIONS,
    )

    tree = prepare_outline_tree(deep_outline, requirements)
    stats = get_outline_stats(tree)
    leaves = collect_leaf_sections(tree)

    # ── Initialize generation state ──
    gen_state = _init_generation_state(leaves)

    # ── Restore previously completed sections (resume support) ──
    if db:
        try:
            from sqlalchemy import select as sa_select
            result = await db.execute(
                sa_select(BidProject).where(BidProject.id == project_id)
            )
            db_project = result.scalar_one_or_none()
            if db_project and db_project.generation_state_json:
                try:
                    prev_state = json.loads(db_project.generation_state_json)
                    if prev_state.get("sections"):
                        for path_key, sec in prev_state["sections"].items():
                            if sec.get("status") == "done" and sec.get("content"):
                                if path_key in gen_state["sections"]:
                                    gen_state["sections"][path_key] = sec
                        gen_state["completed_leaves"] = sum(
                            1 for s in gen_state["sections"].values()
                            if s.get("status") == "done"
                        )
                        logger.info(
                            "Restored %d completed sections from previous run",
                            gen_state["completed_leaves"],
                        )
                except Exception:
                    pass
            # Save initial state
            if db_project:
                db_project.generation_state_json = json.dumps(gen_state, ensure_ascii=False)
                await db.commit()
        except Exception as exc:
            logger.warning("Failed to load/save generation state: %s", exc)

    # ── Yield outline event ──
    yield {
        "event": "outline_ready",
        "data": json.dumps({
            "total_leaves": stats["total_leaf_sections"],
            "estimated_pages": stats["estimated_pages"],
            "max_depth": stats["max_depth"],
            "completed_from_previous": gen_state["completed_leaves"],
            "outline_tree": tree,
        }, ensure_ascii=False),
    }

    # ── Phase 3: Generate sections one by one (NO THREAD POOL) ──
    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "generating",
            "message": f"开始逐节生成（共 {stats['total_leaf_sections']} 个小节，已完成 {gen_state['completed_leaves']} 个）...",
            "total_leaf_sections": stats["total_leaf_sections"],
            "completed_leaf_sections": gen_state["completed_leaves"],
        }, ensure_ascii=False),
    }

    # Collect sibling titles per section for anti-duplication
    all_titles = [leaf.get("title", "") for leaf in leaves]

    total = len(leaves)
    for idx, leaf in enumerate(leaves):
        path_key = " > ".join(leaf.get("path", []))
        sec_state = gen_state["sections"].get(path_key, {})

        # Skip already completed
        if sec_state.get("status") == "done" and sec_state.get("content"):
            logger.info("Skipping already-completed section: %s", path_key)
            continue

        # Skip sections that failed max retries (unless we're explicitly retrying)
        if sec_state.get("status") == "failed" and sec_state.get("retries", 0) >= settings.GENERATION_MAX_RETRIES + 1:
            continue

        # ── Emit section_start ──
        yield {
            "event": "section_start",
            "data": json.dumps({
                "path": path_key,
                "title": leaf.get("title", ""),
                "index": idx + 1,
                "total": total,
                "depth": leaf.get("depth", 0),
                "estimated_pages": leaf.get("estimated_pages", 1),
            }, ensure_ascii=False),
        }

        # ── Update state to generating ──
        _update_generation_state(gen_state, path_key, "generating")
        if db:
            try:
                from sqlalchemy import select as sa_select
                result = await db.execute(
                    sa_select(BidProject).where(BidProject.id == project_id)
                )
                db_project = result.scalar_one_or_none()
                if db_project:
                    db_project.generation_state_json = json.dumps(gen_state, ensure_ascii=False)
                    await db.commit()
            except Exception:
                pass

        # ── Fetch reference sections via RAG ──
        reference_sections: List[str] = []
        if db:
            try:
                similar = await retrieve_similar_chapters(
                    chapter_title=leaf.get("title", ""),
                    requirements=requirements,
                    project_id=project_id,
                )
                reference_sections = [s.get("content", "") for s in similar if s.get("content")]
            except Exception as exc:
                logger.debug("RAG failed for '%s': %s", leaf.get("title", ""), exc)

        # ── Build sibling summaries ──
        sibling_summaries = [f"{t}（详见该章节）" for t in all_titles if t != leaf.get("title", "")]
        leaf["sibling_summaries"] = sibling_summaries

        # ── Generate with retry ──
        content, error = await _generate_single_section_with_retry(
            leaf=leaf,
            requirements=requirements,
            company_profile=company_profile,
            reference_sections=reference_sections,
            max_retries=settings.GENERATION_MAX_RETRIES,
            retry_delay_base=settings.GENERATION_RETRY_DELAY_BASE,
        )

        if content:
            # ── Success ──
            _update_generation_state(gen_state, path_key, "done", content=content)
            yield {
                "event": "section_done",
                "data": json.dumps({
                    "path": path_key,
                    "title": leaf.get("title", ""),
                    "content": content,
                    "content_length": len(content),
                    "char_count": len(content),
                    "index": idx + 1,
                    "total": total,
                }, ensure_ascii=False),
            }
        else:
            # ── Failed ──
            retries = settings.GENERATION_MAX_RETRIES + 1
            _update_generation_state(
                gen_state, path_key, "failed",
                error=error, retries=retries,
            )
            yield {
                "event": "section_error",
                "data": json.dumps({
                    "path": path_key,
                    "title": leaf.get("title", ""),
                    "error": error,
                    "retry_count": retries,
                    "index": idx + 1,
                    "total": total,
                }, ensure_ascii=False),
            }

        # ── Persist after EVERY section ──
        if db:
            try:
                from sqlalchemy import select as sa_select
                result = await db.execute(
                    sa_select(BidProject).where(BidProject.id == project_id)
                )
                db_project = result.scalar_one_or_none()
                if db_project:
                    db_project.generation_state_json = json.dumps(gen_state, ensure_ascii=False)
                    await db.commit()
            except Exception as exc:
                logger.error("Failed to persist generation state: %s", exc)

        # ── Yield overall progress ──
        yield {
            "event": "progress",
            "data": json.dumps({
                "completed": gen_state["completed_leaves"],
                "total": gen_state["total_leaves"],
                "percentage": round(gen_state["completed_leaves"] / max(gen_state["total_leaves"], 1) * 100, 1),
            }, ensure_ascii=False),
        }

    # ── Phase 4: Assemble into chapters ──
    yield {
        "event": "status",
        "data": json.dumps({
            "phase": "assembling",
            "message": "正在组装章节内容...",
        }, ensure_ascii=False),
    }

    # Build generated_sections dict from gen_state
    generated_sections: Dict[str, str] = {}
    for path_key, sec in gen_state["sections"].items():
        if sec.get("status") == "done" and sec.get("content"):
            generated_sections[path_key] = sec["content"]

    chapters_payload = build_final_chapters_payload(tree, generated_sections)

    # ── Phase 5: Yield final result ──
    yield {
        "event": "done",
        "data": json.dumps({
            "chapters_count": len(chapters_payload),
            "total_chars": sum(len(c.get("content", "")) for c in chapters_payload),
            "completed_sections": gen_state["completed_leaves"],
            "total_sections": gen_state["total_leaves"],
            "failed_sections": gen_state["total_leaves"] - gen_state["completed_leaves"],
            "chapters": chapters_payload,
        }, ensure_ascii=False),
    }
```

同时删除原有的线程池相关代码：
- 删除 `completed_sections`、`progress_queue` 等线程池相关变量
- 删除 `_run_generation_in_thread` 嵌套函数
- 删除 `concurrent.futures.ThreadPoolExecutor` 相关代码
- 删除 `asyncio.get_running_loop()` + `run_in_executor` 调用
- 删除 `progress_queue` 的轮询循环

还需要在文件顶部添加 `import datetime`。

- [ ] **Step 4: 验证模块可导入**

```bash
cd backend && python -c "from app.services.ai_pipeline import generate_bid_with_deep_outline, _init_generation_state, _generate_single_section_with_retry; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai_pipeline.py
git commit -m "refactor: rewrite generate_bid_with_deep_outline — remove ThreadPoolExecutor

- Generate sections sequentially in main async context
- Add per-section retry with exponential backoff
- Add generation_state_json init/update helpers
- Support resume from previously completed sections
- Each section persisted immediately after generation"
```

---

### Task 4: 简化 subsection_generator.py

**Files:**
- Modify: `backend/app/services/subsection_generator.py`

**Interfaces:**
- Consumes: `ai_adapter.chat_completion_stream`
- Produces: `generate_section()` — 单节生成（已存在，保持不变）
- Keep: `prepare_outline_tree()`, `get_outline_stats()` — 不变
- Remove: `generate_section_tree()` 的递归逻辑（不再需要批量递归生成）

- [ ] **Step 1: 保留 generate_section 不变，移除 generate_section_tree 递归**

`generate_section()` 单节生成函数保持不变。将 `generate_section_tree` 标记为弃用（或完全删除，因为新逻辑在 `ai_pipeline.py` 中直接循环调用 `generate_section`）。

在 `generate_section_tree` 函数开头添加弃用注释，保留函数体不变（如果有其他地方引用它）：

```python
async def generate_section_tree(
    outline_tree: list,
    requirements: dict,
    company_profile: dict | None = None,
    rag_service=None,
    db=None,
    project_id: str = "",
    progress_callback: Callable | None = None,
    temperature: float = 0.7,
) -> Dict[str, str]:
    """DEPRECATED: Use generate_bid_with_deep_outline() in ai_pipeline.py instead.

    The new pipeline generates sections sequentially in the main async context
    without ThreadPoolExecutor, providing incremental persistence and retry.

    Kept for backward compatibility with any external callers.
    """
    # ... existing implementation unchanged ...
```

- [ ] **Step 2: 验证**

```bash
cd backend && python -c "from app.services.subsection_generator import generate_section, prepare_outline_tree, get_outline_stats; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/subsection_generator.py
git commit -m "refactor: deprecate generate_section_tree in favor of direct per-section loop"
```

---

### Task 5: 历史合同按需求匹配

**Files:**
- Modify: `backend/app/services/rag.py`

**Interfaces:**
- Consumes: `BidProject.parsed_requirements_json`
- Produces: `get_contract_context_for_section()` — 根据章节和需求判断是否注入历史合同

- [ ] **Step 1: 添加历史合同需求检测和检索函数**

在 `rag.py` 文件末尾添加：

```python
# ---------------------------------------------------------------------------
# Historical contract matching for requirements
# ---------------------------------------------------------------------------

CONTRACT_REQUIREMENT_KEYWORDS = [
    "业绩", "合同", "项目经验", "类似项目", "过往项目",
    "成功案例", "中标项目", "承接过", "完成过",
    "项目业绩", "同类项目", "近三年", "近五年",
]


def _has_contract_requirement(requirements: dict) -> bool:
    """Check if the tender explicitly requires historical contracts/performance."""
    # Check service_requirements
    for req in requirements.get("service_requirements", []):
        text = req if isinstance(req, str) else str(req)
        for kw in CONTRACT_REQUIREMENT_KEYWORDS:
            if kw in text:
                return True

    # Check special_requirements
    for req in requirements.get("special_requirements", []):
        text = req if isinstance(req, str) else str(req)
        for kw in CONTRACT_REQUIREMENT_KEYWORDS:
            if kw in text:
                return True

    # Check required_documents
    for doc in requirements.get("required_documents", []):
        name = doc.get("name", "") if isinstance(doc, dict) else str(doc)
        for kw in CONTRACT_REQUIREMENT_KEYWORDS:
            if kw in name:
                return True

    # Check evaluation_criteria
    criteria = requirements.get("evaluation_criteria", "")
    if criteria:
        for kw in CONTRACT_REQUIREMENT_KEYWORDS:
            if kw in criteria:
                return True

    return False


async def get_contract_context_for_section(
    section_title: str,
    requirements: dict,
    db: AsyncSession,
    max_contracts: int = 5,
) -> list[dict]:
    """Retrieve historical contract records relevant to a section.

    If the tender requires contract/performance evidence, returns matched
    contracts for the target section. If no explicit requirement, returns
    contracts only for sections in "投标人认为需要提供的其他内容".
    """
    has_requirement = _has_contract_requirement(requirements)

    # Determine if contracts should be injected for this section
    title_lower = section_title.lower()

    if has_requirement:
        # Inject contracts into relevant sections: 业绩, 资质, 公司, 其他
        relevant_kw = ["业绩", "资质", "公司", "其他", "案例", "合同"]
        if not any(kw in title_lower for kw in relevant_kw):
            return []
    else:
        # Only inject into "其他内容" section
        if not any(kw in title_lower for kw in ["其他", "投标人认为需要提供的"]):
            return []

    # Retrieve historical contracts from archived projects
    from app.models.project import BidProject
    from app.models.qualification import Qualification

    try:
        result = await db.execute(
            select(BidProject)
            .where(BidProject.status == "archived")
            .order_by(BidProject.updated_at.desc())
            .limit(max_contracts)
        )
        archived = result.scalars().all()

        contracts = []
        for proj in archived:
            if proj.name and proj.parsed_requirements_json:
                try:
                    reqs = json.loads(proj.parsed_requirements_json)
                    contracts.append({
                        "project_name": proj.name,
                        "budget": reqs.get("project_budget", ""),
                        "duration": reqs.get("project_duration", ""),
                        "description": reqs.get("project_name", proj.name),
                    })
                except Exception:
                    contracts.append({
                        "project_name": proj.name,
                        "budget": "",
                        "duration": "",
                        "description": proj.name,
                    })

        return contracts
    except Exception as exc:
        logger.warning("Failed to retrieve contract context: %s", exc)
        return []


def format_contract_context(contracts: list[dict]) -> str:
    """Format historical contracts into a context string for AI prompt."""
    if not contracts:
        return ""

    lines = ["\n【历史类似项目业绩 — 以下为公司过往中标/完成的项目，可供参考】"]
    for i, c in enumerate(contracts, 1):
        lines.append(f"{i}. {c.get('project_name', '')}")
        if c.get('budget'):
            lines.append(f"   合同金额：{c['budget']}")
        if c.get('duration'):
            lines.append(f"   服务期限：{c['duration']}")
        if c.get('description'):
            lines.append(f"   项目概况：{c['description']}")
        lines.append("")
    lines.append("请在标书中适当引用上述业绩，注明项目名称和合同金额等信息。")
    return "\n".join(lines)
```

- [ ] **Step 2: 在 _get_section_guidance 中为"其他内容"章节添加引导**

在 `ai_pipeline.py` 的 `_get_section_guidance` 函数中，更新 `其他内容` 分支的引导文本：

```python
if _match_section(title_lower, ["其他", "其他内容", "投标人认为需要提供的"]):
    return """【本章节内容规范 — 投标人认为需要提供的其他内容】
本章节用于补充前三部分未覆盖的证明材料，例如：
1. 公司获奖证书、荣誉证明
2. 类似项目业绩合同关键页（如系统提供了历史合同信息，请在此处列出）
3. ISO管理体系认证证书
4. 企业信用评级报告
5. 其他能证明投标人履约能力的补充材料

严格禁止：
- 不得重复商务部分已有的承诺书
- 不得重复资格审查部分已有的资质证照
- 不得重复技术部分已有的人员配置和服务方案

如确实无补充材料，可简要声明。"""
```

- [ ] **Step 3: 在生成时调用合同匹配**

在 `ai_pipeline.py` 的 `generate_bid_with_deep_outline` 中，生成每个叶子节点前检查并注入合同信息：

在获取 `reference_sections` 的代码块后面添加：

```python
# ── Fetch contract context if applicable ──
contract_context = ""
if db:
    try:
        from app.services.rag import get_contract_context_for_section, format_contract_context
        contracts = await get_contract_context_for_section(
            section_title=leaf.get("title", ""),
            requirements=requirements,
            db=db,
        )
        contract_context = format_contract_context(contracts)
    except Exception as exc:
        logger.debug("Contract context failed for '%s': %s", leaf.get("title", ""), exc)

# Append contract context to reference sections
if contract_context:
    reference_sections.append(contract_context)
```

- [ ] **Step 4: 验证导入**

```bash
cd backend && python -c "from app.services.rag import get_contract_context_for_section, format_contract_context, _has_contract_requirement; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/rag.py backend/app/services/ai_pipeline.py
git commit -m "feat: historical contract injection based on tender requirements

- Detect contract/performance requirements from tender
- If required: inject into qualification/company/other sections
- If not required: inject only into '其他内容' section"
```

---

### Task 6: 更新 /generate API 端点 + 新增 retry-failed

**Files:**
- Modify: `backend/app/api/bid.py`

**Interfaces:**
- Consumes: `generate_bid_with_deep_outline` (新版)
- Produces: `POST /generate` — 适配逐节生成SSE事件
- Produces: `POST /generate/retry-failed/{project_id}` — 重试失败项

- [ ] **Step 1: 更新 /generate 端点中的 deep generation 分支**

在 `bid.py` 的 `event_generator()` 中，将 deep generation 分支（约行262-374）简化为直接调用新版 `generate_bid_with_deep_outline`。

新的 event_generator 中 deep generation 部分：

```python
# ── Deep generation mode (new pipeline) ──
if (
    settings.GENERATION_DEEP_OUTLINE_ENABLED
    and not settings.GENERATION_LEGACY_MODE
):
    async with async_session() as gen_db:
        try:
            # Gather context
            collected = None
            company_profile = None
            matched_qualifications = []
            matched_personnel = []
            try:
                collected = await get_collected_resources(project_id, gen_db)
            except Exception:
                pass
            if collected:
                matched_qualifications = collected.get("qualifications", [])
                matched_personnel = collected.get("personnel", [])
                company_profile = collected.get("company")
            else:
                try:
                    _, matched_qualifications, matched_personnel, source_summary = \
                        await assemble_chapter_context(
                            chapter_title="项目整体",
                            requirements=requirements,
                            project_id=project_id,
                            db=gen_db,
                        )
                    company_profile = source_summary.get("company")
                except Exception:
                    pass

            # Use the NEW deep generation pipeline (no thread pool)
            chapters_payload = None
            async for event in generate_bid_with_deep_outline(
                requirements=requirements,
                company_profile=company_profile,
                matched_qualifications=matched_qualifications,
                matched_personnel=matched_personnel,
                project_id=project_id,
                db=gen_db,
            ):
                yield event
                if event.get("event") == "done":
                    try:
                        done_data = json.loads(event.get("data", "{}"))
                        chapters_payload = done_data.get("chapters")
                    except Exception:
                        chapters_payload = None

            # Save generated chapters to ProjectChapter records
            if chapters_payload:
                for i, ch in enumerate(chapters_payload):
                    try:
                        from sqlalchemy import select as sa_select
                        result_ch = await gen_db.execute(
                            sa_select(ProjectChapter).where(
                                ProjectChapter.project_id == project_id,
                                ProjectChapter.order_index == i + 1,
                            )
                        )
                        db_ch = result_ch.scalar_one_or_none()
                        if db_ch:
                            db_ch.ai_generated_content = ch.get("content", "")
                            db_ch.status = "generated"
                            db_ch.title = ch.get("title", db_ch.title)
                        else:
                            db_ch = ProjectChapter(
                                project_id=project_id,
                                title=ch.get("title", f"第{i + 1}部分"),
                                order_index=i + 1,
                                ai_generated_content=ch.get("content", ""),
                                status="generated",
                            )
                            gen_db.add(db_ch)
                    except Exception:
                        pass
                await gen_db.commit()

            # Mark project for review
            try:
                db_proj = await gen_db.get(BidProject, project_id)
                if db_proj:
                    db_proj.status = "review"
                    await gen_db.commit()
            except Exception:
                pass

            return

        except Exception as exc:
            logger.exception("Deep generation failed: %s", exc)
            try:
                await gen_db.rollback()
                db_proj = await gen_db.get(BidProject, project_id)
                if db_proj:
                    db_proj.status = "error"
                    await gen_db.commit()
            except Exception:
                pass
            yield {
                "event": "error",
                "data": json.dumps(
                    {"message": f"生成失败: {exc}"},
                    ensure_ascii=False,
                ),
            }
            return
```

- [ ] **Step 2: 新增 /generate/retry-failed/{project_id} 端点**

在 `bid.py` 文件末尾添加：

```python
# ---------------------------------------------------------------------------
# POST /generate/retry-failed/{project_id} — Retry failed leaf sections
# ---------------------------------------------------------------------------

@router.post("/generate/retry-failed/{project_id}")
async def retry_failed_sections(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retry generation for leaf sections that failed in a previous run.

    Reads generation_state_json, finds all sections with status='failed',
    resets them to 'pending' and re-runs generation for just those sections.
    """
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    requirements = json.loads(project.parsed_requirements_json)

    # Read current generation state
    gen_state = {}
    if project.generation_state_json:
        try:
            gen_state = json.loads(project.generation_state_json)
        except Exception:
            pass

    if not gen_state.get("sections"):
        raise HTTPException(
            status_code=400,
            detail="No generation state found. Run full generate first.",
        )

    # Find and reset failed sections
    failed_count = 0
    for path_key, sec in gen_state["sections"].items():
        if sec.get("status") == "failed":
            sec["status"] = "pending"
            sec["retries"] = 0
            sec["error"] = None
            failed_count += 1

    if failed_count == 0:
        return {"message": "No failed sections to retry", "retried": 0}

    project.generation_state_json = json.dumps(gen_state, ensure_ascii=False)
    await db.commit()

    # Trigger re-generation (SSE stream)
    async def event_generator():
        async with async_session() as gen_db:
            # Gather context
            company_profile = None
            try:
                collected = await get_collected_resources(project_id, gen_db)
                if collected:
                    company_profile = collected.get("company")
            except Exception:
                pass

            async for event in generate_bid_with_deep_outline(
                requirements=requirements,
                company_profile=company_profile,
                project_id=project_id,
                db=gen_db,
            ):
                yield event

            # Save chapters
            if event.get("event") == "done":
                try:
                    done_data = json.loads(event.get("data", "{}"))
                    chapters_payload = done_data.get("chapters")
                    if chapters_payload:
                        for i, ch in enumerate(chapters_payload):
                            result_ch = await gen_db.execute(
                                select(ProjectChapter).where(
                                    ProjectChapter.project_id == project_id,
                                    ProjectChapter.order_index == i + 1,
                                )
                            )
                            db_ch = result_ch.scalar_one_or_none()
                            if db_ch:
                                db_ch.ai_generated_content = ch.get("content", "")
                                db_ch.status = "generated"
                            else:
                                db_ch = ProjectChapter(
                                    project_id=project_id,
                                    title=ch.get("title", f"第{i + 1}部分"),
                                    order_index=i + 1,
                                    ai_generated_content=ch.get("content", ""),
                                    status="generated",
                                )
                                gen_db.add(db_ch)
                        await gen_db.commit()
                except Exception:
                    pass

    return EventSourceResponse(event_generator())
```

- [ ] **Step 3: 验证语法**

```bash
cd backend && python -c "from app.api.bid import router; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/bid.py
git commit -m "feat: update /generate for per-section streaming, add /generate/retry-failed endpoint"
```

---

### Task 7: 重写前端 GenerationProgress 组件

**Files:**
- Modify: `frontend/src/components/GenerationProgress.tsx`

**Interfaces:**
- Consumes: `outlineTree`, `sectionsState`, `currentGeneratingPath`, `onSectionClick`
- Produces: 树形进度展示组件

- [ ] **Step 1: 重写 GenerationProgress 组件**

```tsx
import { Progress, Tree, Tag, Space, Tooltip, Button, Badge } from 'antd'
import {
  CheckCircleOutlined,
  SyncOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  FileTextOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'
import type { DataNode } from 'antd/es/tree'

interface SectionState {
  status: 'pending' | 'generating' | 'done' | 'failed'
  content: string | null
  char_count: number
  error: string | null
  retries: number
}
interface OutlineNode {
  title: string
  order_index: number
  token_budget_hint: string
  depth: number
  estimated_pages: number
  max_tokens: number
  path: string[]
  children?: OutlineNode[]
}

interface GenerationProgressProps {
  outlineTree: OutlineNode[]
  sectionsState: Record<string, SectionState>
  completed: number
  total: number
  currentSectionTitle: string
  onRetryFailed?: () => void
  failedCount?: number
}

const statusIcon = (status: string) => {
  switch (status) {
    case 'done': return <CheckCircleOutlined style={{ color: '#52c41a' }} />
    case 'generating': return <SyncOutlined spin style={{ color: '#1890ff' }} />
    case 'failed': return <CloseCircleOutlined style={{ color: '#ff4d4f' }} />
    default: return <ClockCircleOutlined style={{ color: '#d9d9d9' }} />
  }
}

const statusColor = (status: string): string => {
  switch (status) {
    case 'done': return 'success'
    case 'generating': return 'processing'
    case 'failed': return 'error'
    default: return 'default'
  }
}

function buildTreeData(
  nodes: OutlineNode[],
  sectionsState: Record<string, SectionState>,
  currentSectionTitle: string,
): DataNode[] {
  return nodes.map((node, idx) => {
    const pathKey = (node.path || []).join(' > ')
    const sec = sectionsState[pathKey]
    const status = sec?.status || 'pending'
    const isCurrent = node.title === currentSectionTitle

    const title = (
      <Space size={4}>
        {statusIcon(isCurrent ? 'generating' : status)}
        <span style={{ fontWeight: node.depth === 0 ? 'bold' : 'normal' }}>
          {node.title}
        </span>
        {sec?.status === 'done' && sec.char_count > 0 && (
          <Tag color="green" style={{ fontSize: 10 }}>{sec.char_count.toLocaleString()}字</Tag>
        )}
        {sec?.status === 'failed' && (
          <Tooltip title={sec.error}>
            <Tag color="red" style={{ fontSize: 10 }}>失败</Tag>
          </Tooltip>
        )}
        {node.estimated_pages > 0 && (
          <span style={{ fontSize: 10, color: '#999' }}>~{node.estimated_pages}页</span>
        )}
      </Space>
    )

    return {
      key: pathKey || node.title,
      title,
      children: node.children ? buildTreeData(node.children, sectionsState, currentSectionTitle) : undefined,
    }
  })
}

export default function GenerationProgress({
  outlineTree,
  sectionsState,
  completed,
  total,
  currentSectionTitle,
  onRetryFailed,
  failedCount = 0,
}: GenerationProgressProps): ReactNode {
  const percent = total === 0 ? 0 : Math.round((completed / total) * 100)
  const treeData = buildTreeData(outlineTree, sectionsState, currentSectionTitle)
  const generatingSec = Object.entries(sectionsState).find(
    ([, s]) => s.status === 'generating'
  )

  return (
    <div style={{ marginBottom: 24 }}>
      {/* Progress bar */}
      <div style={{ marginBottom: 16 }}>
        <Progress
          percent={percent}
          status={failedCount > 0 && completed + failedCount >= total ? 'exception' : 'active'}
          format={() => `${completed} / ${total}`}
        />
        <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
          {generatingSec && (
            <span>
              <SyncOutlined spin /> 正在生成：{currentSectionTitle}
            </span>
          )}
          {!generatingSec && percent >= 100 && (
            <span style={{ color: '#52c41a' }}>
              <CheckCircleOutlined /> 全部生成完成
            </span>
          )}
          {!generatingSec && percent < 100 && percent > 0 && (
            <span style={{ color: '#faad14' }}>生成已暂停（可刷新页面后继续）</span>
          )}
        </div>
      </div>

      {/* Action bar */}
      {failedCount > 0 && onRetryFailed && (
        <div style={{ marginBottom: 12 }}>
          <Button
            icon={<ReloadOutlined />}
            danger
            size="small"
            onClick={onRetryFailed}
          >
            重试 {failedCount} 个失败项
          </Button>
        </div>
      )}

      {/* Outline tree */}
      <div style={{
        maxHeight: 400,
        overflow: 'auto',
        border: '1px solid #f0f0f0',
        borderRadius: 8,
        padding: 12,
      }}>
        <Tree
          showIcon={false}
          defaultExpandAll={false}
          defaultExpandedKeys={outlineTree.slice(0, 2).map(n => (n.path || []).join(' > '))}
          treeData={treeData}
          style={{ fontSize: 13 }}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 验证 TypeScript 编译**

```bash
cd frontend && npx tsc --noEmit src/components/GenerationProgress.tsx
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/GenerationProgress.tsx
git commit -m "feat: rewrite GenerationProgress with tree outline + per-section status icons"
```

---

### Task 8: 集成新进度组件到 ProjectWorkflow

**Files:**
- Modify: `frontend/src/pages/project/ProjectWorkflow.tsx`

- [ ] **Step 1: 更新 SSE 事件处理**

在 SSE `onmessage` 处理中，添加对新事件 `outline_ready`、`section_start`、`section_done`、`section_error`、`progress` 的处理。

在 `ProjectWorkflow.tsx` 中，添加状态：

```tsx
// New state for deep generation
const [outlineTree, setOutlineTree] = useState<any[]>([])
const [sectionsState, setSectionsState] = useState<Record<string, any>>({})
const [deepCompleted, setDeepCompleted] = useState(0)
const [deepTotal, setDeepTotal] = useState(0)
const [deepCurrentTitle, setDeepCurrentTitle] = useState('')
const [failedCount, setFailedCount] = useState(0)
const [deepMode, setDeepMode] = useState(false)  // true = deep outline mode
```

在 SSE event handler 中，添加事件处理分支：

```tsx
// Inside the SSE onmessage handler, add these cases:
} else if (eventType === 'outline_ready') {
  const d = JSON.parse(event.data)
  setOutlineTree(d.outline_tree || [])
  setDeepTotal(d.total_leaves || 0)
  setDeepCompleted(d.completed_from_previous || 0)
  setDeepMode(true)
} else if (eventType === 'section_start') {
  const d = JSON.parse(event.data)
  setDeepCurrentTitle(d.title || '')
  setSectionsState(prev => ({
    ...prev,
    [d.path]: { ...prev[d.path], status: 'generating', content: null }
  }))
} else if (eventType === 'section_done') {
  const d = JSON.parse(event.data)
  setDeepCurrentTitle('')
  setDeepCompleted(prev => prev + 1)
  setSectionsState(prev => ({
    ...prev,
    [d.path]: {
      status: 'done',
      content: d.content,
      char_count: d.char_count || 0,
      error: null,
      retries: 0,
    }
  }))
} else if (eventType === 'section_error') {
  const d = JSON.parse(event.data)
  setDeepCurrentTitle('')
  setFailedCount(prev => prev + 1)
  setSectionsState(prev => ({
    ...prev,
    [d.path]: {
      ...prev[d.path],
      status: 'failed',
      error: d.error,
      retries: d.retry_count || 0,
    }
  }))
} else if (eventType === 'progress') {
  const d = JSON.parse(event.data)
  setDeepCompleted(d.completed || 0)
}
```

- [ ] **Step 2: 条件渲染新/旧进度组件**

在 JSX 中，根据 `deepMode` 判断渲染哪个进度组件：

```tsx
{generating && (
  deepMode ? (
    <GenerationProgress
      outlineTree={outlineTree}
      sectionsState={sectionsState}
      completed={deepCompleted}
      total={deepTotal}
      currentSectionTitle={deepCurrentTitle}
      failedCount={failedCount}
      onRetryFailed={async () => {
        try {
          await client.post(`/bid/generate/retry-failed/${id}`)
          // Re-trigger generation
          setDeepCompleted(0)
          setFailedCount(0)
          startGeneration()
        } catch (e) {
          message.error('重试失败')
        }
      }}
    />
  ) : (
    /* Legacy progress display */
    <GenerationProgress
      chapters={chapters}
      currentChapter={currentChapter}
      completed={completed}
      total={total}
      ragSources={ragSources}
      aiTraces={aiTraces}
    />
  )
)}
```

- [ ] **Step 3: 验证 TypeScript 编译**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/project/ProjectWorkflow.tsx
git commit -m "feat: integrate deep outline progress into ProjectWorkflow with resume support"
```

---

### Task 9: 端到端验证

- [ ] **Step 1: 启动后端确认无导入错误**

```bash
cd backend && python -c "
from app.services.ai_pipeline import generate_bid_with_deep_outline, _init_generation_state, _generate_single_section_with_retry
from app.services.subsection_generator import generate_section, prepare_outline_tree, get_outline_stats
from app.services.rag import get_contract_context_for_section, format_contract_context
from app.api.bid import router
from app.models.project import BidProject
print('All imports OK')
"
```

- [ ] **Step 2: 启动前端确认编译通过**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -20
```

- [ ] **Step 3: 完整流程测试**

```bash
# 1. 启动后端
cd backend && uvicorn app.main:app --reload &

# 2. 启动前端
cd frontend && npm run dev &

# 3. 手动测试流程：
#    - 上传招标文件
#    - 完成信息搜集
#    - 点击一键生成
#    - 观察大纲树展示
#    - 观察逐节进度
#    - 中断后刷新页面验证断点续传
#    - 重试失败项
```

- [ ] **Step 4: Commit final adjustments**

```bash
git add -A
git commit -m "chore: final adjustments after E2E testing"
```

---

## 自检

### 1. Spec 覆盖率

- ✅ 去掉 ThreadPoolExecutor → Task 3
- ✅ 增量持久化 generation_state_json → Task 2 + Task 3
- ✅ 断点续传 → Task 3 (restore from generation_state_json)
- ✅ 重试机制 → Task 3 (_generate_single_section_with_retry)
- ✅ 历史合同按需求匹配 → Task 5
- ✅ 前端树形进度展示 → Task 7
- ✅ 新增 retry-failed API → Task 6
- ✅ 新增配置项 → Task 1

### 2. 占位符检查

无 TBD/TODO/implement later。所有步骤均有完整代码。

### 3. 类型一致性

- `generate_bid_with_deep_outline` → `AsyncIterator[dict]` yields SSE events: `{event, data}` ✅
- `_init_generation_state` → `dict` with `sections`, `total_leaves`, `completed_leaves` ✅
- `_generate_single_section_with_retry` → `tuple[str | None, str | None]` → `(content, error)` ✅
- 前端 `GenerationProgress` props 与 SSE 事件数据结构一致 ✅
