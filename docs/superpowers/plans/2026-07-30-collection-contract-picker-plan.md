# 信息搜集 — 业绩合同选择功能 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 信息搜集步骤支持从历史合同库中选择业绩合同，满足招标文件中的业绩要求。

**Architecture:** 复用 `required_documents` 体系（`category: "contract_performance"`）；新增 `ProjectContract` 关联表存储项目-合同关系；前端 `QualificationPickerModal` 增加"资质/合同"模式切换。

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + React + TypeScript + Ant Design

## Global Constraints

- Contract 模型仅新增 `contract_date` 字段（最小 schema 变更）
- 服务类型通过 AI 语义匹配 `procurement_content` 文本
- 前端弹窗默认模式为"公司资质"，下拉可切换到"历史合同"
- 匹配模式：后端自动筛选推荐 + 用户可调整

---

### Task 1: 数据库迁移

**Files:**
- Create: `backend/alembic/versions/20260730_0003_add_contract_date_and_project_contracts.py`

- [ ] **Step 1: 创建迁移文件**

```python
"""add contract_date to contracts and create project_contracts

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-30

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add contract_date to contracts
    op.add_column(
        "contracts",
        sa.Column(
            "contract_date",
            sa.Date(),
            nullable=True,
            comment="合同签订日期，用于按时间范围筛选业绩合同",
        ),
    )

    # 2. Create project_contracts table
    op.create_table(
        "project_contracts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("bid_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contract_id",
            sa.String(36),
            sa.ForeignKey("contracts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requirement_name", sa.String(300), nullable=False, server_default=""),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="matched"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("project_contracts")
    op.drop_column("contracts", "contract_date")
```

- [ ] **Step 2: 运行迁移**

Run: `cd backend && alembic upgrade head`
Expected: migration 0003 applied successfully

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/20260730_0003_add_contract_date_and_project_contracts.py
git commit -m "feat(db): add contract_date + project_contracts table"
```

---

### Task 2: Contract 模型增加 contract_date 字段

**Files:**
- Modify: `backend/app/models/contract.py`

**Produces:** `Contract.contract_date: Mapped[date | None]`

- [ ] **Step 1: 在 Contract 类中新增字段**

在 `notes` 字段之后、`image_paths_json` 之前插入：

```python
    contract_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="合同签订日期，用于按时间范围筛选业绩合同",
    )
```

需要在文件顶部更新 import（`Date` 已在 `datetime` 导入中，确认 `date` 已导入）：
```python
from datetime import date, datetime
```

当前第7行已有此导入，无需修改。

- [ ] **Step 2: 验证模型导入无报错**

Run: `cd backend && python -c "from app.models.contract import Contract; print(Contract.__tablename__)"`
Expected: `contracts`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/contract.py
git commit -m "feat(model): Contract 新增 contract_date 字段"
```

---

### Task 3: Contract Schema 增加 contract_date

**Files:**
- Modify: `backend/app/schemas/contract.py`

- [ ] **Step 1: 更新 ContractCreate、ContractUpdate、ContractRead**

```python
# ContractCreate 新增
contract_date: date | None = None

# ContractUpdate 新增
contract_date: date | None = None

# ContractRead 新增
contract_date: date | None = None
```

`date` 需要从 `datetime` 导入：
```python
from datetime import date, datetime
```

`ContractRead.model_config` 保持不变（`from_attributes=True`）。

- [ ] **Step 2: 验证 schema 导入**

Run: `cd backend && python -c "from app.schemas.contract import ContractCreate; print(ContractCreate.model_fields.keys())"`
Expected: includes `contract_date`

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/contract.py
git commit -m "feat(schema): Contract schema 新增 contract_date"
```

---

### Task 4: 新增 ProjectContract 模型

**Files:**
- Modify: `backend/app/models/project_resource.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/project.py` (添加 relationship)

**Produces:** `ProjectContract` ORM model

- [ ] **Step 1: 在 project_resource.py 末尾添加 ProjectContract**

```python
class ProjectContract(Base):
    """A historical contract linked to a bid project during collection.

    Created during the information-collection step.  Links a Contract
    from the resource library to fulfil a performance-contract requirement.
    """

    __tablename__ = "project_contracts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    project_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    contract_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("contracts.id", ondelete="SET NULL"),
        nullable=True,
    )
    requirement_name: Mapped[str] = mapped_column(
        String(300),
        nullable=False,
        default="",
    )
    match_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="matched",
        # Values: "matched" | "missing"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Relationships
    project: Mapped["BidProject"] = relationship(  # noqa: F821
        back_populates="project_contracts",
    )

    def __repr__(self) -> str:
        return (
            f"<ProjectContract(id={self.id!r},"
            f" requirement={self.requirement_name!r},"
            f" status={self.match_status!r})>"
        )
```

- [ ] **Step 2: 在 __init__.py 中导出 ProjectContract**

在 `backend/app/models/__init__.py` 中：
```python
from app.models.project_resource import ProjectQualification, ProjectPersonnel, ProjectContract
```
修改现有：
```python
from app.models.project_resource import ProjectQualification, ProjectPersonnel
```
改为：
```python
from app.models.project_resource import ProjectQualification, ProjectPersonnel, ProjectContract
```

并在 `__all__` 中添加 `"ProjectContract"`。

- [ ] **Step 3: 在 BidProject 中添加 relationship**

在 `backend/app/models/project.py` 的 `BidProject` 类中，`project_personnel` relationship 之后添加：

```python
    project_contracts: Mapped[list["ProjectContract"]] = relationship(
        "ProjectContract",
        back_populates="project",
        cascade="all, delete-orphan",
    )
```

- [ ] **Step 4: 验证模型导入**

Run: `cd backend && python -c "from app.models import ProjectContract; print(ProjectContract.__tablename__)"`
Expected: `project_contracts`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/project_resource.py backend/app/models/__init__.py backend/app/models/project.py
git commit -m "feat(model): 新增 ProjectContract 关联模型"
```

---

### Task 5: Collection Schema — 新增 LinkContractRequest

**Files:**
- Modify: `backend/app/schemas/collection.py`

- [ ] **Step 1: 添加 LinkContractRequest**

在文件末尾添加：

```python
class LinkContractRequest(BaseModel):
    contract_id: str
    requirement_name: str = ""
```

- [ ] **Step 2: 验证 schema**

Run: `cd backend && python -c "from app.schemas.collection import LinkContractRequest; print(LinkContractRequest.model_fields.keys())"`
Expected: includes `contract_id, requirement_name`

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/collection.py
git commit -m "feat(schema): 新增 LinkContractRequest"
```

---

### Task 6: Collection Service — 合同匹配与关联

**Files:**
- Modify: `backend/app/services/collection.py`

**Produces:**
- `link_contract(project_id, contract_id, requirement_name, db) -> ProjectContract`
- `_match_contracts(requirement_name, contracts) -> List[Dict]`
- `analyze_collection_needs()` 扩展支持 `contract_performance` category
- `get_collected_resources()` 扩展返回 contracts

- [ ] **Step 1: 添加合同自动匹配函数**

在 `_match_personnel` 函数之后、`_personnel_to_dict` 之前添加：

```python
def _match_contracts(
    requirement_name: str,
    contracts: List,
) -> List[Dict[str, Any]]:
    """Match a contract-performance requirement against the contract library.

    Extracts date-filter keywords ("2023") and service-type keywords
    ("安保", "物业", "保洁", etc.) from the requirement name, then filters
    contracts by contract_date range and fuzzy-matches procurement_content.
    """
    from datetime import date as date_type

    matches = []
    req_lower = requirement_name.lower()

    # Extract year hints, e.g. "2023年1月1日至投标截止日"
    year_hints = []
    import re
    year_matches = re.findall(r"(\d{4})\s*年", requirement_name)
    for y in year_matches:
        try:
            year_hints.append(int(y))
        except ValueError:
            pass

    # Extract service type keywords
    SERVICE_KEYWORDS = ["安保", "保安", "物业", "保洁", "绿化", "餐饮", "维修", "后勤", "秩序维护"]
    matched_service = None
    for kw in SERVICE_KEYWORDS:
        if kw in requirement_name:
            matched_service = kw
            break

    for c in contracts:
        # Date filter: contract_date >= earliest year hint
        if year_hints and c.contract_date:
            min_year = min(year_hints)
            if c.contract_date < date_type(min_year, 1, 1):
                continue

        # Service type fuzzy match against procurement_content
        if matched_service:
            content = (c.procurement_content or "").lower()
            if matched_service not in content and matched_service not in (c.project_name or "").lower():
                continue

        matches.append({
            "source": "contract",
            "id": c.id,
            "name": c.project_name,
            "procurement_unit": c.procurement_unit or "",
            "contract_amount": c.contract_amount or "",
            "contract_date": str(c.contract_date) if c.contract_date else "",
            "service_period": c.service_period or "",
        })

    # If no date-filtered match, return all as candidates
    if not matches and contracts:
        matches = [
            {
                "source": "contract",
                "id": c.id,
                "name": c.project_name,
                "procurement_unit": c.procurement_unit or "",
                "contract_amount": c.contract_amount or "",
                "contract_date": str(c.contract_date) if c.contract_date else "",
                "service_period": c.service_period or "",
            }
            for c in contracts[:10]
        ]

    return matches
```

- [ ] **Step 2: 修改 analyze_collection_needs — 支持 contract_performance**

在 `analyze_collection_needs` 函数中，加载 `qualifications` 和 `personnel_list` 之后，添加合同加载：

```python
    from app.models.contract import Contract

    contracts = (
        (await db.execute(select(Contract))).scalars().all()
    )
```

然后在 `for doc in required_docs` 循环中，修改匹配逻辑。当前代码：

```python
        matches = _match_document(name, category, quals, company)
```

改为：

```python
        if category == "contract_performance":
            matches = _match_contracts(name, contracts)
        else:
            matches = _match_document(name, category, quals, company)
```

同时更新 category label 映射 — 当前文档项使用局部 `categoryLabel`，需要在前端处理。后端只需传递正确的 category。

- [ ] **Step 3: 添加 link_contract 函数**

在 `upload_qualification` 函数之后添加：

```python
async def link_contract(
    project_id: str,
    contract_id: str,
    requirement_name: str,
    db: AsyncSession,
) -> ProjectContract:
    """Link a historical contract to fulfil a performance-contract requirement."""
    # Remove any previous contract for the same requirement in this project
    existing = await db.execute(
        select(ProjectContract).where(
            ProjectContract.project_id == project_id,
            ProjectContract.requirement_name == requirement_name,
        )
    )
    for old in existing.scalars():
        await db.delete(old)

    pc = ProjectContract(
        project_id=project_id,
        contract_id=contract_id,
        requirement_name=requirement_name,
        match_status="matched",
    )
    db.add(pc)
    await db.flush()
    await db.refresh(pc)
    return pc
```

需要在文件顶部导入 `ProjectContract`：
```python
from app.models.project_resource import ProjectPersonnel, ProjectQualification, ProjectContract
```

也需要导入 `select`（已导入）和 Contract model（在函数内部导入，避免循环引用，实际在 `analyze_collection_needs` 中已通过 from import 导入）。

- [ ] **Step 4: 扩展 get_collected_resources — 返回 contracts**

在 `get_collected_resources` 函数末尾，`return` 之前添加：

```python
    # Contracts
    pc_result = await db.execute(
        select(ProjectContract)
        .where(ProjectContract.project_id == project_id)
    )
    from app.models.contract import Contract as ContractModel
    contracts = []
    for pc in pc_result.scalars():
        c = None
        if pc.contract_id:
            c = await db.get(ContractModel, pc.contract_id)
        contracts.append({
            "id": pc.id,
            "contract_id": pc.contract_id,
            "requirement_name": pc.requirement_name,
            "project_name": c.project_name if c else "",
            "procurement_unit": c.procurement_unit if c else "",
            "contract_amount": c.contract_amount if c else "",
            "contract_date": str(c.contract_date) if c and c.contract_date else "",
            "source": "collected",
        })
```

并将 `return` 语句改为：
```python
    return {"qualifications": quals, "personnel": personnel, "company": company, "contracts": contracts}
```

- [ ] **Step 5: 验证无需测试即可导入**

Run: `cd backend && python -c "from app.services.collection import link_contract, _match_contracts; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/collection.py
git commit -m "feat(service): collection 支持合同匹配与关联"
```

---

### Task 7: Collection API — 新增 contract/link 端点

**Files:**
- Modify: `backend/app/api/collection.py`

- [ ] **Step 1: 导入新依赖**

更新 import：
```python
from app.schemas.collection import (
    AssignPersonnelRequest,
    CollectionStatus,
    LinkContractRequest,
    LinkQualificationRequest,
)
from app.services.collection import (
    analyze_collection_needs,
    assign_personnel,
    confirm_collection,
    get_collected_resources,
    link_contract,
    link_qualification,
    unassign_personnel,
    upload_qualification,
)
```

- [ ] **Step 2: 添加 link_contract 端点**

在 `link_qualification_to_project` 端点之后添加：

```python
# ── POST /{project_id}/contract/link ─────────────────────────────────


@router.post("/{project_id}/contract/link")
async def link_contract_to_project(
    project_id: str,
    data: LinkContractRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
):
    """Link a historical contract to fulfil a performance-contract requirement."""
    pc = await link_contract(
        project_id, data.contract_id, data.requirement_name, db
    )
    return {"id": pc.id, "requirement_name": pc.requirement_name, "status": pc.match_status}
```

- [ ] **Step 3: 验证路由导入**

Run: `cd backend && python -c "from app.api.collection import router; print([r.path for r in router.routes])"`
Expected: includes contract/link path

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/collection.py
git commit -m "feat(api): 新增 POST /collection/{id}/contract/link"
```

---

### Task 8: 前端 Contracts 页面 — 新增 contract_date 字段

**Files:**
- Modify: `frontend/src/pages/resources/Contracts.tsx`

- [ ] **Step 1: 更新 TypeScript 接口**

在 `interface Contract` 中添加：
```typescript
  contract_date: string | null
```

- [ ] **Step 2: 在表单中添加日期选择控件**

在 `服务时间` Form.Item 之后添加：

```tsx
            <Form.Item label="合同签订日期" name="contract_date">
              <Input placeholder="如：2024-01-15" />
            </Form.Item>
```

Ant Design 的 `DatePicker` 会更理想，但为了保持简洁先用 Input。如果项目已引入 DatePicker 可以直接使用：

```tsx
            <Form.Item label="合同签订日期" name="contract_date">
              <DatePicker style={{ width: '100%' }} placeholder="选择合同签订日期" />
            </Form.Item>
```

需要在文件顶部 import：
```typescript
import { DatePicker } from 'antd'
```

同时需要在 `handleStep1` 中将 `contract_date` 序列化：
```typescript
        contract_date: values.contract_date ? values.contract_date.format('YYYY-MM-DD') : '',
```

如果用 Input 方式则直接传字符串：
```typescript
        contract_date: values.contract_date || '',
```

推荐使用 DatePicker 方式。

- [ ] **Step 3: 在表格列中显示签订日期**

在 `服务时间` 和 `页数` 列之间添加：

```typescript
    {
      title: '签订日期',
      dataIndex: 'contract_date',
      key: 'contract_date',
      width: 110,
      render: (d: string | null) => d || '-',
    },
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/resources/Contracts.tsx
git commit -m "feat(ui): 历史合同表单新增 contract_date 字段"
```

---

### Task 9: 前端 QualificationPickerModal — 模式切换（资质/合同）

**Files:**
- Modify: `frontend/src/pages/project/QualificationPickerModal.tsx`

- [ ] **Step 1: 重命名组件以支持双模式**

将文件内容改为支持 `mode` 属性。核心改动：

```tsx
import { useEffect, useState } from 'react'
import { Modal, Table, Button, Input, Select, message } from 'antd'
import client from '../../api/client'

interface Qualification {
  id: string
  name: string
  cert_number: string
  issuing_authority: string
  expiry_date: string | null
}

interface Contract {
  id: string
  project_name: string
  procurement_unit: string
  contract_amount: string
  contract_date: string | null
  service_period: string
}

type PickerMode = 'qualification' | 'contract'

interface Props {
  open: boolean
  requirementName: string
  onCancel: () => void
  onSelectQual: (qual: Qualification) => void
  onSelectContract?: (contract: Contract) => void
}

export default function QualificationPickerModal({
  open, requirementName, onCancel, onSelectQual, onSelectContract,
}: Props) {
  const [mode, setMode] = useState<PickerMode>('qualification')
  const [quals, setQuals] = useState<Qualification[]>([])
  const [contracts, setContracts] = useState<Contract[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (open) {
      setLoading(true)
      Promise.all([
        client.get('/qualifications/'),
        client.get('/contracts/'),
      ])
        .then(([qualRes, contractRes]) => {
          setQuals(qualRes.data)
          setContracts(contractRes.data)
        })
        .catch(() => message.error('获取资源列表失败'))
        .finally(() => setLoading(false))
    }
  }, [open])

  const filteredQuals = search
    ? quals.filter((q) =>
        q.name.includes(search) || q.cert_number.includes(search)
      )
    : quals

  const filteredContracts = search
    ? contracts.filter((c) =>
        c.project_name.includes(search) || c.procurement_unit.includes(search)
      )
    : contracts

  const qualColumns = [
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '证书编号', dataIndex: 'cert_number', key: 'cert_number' },
    { title: '发证机构', dataIndex: 'issuing_authority', key: 'issuing_authority' },
    {
      title: '',
      key: 'action',
      render: (_: any, record: Qualification) => (
        <Button type="link" onClick={() => onSelectQual(record)}>选择</Button>
      ),
    },
  ]

  const contractColumns = [
    { title: '项目名称', dataIndex: 'project_name', key: 'project_name', ellipsis: true },
    { title: '采购单位', dataIndex: 'procurement_unit', key: 'procurement_unit', ellipsis: true },
    { title: '合同金额', dataIndex: 'contract_amount', key: 'contract_amount', width: 120 },
    {
      title: '签订日期',
      dataIndex: 'contract_date',
      key: 'contract_date',
      width: 110,
      render: (d: string | null) => d || '-',
    },
    {
      title: '',
      key: 'action',
      render: (_: any, record: Contract) => (
        <Button
          type="link"
          onClick={() => onSelectContract?.(record)}
        >
          选择
        </Button>
      ),
    },
  ]

  return (
    <Modal
      title={`选择资源 — ${requirementName}`}
      open={open}
      onCancel={onCancel}
      footer={null}
      width={mode === 'contract' ? 800 : 700}
    >
      <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
        <span style={{ whiteSpace: 'nowrap' }}>选择类型：</span>
        <Select
          value={mode}
          onChange={(v) => { setMode(v); setSearch('') }}
          style={{ width: 140 }}
          options={[
            { value: 'qualification', label: '公司资质' },
            { value: 'contract', label: '历史合同' },
          ]}
        />
        <Input
          placeholder={mode === 'qualification' ? '搜索资质名称或编号...' : '搜索项目名称或采购单位...'}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
          style={{ flex: 1 }}
        />
      </div>

      {mode === 'qualification' ? (
        <Table
          dataSource={filteredQuals}
          columns={qualColumns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
        />
      ) : (
        <Table
          dataSource={filteredContracts}
          columns={contractColumns}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
        />
      )}
    </Modal>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/project/QualificationPickerModal.tsx
git commit -m "feat(ui): QualificationPickerModal 支持资质/合同双模式切换"
```

---

### Task 10: 前端 CollectionStep — 适配 contract_performance category

**Files:**
- Modify: `frontend/src/pages/project/CollectionStep.tsx`

- [ ] **Step 1: 新增 handleLinkContract**

在 `handleLinkQual` 之后添加：

```typescript
  const handleLinkContract = async (contractId: string, reqName: string) => {
    try {
      await client.post(`/collection/${projectId}/contract/link`, {
        contract_id: contractId,
        requirement_name: reqName,
      })
      message.success('已关联合同')
      setQualPickerOpen(false)
      fetchStatus()
    } catch {
      message.error('关联失败')
    }
  }
```

- [ ] **Step 2: 区分 category 渲染**

在 `renderItem` 中修改 `categoryLabel` 映射，添加：

```typescript
              const categoryLabel =
                item.requirement.category === 'company' ? '公司证照' :
                item.requirement.category === 'financial' ? '财务证明' :
                item.requirement.category === 'qualification' ? '专业资质' :
                item.requirement.category === 'contract_performance' ? '业绩合同' : '其他'
```

- [ ] **Step 3: 已匹配状态显示合同名称**

在 `description` 插槽中，当 `category === 'contract_performance'` 且已匹配时，显示合同的项目名称和签订日期：

```typescript
                    description={
                      <span>
                        <Tag>{categoryLabel}</Tag>
                        {isDone && item.matches[0] && (
                          <span style={{ color: '#666', fontSize: 12 }}>
                            {item.matches[0].name}
                            {item.matches[0].contract_date ? ` (${item.matches[0].contract_date})` : ''}
                          </span>
                        )}
                      </span>
                    }
```

这与当前已有的逻辑兼容。当 source 为 "contract" 时，`matches[0].name` 就是合同的项目名称。

- [ ] **Step 4: 更新 QualificationPickerModal 调用**

将 QualificationPickerModal 的 props 更新为新的接口：

```tsx
      <QualificationPickerModal
        open={qualPickerOpen}
        requirementName={qualPickerReq}
        onCancel={() => setQualPickerOpen(false)}
        onSelectQual={(qual) => handleLinkQual(qual.id, qualPickerReq)}
        onSelectContract={(c) => handleLinkContract(c.id, qualPickerReq)}
      />
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/project/CollectionStep.tsx
git commit -m "feat(ui): CollectionStep 支持业绩合同 category"
```

---

### Task 11: 集成验证

**Files:**
- 无新建文件

- [ ] **Step 1: 后端启动验证**

Run: `cd backend && python -c "
from app.models import Contract, ProjectContract
from app.schemas.contract import ContractCreate, ContractRead
from app.schemas.collection import LinkContractRequest
from app.services.collection import link_contract, _match_contracts
from app.api.collection import router
print('All imports OK')
"`

Expected: `All imports OK`

- [ ] **Step 2: 前端编译验证**

Run: `cd frontend && npx tsc --noEmit`
Expected: no TypeScript errors

- [ ] **Step 3: 全部文件变更审查**

Run: `git diff --stat HEAD`
Expected: ~10 files changed

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: 集成验证通过 — 业绩合同选择功能就绪"
```
