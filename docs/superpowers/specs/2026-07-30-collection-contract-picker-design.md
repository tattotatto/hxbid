# 信息搜集 — 业绩合同选择功能

**日期**: 2026-07-30
**状态**: 设计阶段
**关联**: 信息搜集步骤（CollectionStep）增强

---

## 1. 背景

当前信息搜集步骤（`CollectionStep`）处理两类招标需求：
- **资质与证件**：从资质库匹配
- **人员配置**：从人员库匹配

但招标文件经常要求"业绩合同"，例如：
> 2023年1月1日至投标截止日，安保业务服务业绩至少2项

系统已有 **历史合同管理** 页面（`/resources/contracts`），但信息搜集步骤无法选择合同。

---

## 2. 设计方案

### 2.1 核心思路

复用 `required_documents` 体系，通过 `category: "contract_performance"` 区分业绩合同需求。前端资质选择弹窗增加模式切换（资质 / 合同）。

### 2.2 数据流

```
AI提取需求 → category: "contract_performance"（或用户手动判断）
    ↓
CollectionStep 展示 → "从历史合同选择" → PickerModal(模式=合同)
    ↓
用户选择合同 → POST /collection/{id}/contract/link
    ↓
后端存储 ProjectContract 记录
    ↓
生成阶段 get_collected_resources() 返回已选合同
```

---

## 3. 模型变更

### 3.1 Contract 模型 — 新增字段

```python
# backend/app/models/contract.py
contract_date: Mapped[date] = mapped_column(
    Date, nullable=True,
    comment="合同签订日期，用于按时间范围筛选业绩合同"
)
```

> **设计决定**：只加一个 `contract_date` 字段。服务类型通过 AI 语义匹配 `procurement_content` 文本；金额阈值通过 `contract_amount` 文本做模糊匹配。最小化 schema 变更。

### 3.2 新增 ProjectContract 关联表

```python
# backend/app/models/project_resource.py
class ProjectContract(Base):
    """Project-to-contract association for collection step."""
    __tablename__ = "project_contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("bid_projects.id", ondelete="CASCADE"))
    contract_id: Mapped[str] = mapped_column(String(36), ForeignKey("contracts.id", ondelete="SET NULL"), nullable=True)
    requirement_name: Mapped[str] = mapped_column(String(300), default="")
    match_status: Mapped[str] = mapped_column(String(20), default="matched")
```

---

## 4. 后端变更

### 4.1 新增 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/collection/{project_id}/contract/link` | 关联历史合同到项目需求 |

请求体：
```json
{
  "contract_id": "uuid",
  "requirement_name": "2023年1月至今安保业绩合同"
}
```

### 4.2 Collection 服务扩展

- `analyze_collection_needs()` — 已支持 `required_documents` 中任意 category，无需改动
- `link_contract()` — 新增，关联合同到项目需求
- `get_collected_resources()` — 扩展返回 `contracts` 列表

### 4.3 自动匹配逻辑（混合模式）

当 category 为 `contract_performance` 时：
1. 从需求名称中提取时间范围关键词（如"2023"）和服务类型（如"安保"）
2. 筛选 `contract_date >= 2023-01-01` 的合同
3. 用 `_fuzzy_match` 对 `procurement_content` 做语义匹配
4. 返回匹配结果作为推荐，用户可在前端调整

---

## 5. 前端变更

### 5.1 QualificationPickerModal — 模式切换

弹窗顶部增加下拉选择控件：

```
┌─────────────────────────────────────────┐
│ 选择资源 — 安保业务服务业绩            │
│                                         │
│ [选择类型: 公司资质 ▾]  ← 默认"公司资质"│
│                        下拉可选"历史合同"│
│ ┌─────────────────────────────────────┐ │
│ │ (公司资质模式：现有Table不变)        │ │
│ │ (历史合同模式：合同Table)            │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

**历史合同模式** Table 列：
- 项目名称、采购单位、合同金额、签订日期
- 搜索框（按项目名称/采购单位搜索）
- 选择按钮

### 5.2 CollectionStep — 展示适配

- `category === "contract_performance"` 的需求显示标签为 `业绩合同`（橙色 Tag）
- 已匹配时显示：`已选：{project_name} ({contract_date})`
- "从资质库选择"按钮行为不变，PickerModal 自行处理模式切换

---

## 6. 数据库迁移

```sql
-- 1. Contract 新增 contract_date 字段
ALTER TABLE contracts ADD COLUMN contract_date DATE;

-- 2. 新建 project_contracts 关联表
CREATE TABLE project_contracts (
    id VARCHAR(36) PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL REFERENCES bid_projects(id) ON DELETE CASCADE,
    contract_id VARCHAR(36) REFERENCES contracts(id) ON DELETE SET NULL,
    requirement_name VARCHAR(300) DEFAULT '',
    match_status VARCHAR(20) DEFAULT 'matched'
);
```

---

## 7. 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/app/models/contract.py` | 修改 | 新增 `contract_date` 字段 |
| `backend/app/models/project_resource.py` | 修改 | 新增 `ProjectContract` 模型 |
| `backend/app/schemas/contract.py` | 修改 | `ContractCreate`/`ContractRead` 新增 `contract_date` |
| `backend/app/schemas/collection.py` | 修改 | 新增 `LinkContractRequest` |
| `backend/app/services/collection.py` | 修改 | 新增 `link_contract` + 合同自动匹配 |
| `backend/app/api/collection.py` | 修改 | 新增 contract/link 端点 + resources 扩展 |
| `backend/alembic/` | 新增迁移 | contract_date + project_contracts 表 |
| `frontend/src/pages/project/QualificationPickerModal.tsx` | 修改 | 模式切换（资质/合同） |
| `frontend/src/pages/project/CollectionStep.tsx` | 修改 | 适配 category 展示 + contract link 逻辑 |
| `frontend/src/pages/resources/Contracts.tsx` | 修改 | 表单新增 contract_date 字段 |

---

## 8. 自检

- [x] 无 TBD / TODO
- [x] 模型、API、前端改动一致
- [x] 范围可控 — 约 10 个文件
- [x] 需求明确 — 混合匹配模式、最小 schema 变更
