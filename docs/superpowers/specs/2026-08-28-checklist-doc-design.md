# 标书同步检查文档（核对清单）设计

日期：2026-08-28
状态：已批准（bounded→architectural 过渡，走 spec → plan → SDD）

## 1. 背景与目标

**目标：** 标书导出时同步生成一份独立、可打印的**检查清单文档**，列出投标文件提交关键项（报价、签字签章等），逐项标注其在主标书中的页码与定位状态，留确认勾选栏与备注栏，供内部打印核对后随标书一起交付前自检。

**动机：** 生成的标书包含报价表、投标函、签字盖章页等关键提交要素，散落各章；人工核对时难以逐项定位、容易遗漏（如忘记签字盖章、报价表未生成）。清单把「哪些关键页、在哪一页、是否已就位、我已核对」收拢到一张可打印表。

**非目标（YAGNI）：**
- 不做完整性自动校验引擎之外的自动审阅（不判内容质量，只判「是否定位到 / 是否缺失」）。
- 不做检查清单的持久化 JSON 配置列（自定义行随本次导出请求传入；需要跨导出记忆再加 `checklist_config_json` 列 + alembic 0008）。
- 不把清单混入主标书正文（独立文档，避免交付前删页）。

## 2. 用户流程（需求场景）

1. 用户在项目「导出」面板勾选「导出检查清单」（默认开），可按需增删/改预置行。
2. 点导出：主标书 docx（及所选 pdf）+ 检查清单 docx/pdf 一并产出。
3. 用户打印检查清单，对照主标书逐行核对，在「确认」栏打勾、在「备注」栏手写说明（如「已盖章」「报价表需补」）。
4. 留档或随内部交样流程使用；正式投标文件不含此清单。

## 3. 检查清单内容（规格）

### 3.1 表结构（5 列）

| 列 | 说明 |
|---|---|
| 说明 | 提交项名称（如 `报价（开标一览表/报价表）`、`签字盖章页`） |
| 页码 | 主标书 PDF 中该行关键词首次命中页，1-indexed；定位失败留空 |
| 状态 | `✔ 已定位` / `⚠ 未找到，需核对` |
| 确认 | 空方框（`☐`），打印后人工打勾 |
| 备注 | 生成时为空，打印后手填；表格行高给足书写空间 |

表头之下逐行渲染；清单页首含：标题「投标文件检查清单」、项目名称、生成时间、`提示：请对照标书逐项核对并打勾确认`。

### 3.2 预置行模板（`CHECKLIST_ITEM_TEMPLATES`，9 行）

| key | 说明 | 定位关键词（PDF 扫） | 落库校验源 |
|---|---|---|---|
| quotation | 报价（开标一览表 / 报价表） | `开标一览表`、`报价表` | 开标一览表内容已构建 或 章节含「报价」 |
| bid_letter | 投标函（致招标人） | `投标函` | 章节含「投标函」 |
| legal_rep_cert | 法定代表人身份证明 | `法定代表人身份证明`、`法定代表人` | 商务部分含证明段 |
| authorization | 授权委托书 | `授权委托书` | 章节含「授权委托」 |
| signature_seal | 签字盖章页（法定代表人/委托代理人签字、公章） | `签字`、`盖章`、`签章` | 渲染引擎签名页模板已输出（投标人：（盖章）行） |
| commitment | 承诺书（廉洁承诺/不串标等） | `承诺书` | 章节含「承诺」 |
| qualification | 资格证明（资质证书） | `资质证书`、`证书` | 资质数据非空 或 章节含「资质」 |
| performance | 业绩证明（类似项目合同/中标通知书） | `类似项目`、`业绩`、`中标通知书` | 历史合同数据非空 |
| personnel | 人员配置及证书 | `项目服务人员`、`人员`、`执业证` | 人员/证书数据非空 |

- 每行 `key` 用于自定义行替换/删除；`说明` 可被用户改写。
- 关键词为命中任一即算定位。

### 3.3 状态推导（`derive_status`）

- `✔ 已定位`：PDF 页扫描命中该行任一关键词（页码非空），**或** PDF 不可用时落库校验源通过。
- `⚠ 未找到，需核对`：PDF 未命中 且 落库校验源也未通过（如报价表未生成、无业绩数据时对应行标 ⚠）。
- 状态列文案固定两个枚举值（见上），前端/后端共用。

## 4. 架构与数据流

### 4.0 前提：backend 镜像必须安装 LibreOffice（关键前置，2026-08-28 实测缺失）

**现状**：`export_to_pdf`（render_engine.py:1921，`subprocess ["libreoffice", ...]`）在容器内执行；**后端镜像无 libreoffice/soffice**（服务器宿主有，但容器是 COPY . . 构建、不含宿主二进制）→ 容器内 `export_to_pdf` 恒 `FileNotFoundError → None`。前端 `ProjectWorkflow.tsx:744` 硬编码 `format: 'both'` 且消费 `pdf_url`（:762-763）——**「导出 PDF」是前端一直在请求、后端静默恒空的休眠缺陷**。

**必做**：`backend/Dockerfile` 增加
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
```
- `libreoffice-writer`（headless `--convert-to pdf` 最小集）≈ 镜像 +400MB；`fonts-noto-cjk` 保证中文在 PDF 中正常渲染（pdfplumber 读的是文本层，页码偏移风险来自字形宽度差异，CJK 字体就位后基本一致）。
- 一举两得：修复既有「format=both 但 pdf_url 恒空」缺陷 + 支撑检查清单页码定位。
- **内网风险**：apt 源若不可达则 `docker build` 失败——部署时先验证/配 apt 镜像源（与既有 PIP_INDEX build arg 同思路；若不可达，改用宿主执行转换的 fallback 见 §5）。
- E2E 是运行时验证（容器内跑 e2e_smoke，含 pdf/清单断言）。

### 4.1 挂接点

扩展现有 `POST /bid/{project_id}/export`（`backend/app/api/bid.py`，`export_bid`）——主标书 docx 渲染成功后（`render_bid_to_docx` 之后、响应返回前）同步执行检查清单生成。**清单任何失败不阻塞主标书导出**（try/except 包裹，§13 铁律）。

### 4.2 请求/响应契约（向后兼容）

```python
class ExportRequest:   # 既有字段不动
    ...
    include_checklist: bool = True      # 新增，默认开
    checklist_items: list[dict] | None = None  # 新增：用户自定义行 [{key,label}...]；None=用预置模板
    # 自定义行语义：key=="custom-<n>" 为新增行（label 必填、定位关键词可空）；
    # key 命中预置 key 表示覆盖该行的 label；预置行若未被引用且未删除则按模板默认出
    checklist_removed: list[str] = []   # 新增：被删除的预置行 key 列表

class ExportResponse:   # 既有字段不动
    docx_url: str
    pdf_url: str
    checklist_docx_url: str = ""        # 新增；清单失败/未启用时为空串
    checklist_pdf_url: str = ""         # 新增
```

- 自定义行合并规则：`removed` 中出现的预置 key 不渲染；`checklist_items` 中带预置 key 的条目**覆盖**该行 label；`custom-*` 条目**追加**为自定义行（label 为说明列内容，定位关键词空 → 状态仅依据落库校验源或直接标 ? 需核对——若 label 中含「报价」「签字」等关键词仍可参与 PDF 定位，实现为 label 本身参与关键词扫描）。

### 4.3 检查清单生成内部流程

```
export_bid:
  docx_path = render_bid_to_docx(...)          # 既有
  if data.format in ("pdf","both"):
      pdf_path = export_to_pdf(docx_path)      # 既有
  # ---- 新增（try/except 整段包裹）----
  if include_checklist:
      pdf_for_locate = pdf_path or (export_to_pdf(docx_path) 若存在)  # format=docx 时补渲一次
      rows = checklist_engine.build_rows(items=合并后的行, project, chapters)   # 状态推导（纯函数）
      if pdf_for_locate:
          rows = checklist_engine.fill_pages(pdf_for_locate, rows)   # 页码回填（pdfplumber 页扫）
      checklist_docx = build_checklist_docx(project_name, rows, style, out_dir)
      checklist_pdf  = build_checklist_pdf(checklist_docx)   # 复用 export_to_pdf，失败→pdf_url 空
      return checklist_docx_url/pdf_url
```

- 输出文件进入既有 `OUTPUT_DIR`（与主标书同目录），文件名：`{主标书stem}-检查清单.docx/.pdf`；经既有 `GET /download/{filename}` 服务。

### 4.4 页码定位（关键实现）

- 复用 `pdfplumber`（既有依赖 0.11.0）：`pdfplumber.open(pdf_path)` → `for i, page in enumerate(pdf.pages): text = page.extract_text() or ""`，任一关键词 `in text` → 返回 `i+1`（1-indexed）。扫描全部页取**首次命中**。
- 与 `locate_evaluation_section`（pdf_extractor.py:74）同一模式；抽取公共逻辑或独立实现均可（独立实现更小，避免动既有函数）。
- **语义**：页码 = 主标书 PDF（LibreOffice 同引擎渲染）中的页；Word 打开 docx 页码在字体一致时接近，供打印核对用，允许偏差。
- 性能：一次性扫描，页数≤数百，无增量成本关注点。

### 4.5 清单 docx 渲染（`build_checklist_docx`）

- python-docx，A4 portrait；标题（Heading，`投标文件检查清单`）+ 项目名称 + 生成时间行；table 5 列、表头加粗、网格边框；正文宋体 12pt（沿用 render_engine `_set_run_font` 风格常量）。
- 每行「确认」列填 `☐` 空框；「备注」列空、行高 ≥1.5cm 供书写。
- 返回 docx 绝对路径；失败抛异常由调用方降级。

## 5. 错误处理（spec §13 铁律，三级降级）

| 失败点 | 降级行为 |
|---|---|
| LibreOffice 缺失/apt 装不上（部署期 fallback） | 主标书 PDF 不可用 → 页码列留空，状态仅由落库校验源推导；清单 docx 仍出；`checklist_pdf_url` 空。前端既有的 pdf_url 亦继续为空（维持现状，不倒退） |
| 检查清单构建全套失败 | checklist_*_url 空串 + `logger.warning`，主标书导出正常完成 |
| PDF 渲染失败（LibreOffice 超时等瞬时） | 页码列留空，状态仅由落库校验源推导（`derive_status` 折半），清单 docx 仍出 |
| 单行页定位失败 | 该行页码空 + 状态 ⚠，其余行不受影响 |

全部包裹在 export_bid 的 checklist 段 try/except 内；**任何路径不抛给前端**。

## 6. 待测试项

### 6.1 单元（`:backend/tests/test_checklist_engine.py`）

1. 预置模板：9 行、每行有非空 key/label/关键词、key 唯一。
2. 页定位（参数化注入页文本列表，不走真 PDF）：命中任一关键词返回正确 1-indexed 页码；无命中返回 None；关键词跨行/多页取首次。
3. 状态推导：PDF 命中+落库通过 → ✔；PDF 未命中且落库缺 → ⚠；PDF None 但落库通过 → ✔（含降级路径）。
4. 自定义行合并：removed 剔除预置行；items 覆盖 label；custom-* 追加且 label 参与关键词定位；空 items 回退预置模板。
5. 清单 docx 生成：文件存在、含表头五列、行数 = 传入行数（用 python-docx 读回断言）。

### 6.2 容器 E2E（`backend/scripts/e2e_smoke.py` 扩展）

- 生成完成后 `POST /export`（format="both", include_checklist=True）→ 断言 `pdf_url` 非空（**首次验证容器内 LibreOffice 就位后 PDF 导出真实可用**）+ `checklist_docx_url` 非空且文件存在 + `checklist_pdf_url` 非空。
- 断言清单 docx 表头含「说明」「页码」「确认」。（读回 docx 校验）

## 7. 文件清单

| 动作 | 文件 |
|---|---|
| 修改 | `backend/Dockerfile`（apt 装 libreoffice-writer + fonts-noto-cjk，§4.0 前置） |
| 新建 | `backend/app/services/checklist_engine.py`（中文 docstring + 版权头 `Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.`，§13） |
| 修改 | `backend/app/api/bid.py`（ExportRequest/ExportResponse 字段 + export_bid checklist 段） |
| 新建 | `backend/tests/test_checklist_engine.py` |
| 修改 | `backend/scripts/e2e_smoke.py`（E2E 断言扩展：pdf + 清单） |
| 修改 | `frontend/src/pages/project/ProjectWorkflow.tsx`（导出面板：勾选 + 自定义行编辑 + 结果区清单下载链接 + pdf 真实可达） |
| 新建 | `frontend/src/api/` 导出相关类型（ExportRequest/Response 扩展 + checklist 类型），或就地类型化 |

## 8. 约束与兼容性

- `backend/Dockerfile` 新增 apt 安装（libreoffice-writer + fonts-noto-cjk，§4.0）——镜像 +~400MB、build 时长增加；内网 apt 可达性为部署前提风险（§4.0）。
- 不引入新 Python 依赖（python-docx、pdfplumber、LibreOffice 均既有）。
- 后端任何新文件遵循 §13：中文 docstring + 版权头；AI 相关路径 try/except 降级；asyncio_mode STRICT（纯同步函数不影响）。
- 顺带修复既有休眠缺陷：前端 `format:'both'` 但容器无 LibreOffice → `pdf_url` 恒空（本特性把它点亮）。
- 前向兼容：旧前端调新后端（默认 include_checklist=True）会多产出清单文件，无破坏；旧后端被新前端调用时新增字段被忽略。
- 无 DB 迁移（v1 自定义行随请求传入；持久化后续再加列）。
- 部署沿用已验证流程（tar 排除 config.py、无迁移则无需 alembic、容器 E2E ×3——本轮 E2E 新增 pdf+清单断言为强制项，须生产全绿）。