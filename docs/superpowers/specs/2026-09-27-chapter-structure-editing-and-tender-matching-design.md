# 章节结构可编辑 + 招标原文精确匹配 — 设计

> 日期：2026-09-27
> 状态：待评审
> 适用范围：目录确认页（章节结构）、章节类型语义、固定格式/表格/附件三类章节的内容产出

## 1. 背景与问题

用户反馈：**换一个招标文件，解析出来的章节结构就很不准确**。现有的章节结构完全由 AI 从「投标文件格式」章节一次性提取，用户只能看着，改不了、也挑不了。

调研（2026-09-26）确认的现状与缺陷：

1. **用户在结构页的手动编辑一个字都不会落库**。`OutlineTree` 的增删改只改 React state；删除确认框自己写着「此操作不会上传到服务器」。保存通道只有 `POST /bid/{pid}/chapters/chat`（AI 对话改结构）和 `POST /bid/{pid}/outline/confirm`（**不收任何 payload**，服务端从 `chapter_structure_json` 物化）。后果：**用户删掉的章节照样生成，用户加的章节不会被生成**。
2. **没有拖拽排序能力**。项目里没有任何拖拽库，两棵树（`OutlineTree` / `TreePanel`）都没开 antd `Tree` 的 `draggable`。
3. **章节类型只读**。`ChapterType`（`fixed_form | table | ai_generated | attachment | mixed`）已在前后端和 DB 全线存在，左树也已在渲染彩色 Tag，但用户改不了。
4. **`table` 类型名不副实**。生成管线 `ai_pipeline.py` 的文件章节循环里，只有 `chapter_type == "fixed_form"` 会走原文回填；`table` 直接掉进 `generate_file_section`，即 **AI 自由生成**——用户选了「表格」拿到的是 AI 编的表。
5. **`attachment` 与 `mixed` 类型的章节完全不产出内容**。生成管线的两个循环分别只收 `("fixed_form", "table")` 和 `ai_generated`，这两类是空集，最终在 `_render_body_strict` 落到占位文本「（待补充：本节内容按招标文件要求由投标人填写）」。
6. **匹配原语已有但太窄**。`template_filler.extract_fixed_form_section(section_title=…)` 就是「按标题在招标原文里截取小节」，已被生成管线接线；但它的 `SECTION_HEADER_RE` 只认「一、标题」一种编号，只在 `format_section_text`（投标文件格式章节）里找，且只有单向后缀子串匹配。
7. **表格回填链路断裂**。`template_filler.batch_fill_tables` 已写好（含「取不到值不覆盖」语义）但**生产代码零调用**，`fill_fixed_form_section_from_template` 内部拿到的 `scan_result["table_fills"]` 被直接丢弃。只有「开标一览表」是特例且已完整实现。
8. **匹配逻辑散落 5 处**，各写各的双向子串匹配：`chapters.py`、`ai_pipeline.py`（`_get_section_guidance`）、`format_verifier.py`（两处）、`materials_injection.py`。

## 2. 目标 / 非目标

**目标：**

- 结构页的增删改、拖拽排序、类型选择**真正落库**，并在确认时生效。
- 每个章节节点可指定四类之一：**固定格式 / 表格 / 附件 / AI 生成**。
- 选了固定格式或表格时，**按用户输入的标题在招标文件中精确匹配**出对应原文/表格，匹配结果可预览、可从候选中手动指定，并**固化**下来。
- 生成阶段严格按类型取材：`fixed_form` 照抄原文只填空、`table` 照招标表格定义填、`attachment` 按清单插图、`ai_generated` 才由 AI 撰写。
- 匹配失败**当场告知**，不静默降级。

**非目标：**

- 不改「目录确认」之后的阶段。已确认/已生成的项目回改结构不做（用户 2026-09-27 确认）。
- 不改招标文件解析（`pdf_extractor` / `document_parser`）本身。
- 不补 docx 招标文件的格式章节提取与表格结构化（`extract_format_section` 只支持 PDF；docx 表格在 `document_parser._parse_docx` 被压平成文本）。见 §14 风险。
- 不做真正的拖拽跨层级移动 UI 之外的富交互（如框选多节点）。
- 不清理另外 4 处散落的子串匹配（本次只新建统一工具并在新链路使用；旧调用点保持不动，避免回归面扩大）。

## 3. 已确认的关键决策

| # | 决策 | 结论 |
|---|---|---|
| D1 | 匹配不上或匹配到多个时 | **当场告知 + 给候选让用户挑**；不静默降级 |
| D2 | 「附件」章节的内容来源 | **资源库挑选 + 本项目上传**（保证金凭证等本项目专用文件资源库里没有） |
| D3 | 能力覆盖阶段 | **只在「目录确认」之前** |
| D4 | 匹配结果是否持久化 | **持久化**（方案 A）：编辑时解析成"匹配引用"写进结构 JSON，生成时直接用引用，不再按标题猜 |

## 4. 数据形状

沿用 `bid_projects.chapter_structure_json`。每个节点在现有字段基础上新增两个。`match` 与 `attachments` **绑在同一个节点上**，因此拖拽重排不会让引用错位。

```json
{
  "order_index": 1,
  "number": "一",
  "title": "投标函",
  "type": "fixed_form",
  "required": true,
  "format_notes": null,
  "scoring_context": null,
  "table_columns": null,
  "children": [],

  "match": {
    "status": "matched",
    "source": "format_section",
    "matched_title": "投标函",
    "page": 32,
    "start": 28500,
    "end": 34200,
    "table_index": null,
    "picked": "auto",
    "score": 1.0,
    "corpus_hash": "sha1:ab12…",
    "candidates": null
  },

  "attachments": [
    {"kind": "qualification",   "id": "…", "label": "营业执照",   "path": "ocr/xxx.png"},
    {"kind": "personnel_cert",  "id": "…", "label": "电工证",     "path": "ocr/yyy.png"},
    {"kind": "contract",        "id": "…", "label": "消防服务合同", "path": "ocr/zzz.png"},
    {"kind": "upload",          "id": "…", "label": "保证金缴纳凭证", "path": "uploads/aaa.pdf"}
  ]
}
```

### 4.1 `match` 字段语义

| 字段 | 说明 |
|---|---|
| `status` | `matched` / `ambiguous` / `missing` / `na`。前端徽标直接读这个值 |
| `source` | `format_section`（投标文件格式章节）/ `full_text`（全文）/ `table`（表格） |
| `matched_title` | 命中的原标题行文字（可能与用户标题不同，这正是"精确匹配"要展示给用户的） |
| `page` | 命中位置所在页码（`format_pages` 起算；全文兜底时为近似值） |
| `start` / `end` | 在**该语料文本**中的字符区间，生成时直接切片 |
| `table_index` | `type == "table"` 时指向 `requirements["format_tables"]` 的下标 |
| `picked` | `auto`（系统最佳）/ `manual`（用户从候选里点的） |
| `score` | 匹配得分，便于前端解释与调试 |
| `corpus_hash` | 所用语料文本的 sha1，用于判断"原文变了没" |
| `candidates` | `ambiguous` 时携带候选列表；用户选定后置为 `null` 以缩小体积 |

`type == "ai_generated"` 时 `match = {"status": "na"}`。

### 4.2 `attachments` 字段语义

`kind` 取值 `qualification` / `personnel_cert` / `contract` / `upload`。前三者引用资源库行（`id` 为对应表主键），`upload` 引用本项目上传的文件（`path` 为 `UPLOAD_DIR` 下相对路径）。`label` 是渲染时插在图片下方的说明文字。

### 4.3 向后兼容

老项目的 `chapter_structure_json` 没有 `match` / `attachments`。读取侧一律兜底：`match` 缺失视作 `{"status": "na"}`，`attachments` 缺失视作 `[]`。**不报错、不迁移历史数据。**

## 5. 匹配器（新建公共件）

新建 `backend/app/services/tender_section_matcher.py`。

### 5.1 接口

```python
@dataclass
class MatchCandidate:
    title: str          # 原标题行文字
    page: int | None
    start: int
    end: int
    level: int
    score: float

@dataclass
class MatchResult:
    status: str                       # matched | ambiguous | missing
    source: str | None                # format_section | full_text | table
    best: MatchCandidate | None
    candidates: list[MatchCandidate]
    table_index: int | None
    corpus_hash: str | None

def match_tender_section(
    title: str,
    *,
    chapter_type: str,
    format_section_text: str | None,
    full_text: str | None,
    format_tables: list[dict] | None,
    format_start_page: int | None,
) -> MatchResult: ...
```

### 5.2 标题归一化

复用 `template_filler._normalize_title` 的语义（去全部空白 + lower），并**扩展**：全角转半角、去掉首尾的编号前缀（复用 `_TITLE_NUM_PREFIX_RE`）。

### 5.3 编号体系扩展（关键修正）

现有 `SECTION_HEADER_RE`（`template_filler.py`）只认「一、标题」。按层级扩展为：

| 层级 | 形态 | 样例 |
|---|---|---|
| 1 | `第X章` / `第X节` | `第一节 投标文件格式` |
| 1 | 中文数字 + `、．.` | `一、投标函` |
| 2 | 括号中文数字 | `（一）投标函` / `(一)投标函` |
| 4 | 多级阿拉伯数字 | `1.1` / `1.1.1` |
| 3 | 阿拉伯数字 + `、．.` | `1、投标函` / `1. 投标函` |

**匹配顺序必须是 4 → 3**（`1.1` 也以 `1.` 开头，先判 4 才能正确判层级）。

行尾仍受现有约束：标题行文字长度上限 80 字符；沿用 `_PAGE_NUMBER_LINE_RE` + `_has_body` 剔除目录条目（目录条目后面只有页码，无正文）。

### 5.4 匹配流程

1. **候选枚举**：用 5.3 的正则扫语料，产出 `(title, start, end, level)` 列表。语料优先 `format_section_text`；为空或零候选时退到 `full_text`。
2. **打分**：
   - 归一化后完全相等 → `1.0`
   - 互为子串 → `0.8 × (短串长度 / 长串长度)`
   - 否则 bigram Dice 系数 × `0.6`
3. **定档**（阈值可在实现时微调，但必须由测试钉死）：
   - 无候选 ≥ `0.75` → `missing`
   - 最高分 ≥ `0.9` **且** 次高分与最高分差 ≥ `0.15` → `matched`
   - 否则 → `ambiguous`，返回全部 ≥ `0.75` 的候选（按分数降序，最多 8 条）
4. **区间截取**：从命中标题行起，到**下一个层级 ≤ 当前层级的标题行**前；末节取到语料末尾。
   > 这是对现有实现的实质修正：`extract_fixed_form_section` 现在是"碰到下一个 `一、` 就停"，遇到 `（一）` 这类子级标题会被误当边界截断。
5. **表格**：`chapter_type == "table"` 时，除文本匹配外再在 `format_tables` 里按**表标题行/表头单元格**匹配，命中则填 `table_index` 并把 `source` 置为 `table`。若文本与表格都命中，**表格优先**（用户选的是"表格"）。
6. **`corpus_hash`**：对实际使用的语料文本取 sha1。

### 5.5 匹配引用的失效处理

生成阶段（§7）用 `start/end` 切片前，先校验 `corpus_hash` 与实际语料是否一致：

- 一致 → 直接切片，**不重新匹配**（保证"所见即所得"）。
- 不一致 → 按 `title` 重新匹配一次：
  - 重新命中 → 用新结果，并在格式校验报告里记一条 `warn`：`「投标函」的原文位置已变化，已按标题重新匹配`。
  - 仍未命中 → 该章降级为 AI 生成，在报告里记 `error`。

因为 `parsed_requirements_json` 只在重新解析时整体重建（而重新解析等于换了个项目），hash 不一致在实践中罕见，这一层是安全网。

## 6. 全文语料持久化

`full_text` 目前**不落库**（`document_parser.parse_document` 的结果只在 `upload-and-parse` 请求内使用）。而 §5.4 的全文兜底需要它。

方案：在 `bid.py` 的 `upload-and-parse` 里，把全文写到 `UPLOAD_DIR/parsed/{project_id}.txt`，并把相对路径存进 `parsed_requirements_json["full_text_path"]`。

- 选文件而非新增 DB 列：**避免一次 alembic 迁移**（全文可达数百 KB～数 MB，塞进被频繁读取的 `parsed_requirements_json` 会拖慢所有读该字段的路径）。
- 写入失败不阻断解析，只记 warning；此时全文兜底自动降级为"只匹配格式章节"。

> 若认为「只在投标文件格式章节里匹配」就够用，这一节可整段砍掉——但那样「在**招标文件中**精确匹配」就退化成「在格式章节中匹配」，需用户确认。**本设计采用保留全文兜底。**

## 7. 接口契约

| 端点 | 方法 | 作用 | 变更 |
|---|---|---|---|
| `/bid/{pid}/chapter-structure` | `PUT` | 保存整棵树到 `chapter_structure_json` | **新增** |
| `/bid/{pid}/chapter-structure/match` | `POST` | 试匹配 | **新增** |
| `/bid/{pid}/attachments/upload` | `POST` | 上传本项目专用附件文件 | **新增** |
| `/bid/{pid}/outline/confirm` | `POST` | 目录确认 | **改造**：body 可选携带整棵树 |

### 7.1 `PUT /bid/{pid}/chapter-structure`

- 请求体：`{"chapters": [OutlineChapter…]}`（即前端整棵树，含 `match` / `attachments`）
- 服务端**不重新匹配**：`match` 由前端从 `match` 端点拿到的结果原样带回。服务端只做结构校验（字段合法性、`type` 取值、`order_index` 归一化），然后整体覆盖写 `chapter_structure_json`。
- 拒绝空树（`chapters == []`）→ 400，避免误清空。
- 仅允许 `status in ("structure_ready", "draft")` 的项目调用；已确认（`collecting` 及之后）返回 409。

### 7.2 `POST /bid/{pid}/chapter-structure/match`

- 请求体：`{"title": "投标函", "type": "fixed_form"}`
- 响应：`{"status": …, "source": …, "best": {…}, "candidates": […], "table_index": …, "corpus_hash": …}`
- `best` 含原文片段（**最多 4000 字符**，超出截断并在 UI 上标注），供前端预览。
- 纯读操作，不改任何状态。

### 7.3 `POST /bid/{pid}/attachments/upload`

**附件清单不需要按节点寻址的端点。** 结构树的节点没有稳定 id（`OutlineChapter` 只有 `order_index/number/title`），按编号寻址会在用户改编号后错位。既然前端本来就要 `PUT` 整棵树，附件就直接住在树里：

- 本端点只做一件事：收 `multipart` 文件，落到 `UPLOAD_DIR/project_{pid}/`，返回一条完整记录：
  ```json
  {"kind": "upload", "id": "…", "label": "保证金缴纳凭证", "path": "project_xxx/aaa.pdf"}
  ```
- 前端把这条记录 append 进目标节点的 `attachments`，然后随整棵树 `PUT chapter-structure` 保存。
- 「从资源库挑」不经过任何端点写库 —— 前端调已有的资质/人员证书/合同列表接口拿到行，组装成 `{kind, id, label, path}` 直接塞进树。
- 校验在 `PUT chapter-structure` 里统一做：`kind` 合法、`path` 必须落在 `UPLOAD_DIR` 内（防目录穿越）、引用的资源库行仍存在（不存在则剔除该条 + `warn`）。

### 7.4 `POST /bid/{pid}/outline/confirm`（改造）

- body 可选：`{"chapters": [...]}`。**带了就先执行 7.1 的保存逻辑，再物化**，避免"用户改了忘了保存"。
- 不带时行为完全不变（向后兼容）。
- `_materialise_chapters`（`chapters.py`）需把节点的 `match` 与 `attachments` 一并写进 `ProjectChapter.chapter_meta_json`。

## 8. 生成管线改造

`ai_pipeline.py` 的 `generate_from_chapter_structure`，文件章节循环（现只处理 `("fixed_form","table")`）改为显式三分支 + 附件分支：

### 8.1 `fixed_form`

- 取 `chapter_meta_json["match"]`，校验 `corpus_hash` 后按 `start/end` 切出原文。
- 把切好的文本传给现有 `fill_fixed_form_section_from_template`。
  - **需给该函数加一个"直接给原文片段"的入参**（如 `section_text_override`），跳过它内部的 `extract_fixed_form_section` 重新定位。
- 切片失败或 hash 失效 → 走 §5.5 的重匹配 → 仍失败则 `generate_file_section`（AI 兜底）+ 报告记 `error`。

### 8.2 `table`（本次实质修复）

现在 `table` 直接掉进 `generate_file_section`（AI 自由生成）。改为：

1. 按 `match.table_index` 从 `requirements["format_tables"]` 取表（`{page, table_index, rows}`）。
2. 用首行作为**列定义**，生成 markdown 表头。
3. 接上现有死代码 `batch_fill_tables`（`template_filler.py`）填值——其"取不到值不覆盖"语义正是要的。
4. 同时把 `scan_result["table_fills"]` 从 `fill_fixed_form_section_from_template` 里带出来（现在被丢弃），供 fixed_form 章节内的表格使用。

兜底链：`table_index` 失效 → 按标题重匹配表格 → 仍失败 → **只输出 `table_columns` 声明的空表头**（不写假数据）+ 报告记 `error`。

**不得破坏开标一览表特例**：`extract_bid_opening_data` + `build_bid_opening_table` 的完整实现，以及 `render_engine` 中对它的重复渲染跳过，保持原样。

### 8.3 `attachment`（本次新建）

- 读 `chapter_meta_json["attachments"]`，逐个渲染成 `[IMG:path|label]` 标记（复用 `materials_injection` 的标记形态与 `render_engine._render_image_marker`）。
- 必须复用 `materials_injection.drop_already_embedded` 的**按图片内容哈希去重**，否则同一张营业执照会重复出图（此坑历史上修过两轮）。
- 空清单 → 保留占位文本 + 报告记 `warn`（`「X」未挂载任何附件`）。

### 8.4 `mixed`

`mixed` 由 AI 提取产生，用户不可选。生成阶段按 `ai_generated` 处理（进叶子循环），并在结构页允许用户把它改成四类之一。

### 8.5 失败不静默

上述所有降级/兜底一律写入 `format_verification_json`，在导出前的检查清单里可见。

## 9. 前端交互

改造集中在 `frontend/src/pages/project/OutlineConfirm.tsx` + `frontend/src/components/OutlineEditor/OutlineTree.tsx` + `frontend/src/api/outline.ts`。

1. **拖拽排序**：开 antd `Tree` 原生 `draggable` + `onDrop`，重排复用现有 `flatten/getNode/updateNode/removeNode/insertNode`（`OutlineTree.tsx`）。**不引第三方拖拽库。**
2. **类型下拉**：节点上加下拉，四选一（固定格式 / 表格 / 附件 / AI 生成）。文案与颜色直接复用现成的 `TYPE_LABELS` / `TYPE_COLORS`。`mixed` 保留展示但不给选。
3. **匹配状态徽标**：🟢 `已匹配 · 第32页` / 🟡 `3 个候选` / 🔴 `未匹配` / ⚪ `—`（`ai_generated`）。
4. **匹配抽屉**：点徽标打开右侧抽屉 —— 高亮显示匹配到的原文片段、候选列表（点选即切换，写 `picked: "manual"`，并改 `start/end`/`source`）、附件清单编辑器。
5. **实时匹配**：改标题或改类型 → 防抖 500ms 调 `match` 端点 → 刷新徽标与抽屉内容。
6. **附件挂载**：附件抽屉内两个入口 —— 「从资源库选」直接调已有的资质/人员证书/合同列表接口，选中行组装成附件记录塞进当前节点；「上传本项目文件」调 `POST attachments/upload` 拿回落盘记录同样塞进节点。两者都只改本地树，随后由防抖 `PUT` 统一落库（§7.3）。
7. **保存**：任一编辑（拖拽/改标题/改类型/挂附件）后防抖 800ms 调 `PUT chapter-structure`；同时在「确认目录」时把整棵树随 `confirm` 一起提交（双保险）。
8. **确认前拦截**：存在 `missing` 章节时弹框列出这些章节的标题，文案明确说明「这些章节不会照抄招标原文，将由 AI 撰写」，用户可选择返回修改或继续。

## 10. 错误处理与降级

| 场景 | 行为 |
|---|---|
| 匹配端点：标题为空 | 返回 `status: "missing"`，不报错 |
| 匹配端点：语料为空（无 `format_section_text`） | 全文兜底；全文也没有 → `missing` |
| 保存端点：空树 | 400，拒绝清空 |
| 保存端点：项目已确认 | 409 |
| 附件：引用的资源库行已被删除 | `PUT` 保存时校验并剔除该条 + `warn`（不整单拒绝，避免用户白填一遍） |
| 附件：`path` 越出 `UPLOAD_DIR` | `PUT` 保存时剔除 + `warn` |
| 附件：上传文件类型不在白名单 | 400（白名单：png/jpg/jpeg/pdf） |
| 生成：`corpus_hash` 失效 | 重匹配 → 成功则 warn，失败则降级 AI + error |
| 生成：`table_index` 失效 | 重匹配表格 → 空表头 + error |
| 生成：`attachment` 清单为空 | 占位文本 + warn |
| 全文落盘失败 | 解析继续，全文兜底降级为仅格式章节 + warn |

## 11. 数据模型 / 迁移

- **无 schema 变更，无 alembic 迁移。** `match` / `attachments` 放在已有的 `chapter_structure_json`；物化后放在已有的 `ProjectChapter.chapter_meta_json`；全文路径放在已有的 `parsed_requirements_json`。
- 已知不一致（不在本次修复范围，但实现时需注意）：`chapter_type` 默认值有三套 —— 模型 `default="text"`、迁移 `server_default="text"`、`schemas/project.py` `"text"`，而前端与管线把缺省当 `ai_generated`。**本次新增代码一律显式判定，不依赖默认值。**

## 12. 测试

**后端单测（`backend/tests/`）：**

- `test_match_exact_title` / `test_match_substring_title`
- `test_match_ambiguous_returns_ranked_candidates`
- `test_match_missing_below_threshold`
- `test_match_all_numbering_styles` —— `一、` / `（一）` / `(一)` / `1.` / `1、` / `1.1` / `第X节`
- `test_match_boundary_stops_at_same_or_higher_level` —— **本次关键修正点**：`（一）` 子级标题不得截断父级区间；同级标题必须截断
- `test_match_skips_toc_entries` —— 目录条目（后接页码、无正文）不得被当成命中
- `test_match_table_prefers_table_over_text`
- `test_match_table_by_caption_and_header`
- `test_corpus_hash_detects_changed_source`
- `test_full_text_fallback_used_when_format_section_missing`
- `test_generate_table_chapter_fills_from_matched_table` —— 表格分支（此前是死代码 / AI 生成）
- `test_generate_fixed_form_uses_stored_offsets_not_title_relookup`
- `test_generate_attachment_chapter_emits_image_markers` + `test_attachment_dedup_by_content_hash`
- `test_generate_stale_match_refalls_back_and_warns`
- `test_confirm_accepts_tree_and_persists_match`
- `test_put_chapter_structure_rejects_empty_and_locked`
- `test_put_chapter_structure_prunes_invalid_attachments` —— 资源库行已删 / `path` 越出 `UPLOAD_DIR` 的附件被剔除且不整单拒绝

**前端：** `npm run build` 通过；手工冒烟拖拽、改类型、切候选、挂附件、刷新后仍在。

**E2E：** `scripts/e2e_smoke.py` 全绿；**另拿 `结果/招标文件-红云红河…签章.pdf` 跑一遍完整链路，人工逐字核对固定格式章节是否等于招标原文、表格列定义是否与招标一致。**

## 13. 实施顺序

1. **匹配器** `tender_section_matcher.py` + 全部匹配类单测（独立可验证，不碰现有链路）
2. **端点**：`PUT chapter-structure`、`POST match`、`POST attachments/upload` + `confirm` 改造 + `_materialise_chapters` 带 `match`/`attachments`
3. **全文落盘**（§6）
4. **生成管线**：三分支 + `attachment` 新建 + `batch_fill_tables` 接线
5. **前端**：拖拽 + 类型下拉 + 徽标与抽屉 + 确认前拦截
6. 全量测试 + 真实标书复测 + 部署

## 14. 风险与未决

- **docx 招标文件不覆盖**：`extract_format_section` 只支持 PDF；docx 的表格在 `document_parser._parse_docx` 被压平成 `" | ".join(cell.text)` 文本行，结构丢失。若客户的招标文件有 docx，本次改造对该格式无效。**需确认实际交付中 docx 招标文件占比。**
- **匹配阈值需实测校准**：§5.4 的 `0.75 / 0.9 / 0.15` 是初值，需用真实招标文件（至少 3～5 份不同来源）跑一遍看误判率，再定稿。
- **`mixed` 类型的归属**：本次按 `ai_generated` 处理。若实际是"半固定半生成"，需要更细的设计。
- **旧项目无 `match` 字段**：兜底为 `na`，即这些项目的老章节仍走"按标题现找"的老路径（`fixed_form` 走 `fill_fixed_form_section_from_template` 内部定位）。这意味着**旧项目不会获得本次的精度改善**，除非在结构页重新过一遍。可接受，但需知情。
- **散落的 5 处子串匹配未统一**：本次只新建工具并在新链路使用。`chapters.py` / `ai_pipeline.py` / `format_verifier.py` / `materials_injection.py` 的旧匹配仍在，可能出现"结构页显示已匹配、校验器却按老逻辑判不匹配"的不一致。**需在实施第 4 步后专门核对一次。**

## 15. 相关

- `docs/superpowers/specs/2026-07-29-bid-format-compliance-design.md`（格式强制遵循 v1：四级定位、`format_template` schema）
- `docs/superpowers/specs/2026-07-29-bid-format-compliance-design-v2.md`（双轨生成、`generation_strategy`）
- `docs/superpowers/specs/2026-08-27-per-chapter-generation-and-materials-design.md`（分章节生成、`children_json`、素材注入）
