# 分章节生成 + 素材注入 + 章节对话 + 资料多选 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一键生成分章节提交防漏章节；四类素材注入叶子提示词；每节多轮 AI 对话可手动应用；信息搜集四类资源可多选并显示明显选中状态。

**Architecture:** 后端把素材构建抽成公共模块（`materials_context.py`），`generate_from_chapter_structure` 重构为逐章串行（章内叶子并行、失败标红、重跑续传）；信息搜集在 confirm 时批量自动占用确定匹配项、多选真正落库；每节对话走无状态每轮全量发送（沿用 `chapter_chat.py` 模式）。前端补资质多选、三态状态显示、汇总卡片、编辑区对话面板。

**Tech Stack:** FastAPI + SQLAlchemy async + SSE（后端）；React + TypeScript + antd（前端）。生产模型 deepseek-v4-flash（推理模型）。

**Spec:** `docs/superpowers/specs/2026-08-27-per-chapter-generation-and-materials-design.md`

## Global Constraints

- **生产模型 deepseek-v4-flash 是推理模型**：`max_tokens` 预算会被 `reasoning_tokens` 先行消耗。生成叶节预算下限 `GENERATION_LEAF_MIN_TOKENS=2048`，勿降。单节对话 `max_tokens = _budget_hint_to_tokens('medium')`（8192）。
- **素材上下文整体截断 ≤ `MATERIALS_CTX_MAX_CHARS=6000`**，防素材反噬上下文。
- **`children_json` 兼容嵌套树 + 旧扁平任务**（顶层元素含 `path` 键即扁平列表）——所有树操作必须同时支持两种格式。
- **不破坏现有 SSE 事件**：`section_start / section_done / section_error / progress / outline_generated / format_verification / done / status` 必须继续发出；新增 `chapter_start / chapter_done / chapter_error`。
- **保留服务器本地文件**：`backend/app/config.py`、`docker-compose.yml`、`.env`（部署时排除/备份后恢复）。
- 代码风格：中文 docstring + 文件头 copyright（参照现有文件）；改渲染/生成逻辑后跑 `backend/tests/`。
- 后端测试用纯函数 + `unittest.mock.AsyncMock`（无 DB fixture，参照 `test_template_filler.py`）。
- 无 schema 变更：不加列、不加表；`alembic` 保持 head（0006），不需要 upgrade。

---

### Task 1: D 数据层 — 状态合并已落库选择 + 自动占用决策纯函数

**Files:**
- Modify: `backend/app/services/collection.py`（新增纯函数 + 修改 `analyze_collection_needs`）
- Create: `backend/tests/test_collection_merge.py`

**Interfaces:**
- Consumes: `ProjectQualification/ProjectPersonnel/ProjectContract`（`collection.py` 已 import）、`Qualification/Personnel/Contract/CompanyProfile`。
- Produces:
  - `_merge_matches(persisted: list[dict], auto: list[dict], category: str) -> tuple[list[dict], str]` — 合并已落库选择与自动匹配候选，返回 `(matches, match_status)`。
  - `_is_confident_auto(category: str, auto: list[dict]) -> bool` — 自动匹配是否"确定性"（排除"返回全部候选"兜底分支，且候选带真实 `id`）。
  - `_build_personnel_item(role, certs, count, pp_rows, personnel_list)`、`_build_document_item(...)`、`_build_contract_item(...)` — 见实现步骤。
  - `analyze_collection_needs` 输出增强：每项 `match_status ∈ {"selected","uploaded","auto","matched","missing"}`，`matches[]` 每项加 `selection`（`"selected" | "auto"`），已落库选择带 `link_id`。

**逻辑说明（裁定）**：`status` GET 保持只读。已落库选择（含 auto 行）优先展示；无落库时确定性自动匹配标 `match_status="auto"`（confirm 时才会批量落库）；兜底候选标 `matched`；无候选标 `missing`。

- [ ] **Step 1: 写失败测试**

```python
"""宏曦标书 - 资料选择合并逻辑 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.collection import (
    _is_confident_auto,
    _merge_matches,
    _pick_auto_occupy,
)


class TestMergeMatches:
    def test_persisted_selection_wins(self):
        persisted = [{"source": "selected", "selection": "selected", "name": "保安服务许可证", "id": "q1"}]
        auto = [{"source": "qualification", "name": "保安服务许可证", "id": "q2"}]
        matches, status = _merge_matches(persisted, auto, "qualification")
        assert status == "selected"
        assert len(matches) == 1
        assert matches[0]["id"] == "q1"

    def test_auto_candidate_when_no_selection(self):
        auto = [{"source": "qualification", "name": "保安服务许可证", "id": "q2"}]
        matches, status = _merge_matches([], auto, "qualification")
        assert status == "auto"
        assert matches == auto

    def test_missing_when_nothing(self):
        matches, status = _merge_matches([], [], "qualification")
        assert status == "missing"
        assert matches == []

    def test_multiple_persisted_all_returned(self):
        persisted = [{"name": "A", "id": "1"}, {"name": "B", "id": "2"}]
        matches, status = _merge_matches(persisted, [], "personnel")
        assert status == "selected"
        assert len(matches) == 2


class TestIsConfidentAuto:
    def test_qualification_with_id_is_confident(self):
        assert _is_confident_auto(
            "qualification", [{"id": "q1", "source": "qualification", "confidence": "high"}]
        ) is True

    def test_company_source_match_not_confident(self):
        # 公司资料（营业执照等）无 id、无 confidence，不可落库为 ProjectQualification
        assert _is_confident_auto("qualification", [{"source": "company", "name": "营业执照"}]) is False

    def test_personnel_fallback_candidates_not_confident(self):
        # 无标签匹配时 _match_personnel 返回前 5 人（confidence=low），不可自动占用
        assert _is_confident_auto(
            "personnel", [{"id": "p1", "confidence": "low"}, {"id": "p2", "confidence": "low"}]
        ) is False

    def test_personnel_tag_match_is_confident(self):
        assert _is_confident_auto("personnel", [{"id": "p1", "confidence": "high"}]) is True

    def test_contract_with_id_is_confident(self):
        assert _is_confident_auto(
            "contract", [{"id": "c1", "source": "contract", "confidence": "high"}]
        ) is True


class TestPickAutoOccupy:
    def test_picks_confident_auto_qualification_only(self):
        items = [{
            "requirement": {"name": "保安服务许可证", "category": "qualification"},
            "match_status": "auto",
            "matches": [
                {"id": "q1", "confidence": "high"},
                {"id": "q2", "confidence": "low"},
                {"source": "company", "name": "营业执照"},
            ],
        }]
        rows = _pick_auto_occupy(items, [])
        assert rows == [
            {"model": "qualification", "requirement_name": "保安服务许可证", "resource_id": "q1"},
        ]

    def test_skips_missing_and_matched(self):
        items = [
            {"requirement": {"name": "A", "category": "qualification"}, "match_status": "missing", "matches": []},
            {"requirement": {"name": "B", "category": "qualification"}, "match_status": "matched", "matches": [{"id": "q9", "confidence": "high"}]},
        ]
        assert _pick_auto_occupy(items, []) == []

    def test_personnel_and_contract_branches(self):
        doc = [{"requirement": {"name": "业绩合同", "category": "contract_performance"}, "match_status": "auto", "matches": [{"id": "c1", "confidence": "high"}]}]
        per = [{"requirement": {"name": "项目经理"}, "match_status": "auto", "matches": [{"id": "p1", "confidence": "high"}]}]
        rows = _pick_auto_occupy(doc, per)
        assert rows == [
            {"model": "contract", "requirement_name": "业绩合同", "resource_id": "c1"},
            {"model": "personnel", "requirement_name": "项目经理", "resource_id": "p1"},
        ]
```

- [ ] **Step 2: 运行测试确认失败**

Run（backend 目录）：`python -m pytest tests/test_collection_merge.py -v`
Expected: FAIL — `ModuleNotFoundError`（`_merge_matches`/`_is_confident_auto` 未定义）。

- [ ] **Step 3: 实现纯函数**

在 `backend/app/services/collection.py` 的 `_fuzzy_match` 之后新增：

```python
def _merge_matches(
    persisted: list[dict],
    auto: list[dict],
    category: str,
) -> tuple[list[dict], str]:
    """合并已落库选择与自动匹配候选.

    Returns:
        (matches, match_status):
        - 有已落库选择 → (persisted, "selected")
        - 无已落库、有确定性自动候选 → (auto, "auto")
        - 无已落库、仅有非确定性候选 → (auto, "matched")
        - 全无 → ([], "missing")
    """
    if persisted:
        return persisted, "selected"
    if not auto:
        return [], "missing"
    if _is_confident_auto(category, auto):
        return auto, "auto"
    return auto, "matched"


def _is_confident_auto(category: str, auto: list[dict]) -> bool:
    """自动匹配是否确定性（可安全自动占用）.

    确定性条件：
    - 候选带真实资源 id（排除公司资料无 id 的匹配，如营业执照）
    - 候选带 confidence="high"（排除人员"无标签返回前 N 人"、合同
      "无年份/服务关键词返回前 N 条"的兜底分支）
    """
    if not auto:
        return False
    first = auto[0]
    if not first.get("id"):
        return False
    return first.get("confidence") == "high"


def _pick_auto_occupy(document_items: list, personnel_items: list) -> list[dict]:
    """从三态条目中选出需要自动占用的行（纯计算，不触库）.

    Returns:
        list of {"model", "requirement_name", "resource_id"}
        model ∈ {"qualification", "personnel", "contract"}
    """
    rows: list[dict] = []
    for item in document_items:
        if item.get("match_status") != "auto":
            continue
        req_name = item["requirement"]["name"]
        category = item["requirement"].get("category", "other")
        for m in item.get("matches", []):
            if not m.get("id") or m.get("confidence") != "high":
                continue
            model = "contract" if category == "contract_performance" else "qualification"
            rows.append({"model": model, "requirement_name": req_name, "resource_id": m["id"]})
    for item in personnel_items:
        if item.get("match_status") != "auto":
            continue
        role = item["requirement"]["name"]
        for m in item.get("matches", []):
            if not m.get("id") or m.get("confidence") != "high":
                continue
            rows.append({"model": "personnel", "requirement_name": role, "resource_id": m["id"]})
    return rows
```

- [ ] **Step 4: 运行测试确认通过**

Run（backend 目录）：`python -m pytest tests/test_collection_merge.py -v`
Expected: PASS（TestMergeMatches 4 + TestIsConfidentAuto 5 + TestPickAutoOccupy 3 = 12 例全绿）。

- [ ] **Step 5: 给三个 `_match_*` 函数打 confidence 标记**

`_is_confident_auto` 依赖 `confidence` 字段区分「信号匹配」与「兜底候选」。给三个自动匹配函数打标：

`_match_document`（`collection.py:144-152`）的 qualification 匹配分支，dict 加 `"confidence": "high"`：

```python
    # Check qualifications
    for q in quals:
        if _fuzzy_match(name, q.name):
            matches.append({
                "source": "qualification",
                "id": q.id,
                "name": q.name,
                "cert_number": q.cert_number or "",
                "issuing_authority": q.issuing_authority or "",
                "confidence": "high",
            })
```

（company 分支不加 `confidence` 也无 id，天然不算确定性。）

`_match_personnel`（`collection.py:157-182`）：标签/证书命中 → high；兜底前 5 人 → low。同时给 `_personnel_to_dict`（`collection.py:259-267`）加 `confidence` 参数：

```python
def _personnel_to_dict(p: Personnel, confidence: str = "high") -> Dict[str, Any]:
    return {
        "source": "personnel",
        "id": p.id,
        "name": p.name,
        "education": p.education or "",
        "phone": p.phone or "",
        "tags": p.tags or "",
        "confidence": confidence,
    }
```

```python
    for p in personnel_list:
        tags = (p.tags or "").lower()
        # Match by tags containing role keywords or certifications
        role_keywords = role.lower().replace("项目", "").replace("负责人", "").replace("人员", "")
        if role_keywords and role_keywords in tags:
            matches.append(_personnel_to_dict(p, confidence="high"))
            continue

        for cert in certs:
            if cert.lower() in tags:
                matches.append(_personnel_to_dict(p, confidence="high"))
                break

    # If no tag-based match, return all personnel as candidates
    if not matches and personnel_list:
        matches = [_personnel_to_dict(p, confidence="low") for p in personnel_list[:5]]
```

`_match_contracts`（`collection.py:185-256`）：信号匹配（年份/服务关键词命中）→ `"confidence": "high"`；兜底前 10 条 → `"confidence": "low"`。两处 dict 各加一个 `"confidence": ...` 键（signal 分支在 `collection.py:231-239`，fallback 分支在 `collection.py:243-253`）。

- [ ] **Step 6: 改 `analyze_collection_needs` 合并已落库选择**

在 `analyze_collection_needs`（`collection.py:30`）中，加载库数据之后新增已落库选择加载：

```python
    # ── 加载本项目已落库选择（用户手动链接 + 历史 auto）──
    pq_rows = (await db.execute(
        select(ProjectQualification)
        .where(ProjectQualification.project_id == project_id)
        .options(selectinload(ProjectQualification.qualification))
    )).scalars().all()
    pp_rows = (await db.execute(
        select(ProjectPersonnel)
        .where(ProjectPersonnel.project_id == project_id)
        .options(selectinload(ProjectPersonnel.personnel))
    )).scalars().all()
    pc_rows = (await db.execute(
        select(ProjectContract)
        .where(ProjectContract.project_id == project_id)
        .options(selectinload(ProjectContract.contract))
    )).scalars().all()
```

（`selectinload` 需要 import：`from sqlalchemy.orm import selectinload`。）

把 required_docs 循环改为（替换 `collection.py:70-85`）：

```python
    for doc in required_docs:
        name = doc["name"] if isinstance(doc, dict) else str(doc)
        category = doc.get("category", "other") if isinstance(doc, dict) else "other"

        if category == "contract_performance":
            auto = _match_contracts(name, contracts)
            persisted = [
                {
                    "source": "contract", "selection": "selected",
                    "id": pc.contract_id, "link_id": pc.id,
                    "name": pc.contract.project_name if pc.contract else pc.requirement_name,
                    "procurement_unit": pc.contract.procurement_unit if pc.contract else "",
                    "contract_amount": pc.contract.contract_amount if pc.contract else "",
                    "contract_date": str(pc.contract.contract_date) if pc.contract and pc.contract.contract_date else "",
                }
                for pc in pc_rows if pc.requirement_name == name
            ]
            matches, match_status = _merge_matches(persisted, auto, "contract")
        else:
            auto = _match_document(name, category, quals, company)
            persisted = [
                {
                    "source": "qualification", "selection": "selected",
                    "id": pq.qualification_id, "link_id": pq.id,
                    "name": pq.qualification.name if pq.qualification else pq.requirement_name,
                    "cert_number": pq.qualification.cert_number if pq.qualification else "",
                    "issuing_authority": pq.qualification.issuing_authority if pq.qualification else "",
                }
                for pq in pq_rows if pq.requirement_name == name
            ]
            matches, match_status = _merge_matches(persisted, auto, category)
            # 自动候选标 selection=auto（已落库选择在 persisted 里已是 selected）
            if match_status in ("auto", "matched"):
                for m in matches:
                    m.setdefault("selection", "auto")

        document_items.append({
            "requirement": {"name": name, "category": category},
            "matched": match_status in ("selected", "auto", "uploaded"),
            "matches": matches,
            "match_status": match_status,
        })
```

required_personnel 循环改为（替换 `collection.py:86-103`）：

```python
    for p_req in required_personnel:
        role = p_req.get("role", "") if isinstance(p_req, dict) else str(p_req)
        certs = p_req.get("certifications", []) if isinstance(p_req, dict) else []
        count = p_req.get("count", 1) if isinstance(p_req, dict) else 1

        auto = _match_personnel(role, certs, personnel_list)
        persisted = [
            {
                "source": "personnel", "selection": "selected",
                "id": pp.personnel_id, "link_id": pp.id,
                "name": pp.personnel.name if pp.personnel else "(未指定)",
                "education": pp.personnel.education if pp.personnel else "",
                "tags": pp.personnel.tags if pp.personnel else "",
                "role": pp.role,
            }
            for pp in pp_rows if pp.role == role
        ]
        matches, match_status = _merge_matches(persisted, auto, "personnel")
        if match_status in ("auto", "matched"):
            for m in matches:
                m.setdefault("selection", "auto")
        personnel_items.append({
            "requirement": {
                "name": role,
                "category": "personnel",
                "details": f"需{count}人" if count > 1 else "",
            },
            "matched": match_status in ("selected", "auto", "uploaded"),
            "matches": matches,
            "match_status": match_status,
        })
```

同时把 `matched` 布尔改为上面所示（`uploaded` 也视为已满足）。`is_complete` 计算改为：

```python
    all_matched = all(
        item["match_status"] in ("selected", "auto", "uploaded")
        for item in document_items + personnel_items
    )
```

- [ ] **Step 7: confirm_collection 批量自动占用（幂等）**

新增落库函数（放在 `_pick_auto_occupy` 之后）：

```python
async def _auto_occupy_confident_matches(
    project_id: str,
    rows: list[dict],
    db: AsyncSession,
) -> int:
    """按 _pick_auto_occupy 的结果批量落库（幂等）.

    幂等策略：若该需求已有任何已落库行则整体跳过（不重复占用）。
    """
    occupied = 0
    for spec in rows:
        model_name = spec["model"]
        req_name = spec["requirement_name"]
        if model_name == "qualification":
            existing = (await db.execute(
                select(ProjectQualification).where(
                    ProjectQualification.project_id == project_id,
                    ProjectQualification.requirement_name == req_name,
                )
            )).scalars().all()
        elif model_name == "personnel":
            existing = (await db.execute(
                select(ProjectPersonnel).where(
                    ProjectPersonnel.project_id == project_id,
                    ProjectPersonnel.role == req_name,
                )
            )).scalars().all()
        else:
            existing = (await db.execute(
                select(ProjectContract).where(
                    ProjectContract.project_id == project_id,
                    ProjectContract.requirement_name == req_name,
                )
            )).scalars().all()
        if existing:
            continue
        if model_name == "qualification":
            db.add(ProjectQualification(
                project_id=project_id,
                qualification_id=spec["resource_id"],
                requirement_name=req_name,
                match_status="matched",
            ))
        elif model_name == "personnel":
            db.add(ProjectPersonnel(
                project_id=project_id,
                personnel_id=spec["resource_id"],
                role=req_name,
                requirement_desc=req_name,
                match_status="assigned",
            ))
        else:
            db.add(ProjectContract(
                project_id=project_id,
                contract_id=spec["resource_id"],
                requirement_name=req_name,
                match_status="matched",
            ))
        occupied += 1
    if occupied:
        await db.flush()
    return occupied
```

把 `confirm_collection`（`collection.py:390-398`）改为（自动占用失败只告警、不阻断确认）：

```python
async def confirm_collection(project_id: str, db: AsyncSession) -> BidProject:
    """Mark collection as complete and advance the project to 'parsed'."""
    project = await db.get(BidProject, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")

    # 批量自动占用确定性自动匹配（幂等：只为零落库的需求占用，用户可随后移除/替换）
    try:
        items = await analyze_collection_needs(project_id, db)
        rows = _pick_auto_occupy(items["document_items"], items["personnel_items"])
        if rows:
            await _auto_occupy_confident_matches(project_id, rows, db)
            logger.info("confirm_collection auto-occupied %d rows", len(rows))
    except Exception as exc:
        logger.warning("Auto-occupy skipped: %s", exc)

    project.status = "parsed"
    await db.flush()
    await db.refresh(project)
    return project
```

- [ ] **Step 8: 跑全量 collection 相关测试**

Run（backend 目录）：`python -m pytest tests/ -k "collection or merge" -v`
Expected: PASS（新增 8 例 + 现有无 collection 测试，全部通过；`analyze_collection_needs` 改动不影响其它测试）。

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/collection.py backend/tests/test_collection_merge.py
git commit -m "feat: 信息搜集状态合并已落库选择+三态+confirm自动占用
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: D 数据层 — 多选真正落库 + unlink 端点

**Files:**
- Modify: `backend/app/services/collection.py`（`assign_personnel`/`link_contract` 去删旧逻辑；新增 `unlink_qualification`/`unlink_contract`）
- Modify: `backend/app/api/collection.py`（新增 2 个端点）
- Modify: `backend/app/schemas/collection.py`（新增 unlink 请求模型）
- Create: `backend/tests/test_collection_unlink.py`

**Interfaces:**
- Consumes: Task 1 的 `_merge_matches`（不直接依赖，只复用状态语义）。
- Produces:
  - `unlink_qualification(project_id, requirement_name, resource_id, db) -> bool`
  - `unlink_contract(project_id, requirement_name, resource_id, db) -> bool`
  - `POST /collection/{project_id}/qualification/unlink`（body `{requirement_name, resource_id}`）
  - `POST /collection/{project_id}/contract/unlink`（body `{requirement_name, resource_id}`）
  - `assign_personnel`：不再删同 role 旧记录
  - `link_contract`：不再删同 requirement 旧记录

- [ ] **Step 1: 写失败测试**

```python
"""宏曦标书 - 资料解除关联 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from unittest.mock import AsyncMock, patch
import pytest
from app.services.collection import (
    assign_personnel,
    link_contract,
    unlink_contract,
    unlink_qualification,
)


class TestUnlinkQualification:
    async def test_deletes_row_by_resource_id(self):
        db = AsyncMock()
        fake = AsyncMock()
        fake.qualification_id = "q1"
        fake.id = "pq1"
        fake.requirement_name = "保安服务许可证"
        # 模拟查询结果：两行，一行匹配要删的
        result = AsyncMock()
        result.scalars.return_value.all.return_value = [
            AsyncMock(id="pq1", requirement_name="保安服务许可证", qualification_id="q1"),
            AsyncMock(id="pq2", requirement_name="保安服务许可证", qualification_id="q2"),
        ]
        db.execute.return_value = result
        with patch("app.services.collection.select"):
            deleted = await unlink_qualification("proj1", "保安服务许可证", "q1", db)
        assert deleted is True
        assert db.delete.call_count == 1


class TestUnlinkContract:
    async def test_deletes_row(self):
        db = AsyncMock()
        result = AsyncMock()
        result.scalars.return_value.all.return_value = [
            AsyncMock(id="pc1", requirement_name="业绩合同", contract_id="c1"),
        ]
        db.execute.return_value = result
        with patch("app.services.collection.select"):
            deleted = await unlink_contract("proj1", "业绩合同", "c1", db)
        assert deleted is True
        assert db.delete.call_count == 1


class TestMultiSelectPersists:
    """去掉删旧逻辑后，第二次 assign/link 不再删除第一次的记录.

    旧实现会在 assign/link 前 `for old in existing.scalars(): db.delete(old)`。
    AsyncMock 的 scalars() 不可迭代，旧实现会直接 TypeError → 测试红；
    新实现不触 execute/delete → 断言通过。
    """
    async def test_assign_personnel_does_not_delete_previous(self):
        db = AsyncMock()
        with patch("app.services.collection.select"):
            await assign_personnel("proj1", "p1", "项目经理", "", db)
        db.delete.assert_not_called()

    async def test_link_contract_does_not_delete_previous(self):
        db = AsyncMock()
        with patch("app.services.collection.select"):
            await link_contract("proj1", "c1", "业绩合同", db)
        db.delete.assert_not_called()
```

（说明：`TestMultiSelectPersists` 的核心断言是"服务实现里不再有 `db.delete(old)` 调用"——以代码审查为准，测试作回归哨兵。）

- [ ] **Step 2: 运行测试确认失败**

Run（backend 目录）：`python -m pytest tests/test_collection_unlink.py -v`
Expected: FAIL — `ImportError: cannot import name 'unlink_qualification'`。

- [ ] **Step 3: 实现服务函数**

在 `collection.py` 中修改 `assign_personnel`（`collection.py:280-308`）：删除 `# Remove any previous assignment...` 整段（`collection.py:288-296`），直接创建新行。

修改 `link_contract`（`collection.py:361-387`）：删除 `# Remove any previous contract...` 整段（`collection.py:368-376`），直接创建新行。

新增两个 unlink 函数（放在 `unassign_personnel` 之后）：

```python
async def unlink_qualification(
    project_id: str,
    requirement_name: str,
    resource_id: str,
    db: AsyncSession,
) -> bool:
    """解除某需求下指定资质链接（resource_id 为 qualification_id）."""
    rows = (await db.execute(
        select(ProjectQualification).where(
            ProjectQualification.project_id == project_id,
            ProjectQualification.requirement_name == requirement_name,
            ProjectQualification.qualification_id == resource_id,
        )
    )).scalars().all()
    for pq in rows:
        await db.delete(pq)
    await db.flush()
    return len(rows) > 0


async def unlink_contract(
    project_id: str,
    requirement_name: str,
    resource_id: str,
    db: AsyncSession,
) -> bool:
    """解除某业绩要求下指定合同链接（resource_id 为 contract_id）."""
    rows = (await db.execute(
        select(ProjectContract).where(
            ProjectContract.project_id == project_id,
            ProjectContract.requirement_name == requirement_name,
            ProjectContract.contract_id == resource_id,
        )
    )).scalars().all()
    for pc in rows:
        await db.delete(pc)
    await db.flush()
    return len(rows) > 0
```

- [ ] **Step 4: 新增 schemas 与端点**

`schemas/collection.py` 新增：

```python
class UnlinkResourceRequest(BaseModel):
    requirement_name: str = ""
    resource_id: str = ""
```

`api/collection.py` 在 `unassign_personnel_from_project` 之后新增：

```python
@router.post("/{project_id}/qualification/unlink")
async def unlink_qualification_from_project(
    project_id: str,
    data: UnlinkResourceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """解除某需求下指定资质的链接."""
    deleted = await unlink_qualification(
        project_id, data.requirement_name, data.resource_id, db
    )
    return {"deleted": deleted}


@router.post("/{project_id}/contract/unlink")
async def unlink_contract_from_project(
    project_id: str,
    data: UnlinkResourceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """解除某业绩要求下指定合同的链接."""
    deleted = await unlink_contract(
        project_id, data.requirement_name, data.resource_id, db
    )
    return {"deleted": deleted}
```

并更新 import：`UnlinkResourceRequest` 加入 `from app.schemas.collection import (...)`；`unlink_qualification, unlink_contract` 加入 `from app.services.collection import (...)`。

- [ ] **Step 5: 运行测试确认通过**

Run（backend 目录）：`python -m pytest tests/test_collection_unlink.py -v`
Expected: PASS。

- [ ] **Step 6: 全量回归**

Run（backend 目录）：`python -m pytest tests/ -v`
Expected: 现有套件全绿（`test_format_template_to_prompt_text` 的乱码失败为 Windows 环境问题，忽略）。

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/collection.py backend/app/api/collection.py backend/app/schemas/collection.py backend/tests/test_collection_unlink.py
git commit -m "feat: 资料多选真正落库 + 资质/合同解除关联端点
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: B — 素材上下文公共模块 `materials_context.py`

**Files:**
- Create: `backend/app/services/materials_context.py`
- Create: `backend/tests/test_materials_context.py`

**Interfaces:**
- Consumes: `get_collected_resources` 返回的四类 dict 结构（见 `collection.py:401-481`）。
- Produces:
  - `build_qualifications_context(qualifications: list) -> str`
  - `build_personnel_context(personnel: list) -> str`
  - `build_contract_context(contracts: list) -> str`
  - `build_company_context(company: dict | None) -> str`（复用 `subsection_generator.build_company_info_block`，见 Step 3 说明）
  - `assemble_section_materials(section_title, *, qualifications=None, personnel=None, contracts=None, company=None) -> str`
  - 常量 `QUAL_CTX_KEYWORDS / CONTRACT_KEYWORDS / PERSONNEL_CTX_KEYWORDS / MATERIALS_CTX_MAX_CHARS = 6000`

- [ ] **Step 1: 写失败测试**

```python
"""宏曦标书 - 素材上下文构建 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.materials_context import (
    build_qualifications_context,
    build_contract_context,
    build_personnel_context,
    assemble_section_materials,
)


class TestBuildQualificationsContext:
    def test_formats_qualifications(self):
        quals = [
            {"name": "保安服务许可证", "cert_number": "云保001", "issuing_authority": "云南省公安厅"},
        ]
        text = build_qualifications_context(quals)
        assert "保安服务许可证" in text
        assert "云保001" in text
        assert "严禁编造" in text

    def test_empty_returns_empty(self):
        assert build_qualifications_context([]) == ""


class TestAssembleSectionMaterials:
    def test_qualification_section_gets_qual_ctx(self):
        text = assemble_section_materials(
            "公司资质及资格证书",
            qualifications=[{"name": "保安服务许可证", "cert_number": "001"}],
            personnel=[{"name": "李四", "role": "项目经理"}],
            contracts=[{"project_name": "某项目"}],
        )
        assert "保安服务许可证" in text
        assert "李四" not in text      # 资质章节不注入人员
        assert "某项目" not in text    # 资质章节不注入合同

    def test_personnel_section_gets_personnel_ctx(self):
        text = assemble_section_materials(
            "拟投入项目人员配置表",
            qualifications=[{"name": "保安服务许可证"}],
            personnel=[{"name": "李四", "role": "项目经理"}],
            contracts=[{"project_name": "某项目"}],
        )
        assert "李四" in text
        assert "保安服务许可证" not in text

    def test_company_always_injected(self):
        text = assemble_section_materials(
            "服务方案",
            company={"company_name": "云南领航保安服务有限公司"},
        )
        assert "云南领航保安服务有限公司" in text

    def test_truncated_to_max_chars(self):
        big = [{"name": "证书A", "cert_number": "X" * 5000} for _ in range(10)]
        text = assemble_section_materials("资质证书", qualifications=big)
        assert len(text) <= 6000
```

- [ ] **Step 2: 运行测试确认失败**

Run（backend 目录）：`python -m pytest tests/test_materials_context.py -v`
Expected: FAIL — `ModuleNotFoundError: materials_context`。

- [ ] **Step 3: 实现模块**

新建 `backend/app/services/materials_context.py`：

```python
"""宏曦标书 - 素材上下文构建.

把公司资质 / 人员 / 历史合同 / 公司信息四类素材按章节标题关键词
组装成 AI 提示上下文。供生成管线、单节重新生成、章节对话共用。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MATERIALS_CTX_MAX_CHARS = 6000

# 关键词组：标题命中即注入对应素材块
QUAL_CTX_KEYWORDS = [
    "资质", "证书", "资格", "认证", "许可证", "营业执照",
    "证件", "证明文件", "质量管理", "管理体系",
]
CONTRACT_KEYWORDS = [
    "业绩", "类似项目", "项目经验", "成功案例", "既往", "合同业绩",
    "投标人认为需要提供的其他", "其他内容", "其他材料",
]
PERSONNEL_CTX_KEYWORDS = [
    "人员", "配置", "团队", "组织", "人力", "管理架构", "岗位",
    "项目负责人", "项目经理", "拟投入", "技术负责人",
]


def build_qualifications_context(qualifications: list) -> str:
    """格式化公司资质为提示上下文."""
    items = [q for q in (qualifications or []) if q and q.get("name")]
    if not items:
        return ""
    lines = ["【公司资质证件数据 — 以下为真实资质数据，标书中涉及资质、证书时必须原样使用，严禁编造】"]
    for q in items:
        lines.append(f"  - {q['name']}")
        if q.get("cert_number"):
            lines.append(f"    证书编号：{q['cert_number']}")
        if q.get("issuing_authority"):
            lines.append(f"    发证机构：{q['issuing_authority']}")
    lines.append("重要提醒：标书中涉及资质证书时，必须使用以上真实资质数据，不得编造证书编号或机构。")
    return "\n".join(lines)


def build_personnel_context(personnel: list) -> str:
    """格式化项目人员为提示上下文."""
    lines = ["【可用项目人员 — 以下为真实人员数据，标书中涉及人员配置时必须使用，严禁编造姓名、证书等信息】"]
    count = 0
    for p in (personnel or []):
        if not p or not p.get("name"):
            continue
        role = p.get("role", "")
        edu = p.get("education", "")
        tags = p.get("tags", "")
        certs = p.get("certificates") or []
        lines.append(f"  - {p['name']}（{role}，学历{edu}）")
        if tags:
            lines.append(f"    持证/特长：{tags}")
        for c in certs:
            cn = c.get("cert_name", "") if isinstance(c, dict) else getattr(c, "cert_name", "")
            if cn:
                lines.append(f"    证书：{cn}")
        count += 1
    if count == 0:
        return ""
    lines.append("重要提醒：标书中涉及人员配置时，只能使用以上真实人员数据，严禁编造任何人名或证书信息。")
    return "\n".join(lines)


def build_contract_context(contracts: list) -> str:
    """格式化历史合同为提示上下文."""
    items = [c for c in (contracts or []) if c and c.get("project_name")]
    if not items:
        return ""
    lines = ["【历史合同业绩数据 — 以下为真实项目数据，必须在标书中原样使用，严禁编造项目名称、金额等信息】"]
    for i, c in enumerate(items, 1):
        lines.append(f"{i}. 项目名称：{c['project_name']}")
        if c.get("procurement_unit"):
            lines.append(f"   采购单位：{c['procurement_unit']}")
        if c.get("contract_amount"):
            lines.append(f"   合同金额：{c['contract_amount']}")
        if c.get("contract_date"):
            lines.append(f"   合同日期：{c['contract_date']}")
        lines.append("")
    lines.append("重要提醒：标书中涉及项目业绩时，必须使用以上真实项目数据，不得自行编造。")
    return "\n".join(lines)


def build_company_context(company: dict | None) -> str:
    """格式化公司信息为提示上下文.

    字段来自 get_collected_resources 的 company 块（company_name /
    business_license_number / legal_rep_name / legal_rep_id_number /
    address / contact_phone / website / notes）。字段缺失自动跳过。
    """
    if not company:
        return ""
    parts: list[str] = []
    if company.get("company_name"):
        parts.append(f"公司名称：{company['company_name']}")
    if company.get("business_license_number"):
        parts.append(f"统一社会信用代码：{company['business_license_number']}")
    if company.get("legal_rep_name"):
        parts.append(f"法定代表人：{company['legal_rep_name']}")
    if company.get("legal_rep_id_number"):
        parts.append(f"法定代表人身份证号：{company['legal_rep_id_number']}")
    if company.get("address"):
        parts.append(f"注册地址：{company['address']}")
    if company.get("contact_phone"):
        parts.append(f"联系电话：{company['contact_phone']}")
    if company.get("website"):
        parts.append(f"公司网址：{company['website']}")
    if not parts:
        return ""
    return "\n".join([
        "【公司基本信息 — 以下为真实公司数据，标书中涉及公司信息时必须原样使用，严禁编造】",
        *parts,
        "重要提醒：标书中公司名称、统一社会信用代码等必须以真实数据为准，不得编造。",
    ])


def assemble_section_materials(
    section_title: str,
    *,
    qualifications: list | None = None,
    personnel: list | None = None,
    contracts: list | None = None,
    company: dict | None = None,
) -> str:
    """按章节标题关键词组装该节所需的素材上下文.

    公司信息始终注入；资质/人员/合同按标题关键词命中注入。
    整体截断到 MATERIALS_CTX_MAX_CHARS，防素材反噬上下文。
    """
    parts: list[str] = []
    title_lower = (section_title or "").lower()

    if any(kw in title_lower for kw in QUAL_CTX_KEYWORDS):
        parts.append(build_qualifications_context(qualifications or []))
    if any(kw in title_lower for kw in PERSONNEL_CTX_KEYWORDS):
        parts.append(build_personnel_context(personnel or []))
    if any(kw in title_lower for kw in CONTRACT_KEYWORDS):
        parts.append(build_contract_context(contracts or []))

    company_ctx = build_company_context(company)
    if company_ctx:
        parts.append(company_ctx)

    joined = "\n\n".join(p for p in parts if p)
    if len(joined) > MATERIALS_CTX_MAX_CHARS:
        joined = joined[:MATERIALS_CTX_MAX_CHARS]
    return joined
```

- [ ] **Step 4: 运行测试确认通过**

Run（backend 目录）：`python -m pytest tests/test_materials_context.py -v`
Expected: PASS（8 例全绿）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/materials_context.py backend/tests/test_materials_context.py
git commit -m "feat: 素材上下文公共模块（资质/人员/合同/公司 + 关键词注入）
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: B — 注入点接线（modify / regenerate / 深管线）

**Files:**
- Modify: `backend/app/services/section_editor.py`（`modify_section` 加 `materials_guidance` 参数并注入 prompt；`regenerate_section` 加 `materials_guidance` 参数传 `extra_guidance`）
- Modify: `backend/app/api/chapters.py`（新增内部 helper `_materials_guidance_for_section`；`modify_section`/`regenerate_section` 端点接线）
- Modify: `backend/app/services/ai_pipeline.py`（深管线 `generate_bid_with_deep_outline` 的 personnel 内联构建改调 `build_personnel_context`，补资质注入——具体改法见 Step 3）

**Interfaces:**
- Consumes: Task 3 的 `assemble_section_materials`。
- Produces:
  - `modify_section(..., materials_guidance: str = "") -> dict`（签名扩展，默认空串不破坏调用方）
  - `regenerate_section(..., materials_guidance: str = "") -> AsyncIterator[str]`
  - `_materials_guidance_for_section(section_title: str, project_id: str, db) -> str`（`chapters.py` 内部）

- [ ] **Step 1: 改 `section_editor.py`**

`modify_section` 签名（`section_editor.py:157-164`）加 `materials_guidance: str = ""`；在 `user_prompt` 的 `【当前内容】` 之前插入素材块：

```python
    materials_block = (
        f"\n【可用的真实素材（标书中必须使用，严禁编造）】\n{materials_guidance}\n"
        if materials_guidance else ""
    )
```

`user_prompt` 改为：

```python
    user_prompt = f"""【文档位置】{ancestry}
【当前节标题】{section_title}

【同级其他节摘要（请避免内容重复）】
{sibling_text}
{materials_block}【当前内容】
{current_content if current_content else "（尚未生成）"}

【修改要求】
{instruction}

请返回修改后的完整内容。只返回内容文本，不要加任何解释、标题或标记。"""
```

`regenerate_section` 签名（`section_editor.py:239-247`）加 `materials_guidance: str = ""`；`generate_section(...)` 调用里把 `extra_guidance=""` 改为 `extra_guidance=materials_guidance`。

- [ ] **Step 2: 改 `chapters.py` 接线**

在 `sections/modify` 端点（`chapters.py:769-807`）里，调用前加素材：

```python
    from app.services.collection import get_collected_resources
    collected = await get_collected_resources(project_id, db)
    section_title = data.section_path[-1] if data.section_path else chapter.title
    materials_guidance = assemble_section_materials(
        section_title,
        qualifications=collected.get("qualifications", []) if collected else None,
        personnel=collected.get("personnel", []) if collected else None,
        contracts=collected.get("contracts", []) if collected else None,
        company=collected.get("company") if collected else None,
    )
```

`do_modify(...)` 调用加 `materials_guidance=materials_guidance`。`sections/regenerate` 端点（`chapters.py:810-879`）同样构建 `materials_guidance`，传给 `do_regenerate`。

- [ ] **Step 3: 改深管线（`ai_pipeline.py`）**

在 `generate_bid_with_deep_outline` 中：
- 把内联 personnel 构建（`ai_pipeline.py:1834-1863`）替换为：

```python
    from app.services.materials_context import (
        assemble_section_materials,
        build_personnel_context,
    )
    personnel_context = build_personnel_context(matched_personnel or [])
```

- pending 循环的注入判定（`ai_pipeline.py:1900-1910`）保持不变（仍用 `CONTRACT_KEYWORDS`/`PERSONNEL_CTX_KEYWORDS`，但这两个常量改为从 `materials_context` import，避免两份定义漂移）；新增资质注入：

```python
        quals_context = build_qualifications_context(matched_qualifications or [])
        if quals_context and any(
            kw in title_lower for kw in QUAL_CTX_KEYWORDS
        ):
            extra_parts.append(quals_context)
```

（import `build_qualifications_context, QUAL_CTX_KEYWORDS`；`CONTRACT_KEYWORDS/PERSONNEL_CTX_KEYWORDS` 改为从 `materials_context` 导入并删除 `ai_pipeline.py:1874-1878` 的本地定义。）

- [ ] **Step 4: 测试**

后端单测已由 Task 3 覆盖（`assemble_section_materials`）。本任务无新纯逻辑，验证方式：

Run（backend 目录）：`python -c "from app.services.materials_context import assemble_section_materials; print(assemble_section_materials('公司资质', qualifications=[{'name':'X','cert_number':'1'}]))"`
Expected: 输出包含"公司资质证件数据"。
Run：`python -m pytest tests/ -v`
Expected: 现有套件全绿。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/section_editor.py backend/app/api/chapters.py backend/app/services/ai_pipeline.py
git commit -m "feat: 素材注入接线（modify/regenerate/深管线补资质）
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: C — 章节对话后端（sections/chat）

**Files:**
- Modify: `backend/app/services/section_editor.py`（新增 `chat_section`）
- Modify: `backend/app/api/chapters.py`（新增 `sections/chat` 端点 + 请求/响应模型）
- Create: `backend/tests/test_section_chat.py`

**Interfaces:**
- Consumes: Task 3 的 `assemble_section_materials`；`_budget_hint_to_tokens`（`ai_pipeline.py:1625`）。
- Produces:
  - `chat_section(chapter_title, section_path, current_content, messages, ai_adapter, *, materials_guidance="") -> dict`，返回 `{"reply": str, "revised_content": str}`
  - `POST /{project_id}/chapters/{chapter_id}/sections/chat`
  - 请求模型 `SectionChatRequest`：`section_path: list`、`current_content: str`、`messages: list[dict]`、`instruction: str = ""`
  - 响应模型 `SectionChatResponse`：`reply: str`、`revised_content: str`

- [ ] **Step 1: 写失败测试**

```python
"""宏曦标书 - 章节对话 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import json
from unittest.mock import AsyncMock
import pytest
from app.services.section_editor import chat_section


class TestChatSection:
    async def test_returns_reply_and_revised_content(self):
        ai = AsyncMock()
        ai.chat_completion.return_value = json.dumps(
            {"reply": "已按你的要求补充了人员配置。", "revised_content": "第一章 服务方案\n新增内容"}
        )
        result = await chat_section(
            chapter_title="第一章",
            section_path=["第一章", "服务方案"],
            current_content="旧内容",
            messages=[{"role": "user", "content": "补充人员配置"}],
            ai_adapter=ai,
            materials_guidance="【素材】人员：李四",
        )
        assert result["reply"] == "已按你的要求补充了人员配置。"
        assert result["revised_content"] == "第一章 服务方案\n新增内容"

    async def test_requires_ai_adapter(self):
        with pytest.raises(RuntimeError):
            await chat_section(
                chapter_title="第一章", section_path=["第一章"],
                current_content="", messages=[], ai_adapter=None,
            )

    async def test_empty_response_raises(self):
        ai = AsyncMock()
        ai.chat_completion.return_value = ""
        with pytest.raises(RuntimeError):
            await chat_section(
                chapter_title="第一章", section_path=["第一章"],
                current_content="", messages=[], ai_adapter=ai,
            )
```

- [ ] **Step 2: 运行测试确认失败**

Run（backend 目录）：`python -m pytest tests/test_section_chat.py -v`
Expected: FAIL — `ImportError: cannot import name 'chat_section'`。

- [ ] **Step 3: 实现 `chat_section`**

在 `section_editor.py` 末尾新增：

```python
SECTION_CHAT_SYSTEM_PROMPT = """你是投标文件章节编辑助手。用户在逐节审阅标书，通过多轮对话修改当前这一节。

约束：
1. 只针对当前节，不要改其他节
2. 保持原文中公司信息、人员姓名、证书编号、项目名称等真实数据不变
3. 直接回答用户的问题，或根据指令修改内容
4. 禁止使用"首先""其次""此外""总而言之"等模板化连接词
5. 每个段落至少包含1个具体事实（数字、日期、项目名、证书编号等）
6. 你必须返回 JSON，格式：{"reply": "对用户指令的回复/说明", "revised_content": "修改后的完整本节内容；若无需修改则原样返回当前内容"}
7. 只返回 JSON，不要包含任何其他文字"""


async def chat_section(
    chapter_title: str,
    section_path: list[str],
    current_content: str,
    messages: list[dict],
    ai_adapter=None,
    *,
    materials_guidance: str = "",
) -> dict:
    """章节多轮对话修改当前节.

    Returns:
        {"reply": str, "revised_content": str}
    """
    if not ai_adapter:
        raise RuntimeError("AI 服务不可用")

    section_title = section_path[-1] if section_path else chapter_title
    ancestry = " > ".join([chapter_title] + section_path[:-1]) if len(section_path) > 1 else chapter_title

    # 历史对话（前端已持有，防上下文膨胀由前端限制条数）
    history = messages[-12:] if messages else []

    materials_block = (
        f"\n【可用的真实素材（标书中必须使用，严禁编造）】\n{materials_guidance}\n"
        if materials_guidance else ""
    )

    user_prompt = f"""【文档位置】{ancestry}
【当前节标题】{section_title}
{materials_block}【当前内容】
{current_content if current_content else "（尚未生成）"}

【对话历史】
{json.dumps(history, ensure_ascii=False) if history else "（无）"}

请根据以上上下文，输出 JSON：{{"reply": "...", "revised_content": "..."}}"""

    from app.services.ai_pipeline import _budget_hint_to_tokens

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": SECTION_CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=_budget_hint_to_tokens("medium"),
            response_format={"type": "json_object"},
        )
        if not response:
            raise RuntimeError("AI 返回空内容")
        parsed = json.loads(response)
        return {
            "reply": parsed.get("reply", ""),
            "revised_content": parsed.get("revised_content", current_content),
        }
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI 返回非 JSON：{response[:200]!r}") from exc
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"章节对话失败：{exc}") from exc
```

- [ ] **Step 4: 新增端点与模型**

`chapters.py` 顶部模型区（`SectionRegenerateRequest` 附近）新增：

```python
class SectionChatRequest(BaseModel):
    section_path: list = Field(..., min_length=1)
    current_content: str = ""
    messages: list = []
    instruction: str = ""


class SectionChatResponse(BaseModel):
    reply: str = ""
    revised_content: str = ""
```

`save_section` 端点之后新增：

```python
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
        from app.services.collection import get_collected_resources
        from app.services.materials_context import assemble_section_materials
        from app.services.section_editor import chat_section as do_chat
        from app.services.ai_adapter import ai_adapter as ai

        collected = await get_collected_resources(project_id, db)
        section_title = data.section_path[-1] if data.section_path else chapter.title
        materials_guidance = assemble_section_materials(
            section_title,
            qualifications=collected.get("qualifications", []) if collected else None,
            personnel=collected.get("personnel", []) if collected else None,
            contracts=collected.get("contracts", []) if collected else None,
            company=collected.get("company") if collected else None,
        )

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
```

- [ ] **Step 5: 运行测试确认通过**

Run（backend 目录）：`python -m pytest tests/test_section_chat.py -v`
Expected: PASS（3 例全绿）。

- [ ] **Step 6: 全量回归**

Run（backend 目录）：`python -m pytest tests/ -v`
Expected: 现有套件全绿。

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/section_editor.py backend/app/api/chapters.py backend/tests/test_section_chat.py
git commit -m "feat: 章节多轮对话端点（回复+修改内容，对话+手动应用）
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: D 前端 — 资质多选 + 三态显示 + 汇总卡片

**Files:**
- Modify: `frontend/src/pages/project/QualificationPickerModal.tsx`
- Modify: `frontend/src/pages/project/CollectionStep.tsx`
- Test: `cd frontend && npm run build`（gate）

**Interfaces:**
- Consumes: Task 1/2 的后端契约（`match_status` 三态、`matches[].selection`、`matches[].link_id`、unlink 端点）。
- Produces:
  - `QualificationPickerModal`：`onSelectQual` → `onSelectQuals: (quals) => void`（调用方同步改）。
  - `CollectionStep`：三态 Tag + 全部匹配项列表 + 行高亮 + 「×」移除 + 底部「已选资源汇总」卡片 + 公司信息卡片。

- [ ] **Step 1: 资质弹窗改多选**

`QualificationPickerModal.tsx`：
- `MULTI_SELECT_MODES`（line 52）改为 `['qualification', 'personnel', 'contract']`。
- Props：`onSelectQual: (qual: Qualification) => void` → `onSelectQuals: (quals: Qualification[]) => void`。
- `qualColumns` 去掉行内「选择」按钮列（多选模式用 rowSelection）。
- `selectedRows` 增加 `qualification` 分支：`qualification: quals.filter((q) => selectedRowKeys.includes(q.id))`。
- `handleConfirmMulti` 增加 qualification 分支：`if (mode === 'qualification') onSelectQuals?.(selectedRows.qualification)`。
- footer 里 `multiCount` 显示已对资质生效（现有逻辑已按 `multiSelect` 判断 footer）。

`CollectionStep.tsx` 调用处（line 341）改：
```tsx
onSelectQuals={(list) => handleLinkQuals(list, pickerReq)}
```
新增：
```tsx
const handleLinkQuals = async (quals: any[], reqName: string) => {
  try {
    for (const q of quals) {
      await client.post(`/collection/${projectId}/qualification/link`, {
        qualification_id: q.id,
        requirement_name: reqName,
      })
    }
    message.success(`已关联 ${quals.length} 项资质`)
    setPickerOpen(false)
    fetchStatus()
  } catch {
    message.error('关联失败')
  }
}
```

- [ ] **Step 2: 页面三态显示 + 全部匹配项 + 移除**

`CollectionStep.tsx` 的 document_items / personnel_items 渲染改造（两处 `List.Item` 的 `description` 与 `actions`）：

状态标签辅助：
```tsx
const statusMeta = (s: string) => {
  if (s === 'selected') return { text: '已选择', color: 'green', icon: <CheckCircleOutlined /> }
  if (s === 'uploaded') return { text: '已上传', color: 'green', icon: <CheckCircleOutlined /> }
  if (s === 'auto') return { text: '自动匹配', color: 'blue', icon: <LinkOutlined /> }
  if (s === 'matched') return { text: '候选', color: 'orange', icon: <LinkOutlined /> }
  return { text: '待处理', color: 'red', icon: <CloseCircleOutlined /> }
}
```

行 description 改为列出全部匹配项（不再只 `matches[0]`），每个可移除：
```tsx
description={
  <span>
    {item.matches.length > 0 && (
      <div style={{ marginTop: 4 }}>
        {item.matches.map((m: any, i: number) => (
          <Tag
            key={m.link_id || m.id || i}
            closable={!!m.link_id}
            color={m.selection === 'auto' ? 'blue' : 'green'}
            onClose={async (e) => {
              e.preventDefault()
              await removeMatch(item, m)
            }}
          >
            {m.name}
            {m.selection === 'auto' ? '（自动）' : ''}
          </Tag>
        ))}
      </div>
    )}
  </span>
}
```

行级状态替换 `isDone` 逻辑：`const meta = statusMeta(item.match_status)`，标题图标与 actions 用 `meta`。`isDone = item.match_status === 'selected' || item.match_status === 'uploaded' || item.match_status === 'auto'`；`auto`/`selected` 行加高亮：

```tsx
<List.Item
  style={isDone ? { background: '#f6ffed', borderLeft: '3px solid #52c41a', paddingLeft: 12 } : {}}
  ...
```

新增移除函数（document_items 用 qualification/contract 端点；personnel_items 用 unassign）：

```tsx
const removeMatch = async (item: ResourceMatch, m: any) => {
  try {
    if (item.requirement.category === 'personnel') {
      await client.post(`/collection/${projectId}/personnel/unassign`, { assignment_id: m.link_id })
    } else if (item.requirement.category === 'contract_performance') {
      await client.post(`/collection/${projectId}/contract/unlink`, { requirement_name: item.requirement.name, resource_id: m.id })
    } else {
      await client.post(`/collection/${projectId}/qualification/unlink`, { requirement_name: item.requirement.name, resource_id: m.id })
    }
    message.success('已移除')
    fetchStatus()
  } catch {
    message.error('移除失败')
  }
}
```

- [ ] **Step 3: 已选资源汇总卡片 + 公司信息卡片**

`CollectionStep.tsx` 在 `handleSkip` 后新增汇总数据计算与渲染：

```tsx
const allSelected = [
  ...(data?.document_items ?? []),
  ...(data?.personnel_items ?? []),
].filter((it) => it.match_status === 'selected' || it.match_status === 'auto' || it.match_status === 'uploaded')
```

在 `<Card>` 确认按钮区上方渲染：

```tsx
{allSelected.length > 0 && (
  <Card title="已选资源汇总" size="small" style={{ marginBottom: 16 }}>
    {allSelected.map((it) => (
      <div key={it.requirement.name} style={{ marginBottom: 8 }}>
        <Space>
          <Tag color="blue">{it.requirement.name}</Tag>
          <span style={{ color: '#666' }}>
            {it.matches.map((m: any) => m.name).join('、')}
          </span>
        </Space>
      </div>
    ))}
  </Card>
)}
```

公司信息卡片（`CompanyProfile` 由 `/company/` 接口取，`QualificationPickerModal` 已这么用）：

```tsx
const [company, setCompany] = useState<any>(null)
useEffect(() => {
  client.get('/company/').then((r) => setCompany(r.data)).catch(() => {})
}, [])

{company && (
  <Card title="公司信息" size="small" style={{ marginBottom: 16 }}>
    <Space wrap>
      <Tag>{company.company_name}</Tag>
      <span style={{ color: '#999' }}>统一社会信用代码：{company.business_license_number || '-'}</span>
      <span style={{ color: '#999' }}>法定代表人：{company.legal_rep_name || '-'}</span>
    </Space>
    <div style={{ color: '#999', fontSize: 12, marginTop: 4 }}>生成标书时，公司信息将自动注入所有章节。</div>
  </Card>
)}
```

- [ ] **Step 4: 构建验证**

Run（frontend 目录）：`npm run build`
Expected: 构建成功、无 TS 错误。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/project/QualificationPickerModal.tsx frontend/src/pages/project/CollectionStep.tsx
git commit -m "feat: 资料选择多选+三态状态显示+已选汇总卡片
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: A — 一键生成逐章重构（后端）

**Files:**
- Modify: `backend/app/services/ai_pipeline.py`（`generate_from_chapter_structure` 重构）
- Create: `backend/tests/test_generate_chapter_flow.py`（纯函数级）

**Interfaces:**
- Consumes: Task 3 的 `assemble_section_materials`；Task 4 的深管线改动（不冲突）。
- Produces（本任务内部纯函数，供 Task 8 前端契约对齐）：
  - `_mark_leaf_failure(tree: list, path: list[str], error: str) -> None`：给叶子节点写 `status="failed"` + `error`。
  - `_is_leaf_done(node: dict) -> bool`：`node.get("status") == "generated" and node.get("content")`。
  - 新 SSE 事件：`chapter_start / chapter_done / chapter_error`（payload 见 3.5）。
  - 叶子失败不再产出占位符文本；`children_json` 写回 `status`。

- [ ] **Step 1: 写失败测试（纯函数）**

```python
"""宏曦标书 - 逐章生成流程辅助函数 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.ai_pipeline import _is_leaf_done, _mark_leaf_failure


class TestMarkLeafFailure:
    def test_marks_nested_leaf(self):
        tree = [{"title": "第一章", "children": [{"title": "服务方案", "content": "旧"}]}]
        _mark_leaf_failure(tree, ["第一章", "服务方案"], "AI 超时")
        leaf = tree[0]["children"][0]
        assert leaf["status"] == "failed"
        assert leaf["error"] == "AI 超时"

    def test_flat_task_list_supported(self):
        tree = [{"title": "服务方案", "path": ["第一章", "服务方案"], "content": "旧"}]
        _mark_leaf_failure(tree, ["第一章", "服务方案"], "AI 超时")
        assert tree[0]["status"] == "failed"


class TestIsLeafDone:
    def test_generated_with_content_is_done(self):
        assert _is_leaf_done({"status": "generated", "content": "正文"}) is True

    def test_failed_is_not_done(self):
        assert _is_leaf_done({"status": "failed", "error": "x"}) is False

    def test_no_status_is_not_done(self):
        assert _is_leaf_done({"content": "旧内容"}) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run（backend 目录）：`python -m pytest tests/test_generate_chapter_flow.py -v`
Expected: FAIL — `ImportError: cannot import name '_is_leaf_done'`。

- [ ] **Step 3: 实现纯函数**

在 `ai_pipeline.py` 的 `generate_from_chapter_structure` 之前新增：

```python
def _is_leaf_done(node: dict) -> bool:
    """叶子是否已生成完毕（续传时跳过）."""
    return bool(node.get("content")) and node.get("status") == "generated"


def _mark_leaf_failure(tree: list, path: list[str], error: str) -> None:
    """把叶子节点标记为失败（兼容嵌套树与扁平任务列表）."""
    def _find(nodes, remaining):
        for node in nodes:
            if node.get("title") == remaining[0]:
                if len(remaining) == 1:
                    node["status"] = "failed"
                    node["error"] = error
                    return True
                kids = node.get("children") or []
                if _find(kids, remaining[1:]):
                    return True
        return False

    def _find_flat(nodes, remaining):
        for node in nodes:
            if node.get("path") == remaining:
                node["status"] = "failed"
                node["error"] = error
                return True
        return False

    if tree and isinstance(tree[0], dict) and "path" in tree[0]:
        _find_flat(tree, path)
    else:
        _find(tree, path)
```

- [ ] **Step 4: 重构 `generate_from_chapter_structure`（核心改动）**

在 `generate_from_chapter_structure` 内，把「收集全部叶子 + 并行生成 + 统一组装」三段改为逐章循环。关键骨架：

```python
    # ── 阶段 2：逐章节串行生成（每章上下文只装本章需要的）──
    generated_sections: Dict[str, str] = {}
    chapter_errors: list[str] = []
    chapters_payload: list[dict] = []
    lead_ins: Dict[str, str] = {}

    from app.services.materials_context import assemble_section_materials

    for chapter in chapters:
        if chapter.chapter_type != "ai_generated":
            continue

        yield {
            "event": "chapter_start",
            "data": json.dumps({
                "chapter_id": chapter.id, "title": chapter.title,
                "index": chapter.order_index, "total": len(chapters),
            }, ensure_ascii=False),
        }

        # 本章素材上下文（四类，按标题关键词）
        materials_guidance = assemble_section_materials(
            chapter.title,
            qualifications=matched_qualifications,
            personnel=matched_personnel,
            contracts=matched_contracts,
            company=company_profile,
        )
        # 本章招标要求过滤：保留顶层键，缩小数组
        chapter_requirements = _filter_requirements_for_chapter(
            requirements, chapter.title
        )

        # 本章叶子任务（复用 _collect_leaf_tasks 收集全量的结果，按 chapter_id 分组）
        chapter_tasks = [t for t in all_tasks if t["chapter_id"] == chapter.id]

        leaf_failed = 0
        leaf_done = 0
        chapter_error_msg = None
        try:
            for task_info in chapter_tasks:
                # 续传：已有内容且 status=generated → 跳过
                ...（从 children_json 读叶子状态判定）

            if chapter_tasks:
                # 本章叶子并行生成（semaphore），复用 _gen_one 改造版
                ...

            # 本章容器引导段 + 组装（复用现有 leadin/组装逻辑，scope 到本章）
            ...
            # 回写 children_json + 落库
            chapter.children_json = json.dumps(tree, ensure_ascii=False)
            await db.commit()
        except Exception as exc:
            logger.exception("Chapter '%s' generation failed: %s", chapter.title, exc)
            chapter_errors.append(chapter.title)
            chapter_error_msg = str(exc)
            # 标红本章所有叶子
            for task_info in chapter_tasks:
                _mark_leaf_failure(children_tree, task_info["task"]["path"], str(exc))
            try:
                chapter.children_json = json.dumps(children_tree, ensure_ascii=False)
                await db.commit()
            except Exception:
                pass
            leaf_failed = len(chapter_tasks)

        yield {
            "event": "chapter_error" if chapter_error_msg else "chapter_done",
            "data": json.dumps({
                "chapter_id": chapter.id, "title": chapter.title,
                "leaf_success": leaf_done, "leaf_failed": leaf_failed,
                "error": chapter_error_msg,
            }, ensure_ascii=False),
        }
```

新增纯函数（放在 `_mark_leaf_failure` 旁）：

```python
def _filter_requirements_for_chapter(requirements: dict, chapter_title: str) -> dict:
    """按章节标题过滤招标要求，保留顶层结构.

    命中章节标题/章节关键词的要求条目保留，否则移除数组元素；
    顶层键（required_documents/required_personnel/project_name 等）必须保留。
    """
    if not requirements:
        return {}
    filtered = dict(requirements)
    for key in ("required_documents", "required_personnel"):
        items = filtered.get(key) or []
        if not isinstance(items, list):
            continue
        kept = []
        for it in items:
            name = it.get("name", "") if isinstance(it, dict) else str(it)
            if not name or _chapter_matches_requirement(chapter_title, name):
                kept.append(it)
        filtered[key] = kept
    return filtered


def _chapter_matches_requirement(chapter_title: str, req_name: str) -> bool:
    """粗略相关性：章节标题与要求名有共现词即认为相关。"""
    return chapter_title in req_name or req_name in chapter_title
```

**实现注意（务必遵守）**：
- 保留文件章节先生成、`assign_target_budgets` 全局分配、`format_verification`、`done` 的现有结构（这些不动）。
- `_gen_one` 改造：异常/空内容不再产出 `[本节生成失败：...]` 文本；改为 `content=None, error=...`，成功后叶子写 `status="generated"`，失败 `_mark_leaf_failure` + `yield section_error`（事件保持）。
- 组装阶段 scope 到本章：本章 `chapter_trees`、`leadin_items`、`build_final_chapters_payload` 按章产出后 append 到全局 `chapters_payload`。
- 本章叶子在 `children_json` 里的当前状态用于续传判定：生成前先加载本章 tree，逐叶看 `_is_leaf_done`，已 done 的跳过并 `yield section_done`（复用已有 content）。
- 若实现中发现把整段重写比"就地改动"更清晰，允许重写 `generate_from_chapter_structure` 函数体，但**必须保持函数签名不变**（`bid.py:361-370` 的调用方不动）。

- [ ] **Step 5: 运行测试**

Run（backend 目录）：`python -m pytest tests/test_generate_chapter_flow.py -v`
Expected: PASS。
Run：`python -m pytest tests/ -v`
Expected: 现有套件全绿（`generate_from_chapter_structure` 无既有直接单测，主要靠 e2e_smoke）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ai_pipeline.py backend/tests/test_generate_chapter_flow.py
git commit -m "feat: 一键生成逐章串行+失败标红+续传补齐（新SSE事件）
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: A 前端 — 生成事件与失败显示

**Files:**
- Modify: `frontend/src/pages/project/ProjectWorkflow.tsx`
- Modify: `frontend/src/components/TreeEditor/TreePanel.tsx`
- Test: `cd frontend && npm run build`（gate）

**Interfaces:**
- Consumes: Task 7 的新 SSE 事件与 `children_json.status` 契约。
- Produces: `failedSections` 同时解析 `generation_state_json` 与 `children_json`；重试走续传；TreePanel 显示 `status==='failed'` 红标。

- [ ] **Step 1: ProjectWorkflow 事件处理**

在 `handleGenerate` 的 SSE reader 里加分支：

```tsx
if (ev === 'chapter_start') {
  const d = JSON.parse(parsed.data)
  setStatusMessage(`正在生成第 ${d.index}/${d.total} 章：${d.title}...`)
} else if (ev === 'chapter_done') {
  const d = JSON.parse(parsed.data)
  setStatusMessage(`第 ${d.index}/${d.total} 章完成（成功 ${d.leaf_success}，失败 ${d.leaf_failed}）`)
} else if (ev === 'chapter_error') {
  const d = JSON.parse(parsed.data)
  setStatusMessage(`第 ${d.title} 章生成失败`)
}
```

（`setStatusMessage` 若不存在，用现有的 `setProgressMessage`/`setGeneratingText` 等价状态；以文件现有状态变量为准。）

- [ ] **Step 2: failedSections 同时解析 children_json**

`handleGenerate` 结束后（或 `done` 事件里），在现有 `failedSections` 基础上追加扫描：调用章节接口拿到 `children_json`，递归找 `status === 'failed'` 的节点路径并入 `failedSections`。若前端已有章节树（TreeEditor 的 chapters），直接遍历树里的节点：

```tsx
const collectFailed = (nodes: any[]): { title: string; path: string }[] => {
  const out: { title: string; path: string }[] = []
  for (const n of nodes) {
    if (n.status === 'failed') out.push({ title: n.title, path: (n.path || []).join(' / ') })
    if (n.children) out.push(...collectFailed(n.children))
  }
  return out
}
```

- [ ] **Step 3: 重试走续传**

把失败重试按钮的 `handleRetry` 改为调 `POST /api/v1/bid/generate`（同 `handleGenerate`，body 只带 `project_id`），复用续传语义，不再调 `generate/retry-failed`。UI 提示改为「重新生成（跳过已完成章节，补齐未生成的）」。

- [ ] **Step 4: TreePanel 失败显示**

`TreePanel.tsx` 的叶子状态图标逻辑：在现有 `status === 'failed'` 判断基础上，直接支持 `node.status === 'failed'`（children_json 写回后节点带此字段），显示红色 `⚠` + title 加红。若现在只判断 `section.status`，改为同时读 `node.status`。

- [ ] **Step 5: 构建验证**

Run（frontend 目录）：`npm run build`
Expected: 构建成功、无 TS 错误。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/project/ProjectWorkflow.tsx frontend/src/components/TreeEditor/TreePanel.tsx
git commit -m "feat: 生成事件进度+未生成章节标红+重试走续传
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: C 前端 — 章节对话面板 SectionChat

**Files:**
- Create: `frontend/src/components/TreeEditor/SectionChat.tsx`
- Modify: `frontend/src/components/TreeEditor/EditPanel.tsx`
- Modify: `frontend/src/components/TreeEditor/TreeEditor.tsx`
- Test: `cd frontend && npm run build`（gate）

**Interfaces:**
- Consumes: Task 5 的 `sections/chat` 端点；EditPanel 现有的 diff 弹窗（`handleAcceptAI`）/ `onAIModify` props。
- Produces: `SectionChat` 组件 + EditPanel 挂载点。

- [ ] **Step 1: 新建 SectionChat 组件**

`frontend/src/components/TreeEditor/SectionChat.tsx`（复用 `OutlineChat.tsx` 的消息列表样式）：

```tsx
import { useEffect, useRef, useState } from 'react'
import { Button, Input, message, Space, Typography } from 'antd'
import client from '../../api/client'

interface Props {
  projectId: string
  chapterId: string
  sectionPath: string[]       // 当前节路径
  currentContent: string      // 当前编辑器内容（每次发送时实时取）
  onApplyContent: (content: string) => void  // 应用修改：走 diff 预览
}

interface Msg { role: 'user' | 'assistant'; content: string }

export default function SectionChat({ projectId, chapterId, sectionPath, currentContent, onApplyContent }: Props) {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [revisedContent, setRevisedContent] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setMessages([])
    setRevisedContent(null)
  }, [chapterId, JSON.stringify(sectionPath)])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, revisedContent])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    const next: Msg[] = [...messages, { role: 'user', content: text }]
    setMessages(next)
    setLoading(true)
    try {
      const res = await client.post(
        `/bid/${projectId}/chapters/${chapterId}/sections/chat`,
        {
          section_path: sectionPath,
          current_content: currentContent,
          messages: next,
          instruction: text,
        }
      )
      setMessages([...next, { role: 'assistant', content: res.data.reply || '（无回复）' }])
      if (res.data.revised_content && res.data.revised_content !== currentContent) {
        setRevisedContent(res.data.revised_content)
      } else {
        setRevisedContent(null)
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '对话失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ marginTop: 12, borderTop: '1px solid #f0f0f0', paddingTop: 12 }}>
      <Typography.Text strong>AI 对话修正本节</Typography.Text>
      <div ref={scrollRef} style={{ maxHeight: 240, overflowY: 'auto', marginTop: 8 }}>
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 8, textAlign: m.role === 'user' ? 'right' : 'left' }}>
            <div
              style={{
                display: 'inline-block', maxWidth: '85%', padding: '6px 10px', borderRadius: 8,
                background: m.role === 'user' ? '#e6f4ff' : '#f5f5f5', whiteSpace: 'pre-wrap',
                textAlign: 'left',
              }}
            >
              {m.content}
            </div>
          </div>
        ))}
        {revisedContent && (
          <div style={{ marginTop: 8 }}>
            <Button
              type="primary" size="small"
              onClick={() => { onApplyContent(revisedContent); setRevisedContent(null) }}
            >
              应用修改（预览 diff）
            </Button>
          </div>
        )}
      </div>
      <Space.Compact style={{ marginTop: 8, width: '100%' }}>
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入修改意见，可与 AI 多轮对话…"
          onPressEnter={handleSend}
          disabled={loading}
        />
        <Button type="primary" onClick={handleSend} loading={loading}>发送</Button>
      </Space.Compact>
    </div>
  )
}
```

- [ ] **Step 2: EditPanel 挂载**

`EditPanel.tsx`：
- 加 props：`projectId: string`、`chapterId: string`、`sectionPath: string[]`、`currentContent: string`。
- 在编辑器（BidEditor）下方、`isFileType` 为 false 时渲染：

```tsx
<SectionChat
  projectId={projectId}
  chapterId={chapterId}
  sectionPath={sectionPath}
  currentContent={currentContent}
  onApplyContent={(content) => {
    // 复用现有 diff 预览弹窗：设置 AI 修改内容并打开弹窗，走 handleAcceptAI 路径
    setAIModifyResult(content)          // 以现有 diff 弹窗的状态变量为准
    setDiffOpen(true)                   // 现有弹窗开关
  }}
/>
```

（若 EditPanel 的 diff 弹窗是函数式而非状态式，调整接入方式，保持「应用修改 → 原文 vs 修改 diff → 接受保存」的现有流程。）

- [ ] **Step 3: TreeEditor 透传 props**

`TreeEditor.tsx` 在渲染 `EditPanel` 处透传 `projectId`、`chapterId`（当前选中章节）、`sectionPath`（当前选中节）、`currentContent`（现有 `currentContent` 状态）。

- [ ] **Step 4: 构建验证**

Run（frontend 目录）：`npm run build`
Expected: 构建成功、无 TS 错误。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/TreeEditor/SectionChat.tsx frontend/src/components/TreeEditor/EditPanel.tsx frontend/src/components/TreeEditor/TreeEditor.tsx
git commit -m "feat: 每节 AI 对话面板（多轮+手动应用diff）
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 回归 + E2E + 部署

**Files:**
- Run: `backend/tests/`、`frontend npm run build`、`backend/scripts/e2e_smoke.py`
- Deploy per `server-deployment-config.md`

- [ ] **Step 1: 后端全量测试**

Run（backend 目录）：`python -m pytest tests/ -v`
Expected: 除 `test_format_template_to_prompt_text`（Windows 乱码环境问题，已知）外全绿。

- [ ] **Step 2: 前端构建**

Run（frontend 目录）：`npm run build`
Expected: 成功。

- [ ] **Step 3: E2E 冒烟**

本地若可跑：`python scripts/e2e_smoke.py`（真实 AI，几分钟）。若本地不可跑，部署后在服务器容器内跑：
`docker exec -w /app -e PYTHONPATH=/app hongxi-backend python scripts/e2e_smoke.py`
Expected: 全绿（3 次运行稳定）。

- [ ] **Step 4: 部署**

按 `server-deployment-config.md`：
1. `cd backend && tar czf /tmp/bk.tar.gz --exclude='__pycache__' --exclude='*.pyc' app alembic scripts/e2e_smoke.py`
2. `cd frontend && tar czf /tmp/fr.tar.gz src`
3. pscp 两个包 + 服务器解压到 `/hxbid/hongxi-bid/`
4. `docker compose up -d --build backend frontend`
5. 等 healthy 后 `alembic stamp <当前 head>`（无 schema 变更，仅确认版本）
6. 验证：`curl /api/v1/bid-lessons`（401 预期）；`docker exec hongxi-frontend sh -c "grep -rl <新组件串> /usr/share/nginx/html/assets/"`；`docker logs hongxi-backend --tail 30 | grep -i error`。

- [ ] **Step 5: 部署后冒烟**

服务器容器内跑 `e2e_smoke.py`（真实 AI，几分钟），并人工验证：信息搜集多选+三态、逐章生成进度、失败章节标红、章节对话应用 diff。

- [ ] **Step 6: 更新记忆**

把本次关键改动（逐章生成、素材注入、章节对话、资料多选、自动占用改 confirm 时）追加到 `bid-pipeline-validated.md`，含 deployment 注意事项（无 schema 变更、alembic stamp）。

- [ ] **Step 7: Commit（若测试过程中有代码改动）**

```bash
git add -A
git commit -m "chore: 分章节生成+素材+对话+多选 回归与收尾
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
