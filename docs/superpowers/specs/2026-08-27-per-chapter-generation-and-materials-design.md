# 分章节生成 + 素材注入 + 章节对话 + 资料多选设计

> 日期：2026-08-27
> 状态：待评审
> 适用范围：一键生成管线、素材上下文、章节编辑对话、信息搜集选择交互

## 1. 背景与问题

用户三个诉求汇聚成一次架构级改动：

1. **一键生成部分章节没生成**。根因（已排查确认）：
   - 新管线 `generate_from_chapter_structure` 把异常吞成 `[本节生成失败：...]` 占位符，但前端当"已生成"显示（`ai_pipeline.py:1349-1351`）。
   - 空内容叶子触发 `section_error`，但 `content_assembler` 会丢弃空子树，导致整个部分从目录里消失。
   - 新管线从不写 `generation_state_json`，`/generate/retry-failed` 对新管线是死代码。
   - 一次性把所有章节的所有叶子并行提交，上下文过大时叶子 `max_tokens=4096` 预算耗尽 → 空内容 → 章节消失。这正是"一轮对话全部生成可能超 AI 上下文限制"。

2. **素材插入有问题**。后台资源库四类（公司资质 / 人员信息 / 历史合同 / 公司信息）都应可调用：
   - `_gather_generation_context` 已取回四类，但新管线只把 `company_profile` 传进叶子提示词；`contract_context` 构建了却没注入（`ai_pipeline.py:1311-1313`）。
   - 资质在**任何**管线都不注入。
   - 深管线靠关键词注入人员/合同，无资质。

3. **信息搜集（资料选择）交互问题**：
   - 选择后没有明显提示，"选了和没选一样"。根因：状态接口 `analyze_collection_needs` 只做自动匹配、**从不读已落库的选择**（`ProjectQualification/ProjectPersonnel/ProjectContract` 三张表），所以用户在弹窗里的选择刷新后不显示。
   - 四类资源实际全是单选：资质弹窗是单选按钮；`assign_personnel` 删同角色旧记录；`link_contract` 删同需求旧记录；公司信息在搜集页无入口。

### 已确认的交互决定

- 章节对话采用「**对话 + 手动应用**」：AI 回复 + 拟改内容，用户点「应用修改」走 diff 预览确认，可继续对话（用户已在 AskUserQuestion 确认）。
- 自动匹配的资源处理走 **方案① 自动占用**：系统自动匹配到且未被手动改过的资源自动落库为"已选（自动）"，让"已匹配"真的进生成，所见即所得；用户可改可删（本次设计裁定，用户批准实施）。

## 2. 目标 / 非目标

**目标：**
- 一键生成改为分章节提交，每章上下文只装本章需要的招标要求 + 素材，降低上下文超限与漏章节概率。
- 未生成/失败的章节在 UI 上明确标红、可单独补齐；重跑一键生成 = 续传（跳过已生成，补齐失败/空白）。
- 四类素材按章节标题关键词注入叶子提示词，并强制"必须用真实数据、严禁编造"。
- 每节新增多轮 AI 对话，可查看拟改内容并手动应用。
- 信息搜集四类资源全部可多选，选中后有明显视觉反馈，已选/自动匹配/缺失三态清晰。

**非目标：**
- 不重构深管线 `generate_bid_with_deep_outline` 的整体结构，只复用其素材构建逻辑（抽公共函数）。
- 不做真正的对话历史持久化（沿用 `chapter_chat.py` 无状态每轮全量发送的模式，前端持有历史）。
- 不改招标文件解析、目录确认流程。
- 不引入消息队列（现有 SSE 同步生成保持不变）。

## 3. 工作流 A：一键生成分章节提交

### 3.1 现状

`generate_from_chapter_structure`（`ai_pipeline.py:1088-1622`）流程：
1. 文件/表格章节先 `generate_file_section` 生成（失败吞掉 → content=""）。
2. 从所有 `ai_generated` 章节收集全部叶子任务（`_collect_leaf_tasks`，嵌套树 DFS / 旧扁平任务 / 章节自身 fallback 伪树）。
3. `assign_target_budgets` 全局按目标页数给所有叶子分配篇幅。
4. 用 `asyncio.Semaphore(parallel_workers)` 一次性把**所有章节的叶子**并行生成；异常 → `[本节生成失败：{exc}]` 占位符且算成功；空内容 → 静默不存。
5. 统一组装：容器引导段（`generate_container_lead_in`）→ `build_final_chapters_payload` → 回写 `children_json`（有 content 的叶子才写）→ `format_verification` → done。

### 3.2 改造目标

外层改为**逐章节串行**，章节内叶子并行：

```
阶段 0: 加载章节 / 文件章节先生成（不变）
阶段 1: 收集全部叶子 + 全局篇幅分配（纯计算，无 AI 调用）
阶段 2: for chapter in ai_generated 章节:
            build_chapter_context(chapter)      # 3.3
            leaves = 本章叶子（含失败/未生成续传判定）
            if leaves 空: 直接组装该章；continue
            yield chapter_start
            并行生成本章 leaves（semaphore）
            每叶子成功→section_done + 写入 children_json；失败→section_error + 标 failed
            生成本章容器引导段 + 组装本章 payload
            回写 children_json + 落库（每章 commit）
            yield chapter_done
阶段 3: format_verification + done（汇总所有章节 payload）
```

### 3.3 每章上下文构建

`build_chapter_context(chapter_title, requirements, materials)`：
- **招标要求过滤**：从 `requirements` 的 `required_documents/required_personnel/scoring_context` 中，用标题关键词（复用 `_format_requirements_for_section` 的过滤思路）筛出与本章相关的条目。整份 `requirements` 过长（可超上下文）时这是关键约束。**过滤只缩小数组元素，必须保留顶层键结构**（`required_documents/required_personnel/...` 等键仍在），避免生成函数读不到结构而报错。
- **素材过滤**：调用 4.3 的 `assemble_section_materials`（按标题关键词注入四类素材；公司信息始终注入）。
- 输出：`{"filtered_requirements": ..., "materials_guidance": ...}`，传给本章每个叶子的 `generate_section`（requirements 传过滤后的子集，extra_guidance 带素材）。

### 3.4 失败处理（关键改动）

- **叶子异常不再产出占位符**。`_gen_one` 的 `try/except` 改为：异常时返回 `(None, error)`，`full_content=None`；生成空内容同样视为失败。
- **失败叶子写回 `children_json`**：叶子节点加 `status: "failed"` + `error: "..."`（可选 `status: "generated"` 表示成功）。TreePanel 读 `node.status` 显示 ⚠/红标。
- **整章失败隔离**：每章外层 `try/except`；章节级异常记日志、给该章所有叶子标 failed、`yield chapter_error`（含 chapter_title），继续下一章。
- **续传（重跑 = 补齐）**：叶子已有 content（children_json 中 `status == "generated"` 且有 content）→ 跳过；`status == "failed"` 或空 → 重新生成。这样"一键生成没生成的部分"再点一次就能补齐，天然替代死代码 `retry-failed` 对新管线的支持。重跑前把 `project.status` 重新置为 `generating` 的判定保持与现状一致（见 bid.py generate 入口）。

### 3.5 SSE 事件

新增事件（不破坏现有事件流）：
- `chapter_start`：`{chapter_id, title, index, total, leaf_total}`
- `chapter_done`：`{chapter_id, title, content, content_length, leaf_success, leaf_failed}`
- `chapter_error`：`{chapter_id, title, error}`
现有 `section_start / section_done / section_error / progress / outline_generated / format_verification / done / status` 保持。`done` 的 `chapters` 字段现在是逐章累积的完整 payload 列表。

### 3.6 前端 ProjectWorkflow

- `handleGenerate` 增加 `chapter_start / chapter_done / chapter_error` 分支：主要用于进度文案（"正在生成第 N/M 章"）；`chapter_done` 可顺手把该章 content 并入本地章节树。
- `failedSections` 收集逻辑扩展：除解析 `generation_state_json` 外，还要在生成结束后扫描 `children_json` 中 `status === "failed"` 的节点（前端可读章节接口拿到），用于展示"X 个章节未生成"提示与重试入口。
- 重试按钮：点重试走 `POST /bid/generate`（续传语义），不再走 `retry-failed`。

## 4. 工作流 B：素材注入

### 4.1 共享素材上下文模块

新建 `backend/app/services/materials_context.py`，从深管线抽公共函数 + 新增资质构建器：

- `build_qualifications_context(qualifications) -> str`：格式块，表头「【公司资质证件数据 — 以下为真实资质数据，标书中涉及资质、证书时必须原样使用，严禁编造】」，逐条 name / cert_number / issuing_authority。
- `build_personnel_context(personnel) -> str`：从 `ai_pipeline.py:1834-1863` 抽取（深管线改用此函数）。
- `build_contract_context(contracts) -> str`：把 `_format_contract_context`（`ai_pipeline.py:937-972`）迁过来并保留签名；`_gather_contract_data` 逻辑一并迁入（DB 兜底）。
- `SECTION_MATERIAL_KEYWORDS`：四组关键词，复用深管线现有 `CONTRACT_KEYWORDS` / `PERSONNEL_CTX_KEYWORDS`（`ai_pipeline.py:1874-1878`），新增：
  - `QUAL_CTX_KEYWORDS = ["资质", "证书", "资格", "认证", "许可证", "营业执照", "证件", "证明文件", "质量管理", "管理体系"]`
  - 公司信息不靠关键词，始终注入。
- `assemble_section_materials(section_title, *, qualifications, personnel, contracts, company_profile) -> str`：按标题关键词匹配，返回拼好的素材上下文文本（每个块自带"严禁编造"提醒），无匹配返回 ""。**返回前整体截断**，上限常量 `MATERIALS_CTX_MAX_CHARS`（默认 6000，防素材本身反噬上下文）。

### 4.2 注入点

| 位置 | 现状 | 改后 |
|---|---|---|
| 新管线 `_gen_one`（`ai_pipeline.py:1321-1363`） | 只传 company_profile | `extra_guidance = assemble_section_materials(title, ...) + guidance` |
| 深管线 pending 判定（`ai_pipeline.py:1882-1911`） | 关键词注入 contract/personnel | 改调 `assemble_section_materials`（含资质），删内联 personnel 构建 |
| `section_editor.regenerate_section`（`section_editor.py:239-271`） | 只传 company_profile + requirements | 补 `materials_guidance`（四类） |
| `section_editor.modify_section`（`section_editor.py:157-217`） | 无任何素材 | 补 `materials_guidance` 参数 + 注入 |
| 新 `sections/chat`（见 5） | 无 | 注入 `assemble_section_materials` |

`regenerate_section` / `modify_section` / `chat` 需要素材来源：调用处（`chapters.py`）统一先 `get_collected_resources(project_id, db)` 取四类，再 `assemble_section_materials(title, ...)`。把这段取素材逻辑封装成 `chapters.py` 内部 helper `_materials_guidance_for_section(section_title, project_id, db)`。

### 4.3 与 D 的联动

素材来源是 `get_collected_resources`（读已落库的 Project* 表）。方案①自动占用后，自动匹配项也会落库，因此"已匹配"的资质/合同/人员会真实进入生成上下文——这正是 D 与 B 闭环的关键。

## 5. 工作流 C：每节 AI 对话（对话 + 手动应用）

### 5.1 后端

新端点 `POST /api/v1/bid/{project_id}/chapters/{chapter_id}/sections/chat`：

- 请求体：
```json
{
  "section_path": ["第一章", "（一）服务方案"],
  "current_content": "现有正文...",
  "messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
  "instruction": "最新一条用户指令（可选，便于后端单独取用）"
}
```
`messages` 为完整对话历史（前端持有，最多保留最近 ~12 条防上下文膨胀）。

- 处理：`chapters.py` 内 `sections/chat` handler →
  1. 取 `get_collected_resources` → `_materials_guidance_for_section(section_title, ...)`；
  2. 过滤相关招标要求（同 3.3 思路）；
  3. 调 `section_editor.chat_section(...)`（新函数）：系统提示词 = 章节对话助手（章节目录祖先链 + 当前内容 + 素材上下文 + 相关要求），要求 AI 返回 **JSON** `{"reply": "对用户指令的回复/说明", "revised_content": "修改后的完整本节内容（不修改则原样返回当前内容）"}`，`max_tokens = _budget_hint_to_tokens('medium')`（与叶子同档，8192，见 `token_budget.hint_to_tokens`）。返回空内容时抛 502（不静默）。
  4. 返回 `{reply, revised_content}`。无状态、不持久化 conversation_id（与 outline chat 一致）。

### 5.2 前端

- 新组件 `frontend/src/components/TreeEditor/SectionChat.tsx`（复用 `OutlineChat.tsx` 的消息列表 / 滚动 / 输入模式）：
  - 状态：`messages[]`、`revisedContent`、`applying`。
  - 发送：把 `currentContent`（当前编辑器内容）+ `messages` 全量 POST `/sections/chat` → 追加 AI 回复到列表；若有 `revised_content`，底部出现「应用修改」按钮 + 「复制修改后内容」。
  - 「应用修改」→ 复用 EditPanel 现有 diff 弹窗（原文 vs AI 修改 → 接受/放弃），接受后走现有 `handleAcceptAI` 保存路径（`sections/save`）。
  - 继续对话：用户可直接在输入框再追问，messages 继续累积。
- EditPanel：在编辑器下方挂 `<SectionChat>`（仅当选中节点是叶子且可编辑时显示，与「AI 修改」按钮并存，互不干扰）。`isFileType` 章节隐藏（与现状一致）。
- TreeEditor 需把 `currentContent`、`children_json` 上下文透传给 SectionChat（复用现有 props）。

## 6. 工作流 D：资料多选 + 明显选中提示

### 6.1 数据层（`backend/app/services/collection.py`）

**a. 状态接口合并已落库选择。** `analyze_collection_needs` 内：
- 先查本项目的 `ProjectQualification / ProjectPersonnel / ProjectContract`；
- 每个 required doc / personnel item：**优先展示已落库选择**（含自动占用），没有落库选择时才走自动匹配候选。

**b. 方案①自动占用（实现裁定：改到 `confirm_collection` 时批量落库）。** `status` GET 保持只读（避免 GET 带写副作用）；对无落库选择、但存在**确定性自动匹配**的项（排除"返回全部候选"兜底分支），在用户点「确认并继续」时：
- 批量插入 `Project*` 行，`match_status="auto"`；
- 幂等：仅当该 requirement 无任何落库行时插入；
- 人员匹配兜底（`_match_personnel` 的"无匹配返回前5人"）**不自动占用**，只作候选提示。
- 进入生成时 `get_collected_resources` 读出全部落库行（含 auto），四类素材真正进生成。

**c. 多选真正落库：**
- `assign_personnel`：删除"移除同 role 旧记录"逻辑（`collection.py:288-296`），允许多人同 role（对应"需 N 人"）。
- `link_contract`：删除"移除同 requirement 旧记录"逻辑（`collection.py:368-376`），允许多合同同一业绩要求。
- `link_qualification` 已支持多行，不改。
- **新端点**：`POST /collection/{project_id}/qualification/unlink`、`POST /collection/{project_id}/contract/unlink`（请求体 `{requirement_name, resource_id}`；人员已有 `unassign_personnel`，前端改接它）。解除关联时同时可删除 auto 行。

**d. `match_status` 语义扩展**（`analyze_collection_needs` 输出）：
- `selected`：有用户手动选择（含人员 assigned）
- `auto`：自动占用
- `uploaded`：上传了文件
- `matched`：有自动匹配候选但未占用（纯提示）
- `missing`：无任何候选
- 每个 match 项加 `selection` 字段（`"selected" | "auto"`）供前端区分标签颜色。

### 6.2 前端（`CollectionStep.tsx` + `QualificationPickerModal.tsx`）

**a. 资质弹窗多选。** `QualificationPickerModal`：
- `qualification` 模式加入 `rowSelection`（复选框），`MULTI_SELECT_MODES` 加入 `'qualification'`；
- `onSelectQual` → `onSelectQuals: (quals: Qualification[]) => void`；footer 复用"已选 N 项 + 确定选择"；
- 后端循环 `link_qualification` 落库（幂等，重复链接自动去重——见实现）。
- `history_bid` 保持单选（仅参考用途，不落库）；`company` 保持只读展示。

**b. 信息搜集页每行状态明显化。**
- 标题图标与状态 Tag 三态：`selected` → 绿勾「已选择 N 项」；`auto` → 蓝 Tag「自动匹配」；`missing` → 红叉「待处理」。
- 行描述展示**全部**已选/自动项名称（现在只显示 `matches[0]`），每项带「×」移除按钮（调 unlink/unassign）。
- 有选择的行使整行高亮（浅绿背景 + 左侧竖条），与未选明显区分。

**c. 已选资源汇总卡片。** CollectionStep 底部（确认按钮上方）加卡片：
- 汇总本项目已落库的资质 / 人员 / 合同（含 auto），分块列出，可移除；
- 公司信息卡片：显示当前 `CompanyProfile` 摘要 + "生成时将自动注入标书"提示（补齐"公司信息在搜集页无入口"的缺口）。

**d. 完成判定。** `is_complete` 改为基于 `selected/auto/uploaded`（即有落库占用）计算，而非自动匹配候选；进度条 `done` 同理。

## 7. 数据模型 / Schema

- **无新表、无新增列**。`match_status` 为字符串列（现有），新增取值 `auto` 不要求迁移。若模型上存在对 match_status 的 enum/check 约束，需先移除（实现时验证）。
- `children_json` 叶子节点结构新增可选字段：`status: "generated" | "failed"`、`error?: str`。
- 前端 `ResourceMatch` 接口扩展 `match_status` 取值与 `matches[].selection`。

## 8. API 契约汇总

| 端点 | 动作 |
|---|---|
| `GET /collection/{id}/status` | 响应增强（三态 + selection + 全部 matches） |
| `POST /collection/{id}/qualification/unlink` | 新增 |
| `POST /collection/{id}/contract/unlink` | 新增 |
| `POST /bid/{id}/chapters/{cid}/sections/chat` | 新增 |
| `POST /bid/{id}/generate` | SSE 新增 `chapter_start/chapter_done/chapter_error`；重跑=续传 |
| `POST /bid/{id}/generate/retry-failed` | 保留；新管线改为前端不再调用（续传取代） |

## 9. 错误处理

- 叶子生成：异常/空内容 → 标 failed（写 children_json）+ `section_error`，不再静默成功。
- 章节级：`try/except` 包裹 → `chapter_error`，继续后续章节。
- 对话端点：AI 返回空 → 502 明确报错，不静默。
- 素材上下文：构建失败返回空字符串并记 warning（不阻断生成）。
- 自动占用：插入失败记 warning（不阻断状态读取）。

## 10. 测试

**后端单测**（`backend/tests/`）：
- `test_collection_status_merges_persisted_selection`：落库选择出现在 status matches 且 `selection="selected"`。
- `test_assign_personnel_multiple_same_role` / `test_link_contract_multiple_same_requirement`：多选不互相覆盖。
- `test_auto_occupy_qualification_and_contract` / `test_auto_occupy_skips_personnel_fallback`：确定性匹配才自动占用，兜底候选不占。
- `test_build_qualifications_context` / `test_assemble_section_materials_keyword_filter`：关键词命中与截断。
- `test_section_chat_returns_reply_and_content`：mock ai_adapter 返回 JSON，断言解析。
- `test_generate_marks_failed_leaf_in_children_json`：mock 生成器抛异常，断言叶子标 failed、事件含 section_error。
- `test_generate_resume_skips_done_leaves`：已有 content 的叶子不再生成。

**前端**：`npm run build` 通过；手工冒烟四块功能。

**E2E**：`docker exec -w /app -e PYTHONPATH=/app hongxi-backend python scripts/e2e_smoke.py` 全绿；`pytest` 后端套件（注意 Windows 本机 `test_format_template_to_prompt_text` 乱码为环境问题，非回归）。

## 11. 部署与迁移

- 部署流程遵循 `server-deployment-config.md`：`backend/app`、`backend/scripts/e2e_smoke.py`、`frontend/src` 全量打包 → pscp → 服务器解压 → `docker compose up -d --build backend frontend`。
- 无 schema 变更：`alembic stamp` 保持当前 head（0006），不需要 upgrade。
- 服务器保留 `config.py / docker-compose.yml / .env`。
- 回滚：`.deploy-backup-<ts>/`。

## 12. 实施顺序

1. D 数据层（多选落库 + 状态合并 + unlink 端点）→ D 前端（弹窗多选 + 三态显示 + 汇总卡片）。
2. B 素材模块（`materials_context.py` + 注入点）。
3. A 分章节生成（`generate_from_chapter_structure` 重构 + SSE + 前端事件）。
4. C 章节对话（后端 chat 端点 + 前端 SectionChat）。
5. 全量测试 + E2E + 部署。
