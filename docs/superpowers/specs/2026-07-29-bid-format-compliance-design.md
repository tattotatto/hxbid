# 招标文件格式强制遵循系统 — 技术设计规格

**日期**: 2026-07-29
**状态**: 设计阶段
**关联**: [[2026-06-22-自动投标书生成系统-design]]

---

## 1. 问题陈述

### 1.1 当前缺陷

系统生成标书后经专业人员审核，主要问题是**格式不符合招标文件要求**。根源在于：

1. **格式提取缺失**：`parse_bid_requirements()` 只提取项目信息（名称、预算、资质要求），不提取招标文件中"投标文件格式"章节的结构化格式要求。该章节在不同招标文件中位置不固定（可能是第六章、第七章、第八章等），需要智能定位而非硬编码章节号
2. **生成约束松散**：AI 通过文字 prompt 约束格式（"使用 ## 和 ### 标记标题"），属于"软约束"，容易跑偏
3. **无格式校验**：生成后没有自动检查环节，问题要靠人工审核才能发现

### 1.2 危害

格式不对的标书会被直接废标。如：投标报价一览表缺少规定列、章节序号与招标文件不一致、承诺书落款格式错误等。

---

## 2. 解决方案：四阶段格式保障流水线

```
招标文件 ─→ ①格式提取 ─→ bid_format_template (JSON, 唯一格式真相源)
                                  │
              ②大纲生成（强制按模板结构）  ←── 模板约束
                                  │
              ③内容生成（每节带格式约束）  ←── 模板约束
                                  │
              ④格式校验（自动修正+标记）  ←── 模板校验
                                  │
                          render_engine → .docx
```

### 核心原则

- **bid_format_template** 是从招标文件中"投标文件格式"章节提取的结构化格式定义，是整个生成流程的**唯一格式真相源** (single source of truth)
- 所有格式决策回溯到此模板，AI 不能偏离
- 能自动修正的自动修，修不了的目标记出来让人审

---

## 3. 新增/改造模块

### 3.1 `format_extractor.py`（新增）— 格式提取器

**职责**: 从招标文件中智能定位并提取"投标文件格式"章节的结构化格式模板

**输入**: 招标文件全文文本
**输出**: `bid_format_template` dict

#### 3.1.1 智能格式章节定位

不同招标文件把"投标文件格式"放在不同章节（可能是第五章、第六章、第七章、第八章等）。不能硬编码章节号，需要**基于内容语义定位**。

定位策略（按优先级尝试）：

1. **关键词匹配定位**：在全文搜索以下模式，找到"投标文件格式"相关章节的起止位置
   ```
   第[一二三四五六七八九十\d]+章.*投标文件格式
   第[一二三四五六七八九十\d]+章.*投标书格式
   第[一二三四五六七八九十\d]+章.*投标文件.*格式
   第[一二三四五六七八九十\d]+节.*投标文件格式
   ```
2. **目录推断定位**：如果招标文件有目录页（含"目录"标题的连续页码），从目录中提取"投标文件格式"所在页码，再跳到对应正文位置
3. **语义分块定位**：如果以上都失败，对全文做语义分块（按"第X章"或"第X节"切分），用 AI 判断哪个块是格式章节
4. **全文尾部兜底**：如果以上都失败，取招标文件正文的后 40%（格式要求通常在文档后半部分）

定位结果包含：
```python
{
    "chapter_number": "六",           # 或 null（未识别到章节号）
    "chapter_title": "第六章 投标文件格式",
    "start_offset": 28500,           # 在全文中的起始字符位置
    "end_offset": 34200,             # 结束字符位置（或 null=直到文末）
    "method": "keyword_match",       # 使用的定位方法
    "section_text": "..."            # 提取的格式章节全文
}
```

#### 3.1.2 AI 格式提取

**关键数据结构 bid_format_template**:

```json
{
  "document_structure": [
    {
      "number": "一",
      "title": "商务部分",
      "required": true,
      "start_new_page": true,
      "children": [
        {
          "number": "（一）",
          "title": "开标一览表",
          "type": "table",
          "table_schema": {
            "note": "投标报价一览表",
            "columns": [
              {"name": "序号", "width_hint": "auto"},
              {"name": "服务内容", "width_hint": "auto"},
              {"name": "不含税单价（元）", "width_hint": "auto"},
              {"name": "税率（%）", "width_hint": "auto"},
              {"name": "含税总价（元）", "width_hint": "auto"},
              {"name": "备注", "width_hint": "auto"}
            ]
          },
          "footer_note": "投标人：（公章）\n法定代表人或授权代理人：（签字）\n日期：  年  月  日"
        },
        {
          "number": "（二）",
          "title": "投标函",
          "type": "fixed_form",
          "fixed_text_segments": [
            {"text": "致：{招标人名称}", "editable": false, "var": "tenderer_name"},
            {"text": "我方（投标人名称）已仔细阅读并充分理解...", "editable": true},
            {"text": "投标有效期：从投标截止日起120个日历天", "editable": false}
          ],
          "signature_block": {
            "lines": [
              "投标人：（公章）",
              "法定代表人或授权代理人：（签字）",
              "日期：  年  月  日"
            ]
          }
        },
        {
          "number": "（三）",
          "title": "法定代表人身份证明书",
          "type": "fixed_form",
          "children": []
        },
        {
          "number": "（四）",
          "title": "法定代表人授权委托书",
          "type": "fixed_form",
          "condition": "如由授权代理人签署则提供",
          "children": []
        },
        {
          "number": "（五）",
          "title": "投标保证金缴纳凭证及基本账户证明",
          "type": "attachment",
          "children": []
        }
      ]
    },
    {
      "number": "二",
      "title": "技术部分",
      "required": true,
      "start_new_page": true,
      "children": [
        {
          "number": "（一）",
          "title": "项目投入服务人员一览表",
          "type": "table",
          "table_schema": {...}
        }
      ]
    }
  ],
  "global_format_rules": {
    "numbering_style": "chinese_legal",
    "toc_heading_title": "目录",
    "cover_page_required": true,
    "cover_elements": ["项目名称", "投标文件", "投标人名称", "日期"],
    "signature_style": "seal_plus_signature",
    "page_number_format": "center_bottom",
    "attachment_labeling": "附件X："
  },
  "extraction_metadata": {
    "source": "招标文件正文.pdf — 投标文件格式章节（自动定位）",
    "extracted_at": "2026-07-29T...",
    "confidence_scores": {
      "document_structure": 0.95,
      "table_schemas": 0.88,
      "fixed_forms": 0.90
    },
    "warnings": ["投标函固定措辞中部分文字OCR不清晰，请人工核对"]
  }
}
```

**AI 提取 Prompt 设计要点**:

- 输入：由智能定位器提取的格式章节全文（通常 3000-8000 字符）
- 使用 `response_format: json_object` 强制结构化输出
- 要求提取：章节层级、序号格式、每张表的完整列定义、固定表单的可编辑/不可编辑段落、签章位置
- 输出 confidence_scores 标注每部分的提取可信度

**实现文件**: `backend/app/services/format_extractor.py`

**API 入口**: 在 `parse_bid_requirements()` 调用后同步调用，结果存入 `BidProject.format_template_json`

---

### 3.2 `outline_engine.py`（改造）— 大纲生成增强

**改造点**:

1. `generate_deep_outline()` 新增参数 `format_template: dict | None = None`
2. 当 `format_template` 存在时，大纲的顶层结构必须与 `document_structure` 完全一致：
   - 部分的序号和标题不得更改
   - AI 可以在每个部分内部扩展子节（第3-4级）
   - prompt 中明确告诉 AI："以下文档结构为招标文件规定，顶层不得增删改"
3. 在 prompt 中注入格式模板的结构描述文本，让 AI 在大纲中遵循

**Prompt 注入方式**:

```python
def _format_template_to_prompt_text(format_template: dict) -> str:
    """将格式模板转为可注入AI prompt的文本描述"""
    lines = ["【招标文件规定的标书格式 — 以下结构为强制要求，顶层不得更改】"]
    for part in format_template.get("document_structure", []):
        number = part.get("number", "")
        title = part.get("title", "")
        lines.append(f"\n{number}、{title}")
        for child in part.get("children", []):
            c_num = child.get("number", "")
            c_title = child.get("title", "")
            c_type = child.get("type", "")
            type_hint = ""
            if c_type == "table":
                cols = [c["name"] for c in child.get("table_schema", {}).get("columns", [])]
                type_hint = f"（表格，列：{'、'.join(cols)}）"
            elif c_type == "fixed_form":
                type_hint = "（固定格式表单）"
            lines.append(f"  {c_num} {c_title}{type_hint}")
    lines.append("\n以上顶层结构为招标文件硬性规定，生成大纲时这些标题和顺序必须原样保留。可在每章内部按需扩展子节。")
    return "\n".join(lines)
```

---

### 3.3 `ai_pipeline.py`（改造）— 内容生成增强

**改造点**:

1. **`parse_bid_requirements()` 扩展**：在解析完业务需求后，新增调用 `format_extractor.extract_format_template()` 提取格式模板

2. **`_get_section_guidance()` 增强**：当存在格式模板时，从模板中查找当前章节的格式约束，注入到生成 prompt：
   - 如果是表格类型 → 注入列定义
   - 如果是固定表单 → 注入固定文本模板
   - 注入签章要求

3. **SYSTEM_PROMPT 增强**：
   - 新增"格式强制规范"部分，从格式模板动态生成
   - 明确："以下格式要求是从招标文件第六章提取的硬性规定，必须严格遵守"
   - 序号格式要求：明确各层级使用什么序号（一、（一）、1.、（1）等）

4. **表格生成约束**：对表格类章节，prompt 中必须强制指定：
   ```
   【强制表格格式】
   本节的表格必须严格按照以下列定义生成：
   表头列（按顺序）：{列名列表}
   禁止增减列、禁止调换列顺序
   ```

5. **固定表单处理**：对于投标函、承诺书等固定格式，prompt 注入模板文本：
   ```
   【固定格式模板 — 以下加粗部分为招标文件规定的固定措辞，必须原样使用】
   致：{招标人名称}
   （可编辑段：在此处根据招标文件要求填写补充内容）
   投标有效期：从投标截止日起120个日历天
   ```

---

### 3.4 `format_verifier.py`（新增）— 格式校验器

**职责**: 生成完成后，对比生成内容与格式模板，报告偏差并自动修正

**校验维度**:

| 校验项 | 检查方式 | 可自动修正 | 不可自动修正时 |
|--------|---------|-----------|---------------|
| 章节完整性 | 检查 required 章节是否全部存在 | ✅ 插入缺失章节骨架 | 标记为待人工编写 |
| 章节顺序 | 检查章节排列是否与模板一致 | ✅ 自动重排 | — |
| 序号格式 | 正则匹配检查序号是否与模板一致 | ✅ 自动替换序号 | — |
| 表格列匹配 | 检查表格列数和列名是否匹配模板 | ⚠️ 列名可自动修正 | 列数不匹配标记 |
| 签章块存在 | 检查章节末尾是否有签章行 | ✅ 自动补充 | — |
| 固定措辞完整 | 检查固定措辞是否原样存在 | ✅ 自动替换 | — |
| 目录完整性 | 检查 TOC 是否包含所有章节 | ✅ 目录由 Word TOC 域自动生成 | — |

**校验报告输出格式**:

```json
{
  "overall_status": "pass_with_warnings",
  "checks": [
    {"item": "章节完整性", "status": "pass", "detail": "11/11 个必需章节全部存在"},
    {"item": "表格列匹配", "status": "warning", "detail": "开标一览表列名偏差：'单价'→应为'不含税单价（元）'，已自动修正"},
    {"item": "序号格式", "status": "pass", "detail": "全部序号格式与模板一致"}
  ],
  "auto_fixes_applied": 3,
  "manual_review_required": 0,
  "warnings": [...]
}
```

**校验时机**: 在 `generate_bid_with_deep_outline()` 的 Phase 4（组装）和 Phase 5（完成）之间执行。校验结果通过 SSE event 推送给前端。

**实现文件**: `backend/app/services/format_verifier.py`

---

### 3.5 `render_engine.py`（微调）— 渲染增强

**改造点**（较小）:

1. 支持 `bid_format_template` 中的全局格式规则覆盖 `DEFAULT_STYLE`：
   - 如果模板指定了特殊页边距，从模板读取
   - 封面元素的数量/内容由模板控制

2. 签章块的渲染：`[[signature_block]]` 标记 → 固定格式的落款表格

---

### 3.6 数据模型变更

**BidProject 模型新增字段** (`backend/app/models/project.py`):

```python
# 格式模板 — 从招标文件第六章提取的结构化格式定义
format_template_json = Column(Text, nullable=True, comment="格式模板JSON")

# 格式校验报告 — 最近一次的校验结果
format_verification_json = Column(Text, nullable=True, comment="格式校验报告JSON")
```

**API Schema 变更** (`backend/app/schemas/bid.py`):

```python
class FormatVerificationResult(BaseModel):
    overall_status: str  # "pass" | "pass_with_warnings" | "fail"
    checks: list[dict]
    auto_fixes_applied: int
    manual_review_required: int
    warnings: list[str]
```

---

## 4. 数据流

### 4.1 整体流程

```
POST /upload-and-parse
  │
  ├─ parse_document(text) → raw text
  ├─ parse_bid_requirements(text) → requirements dict
  └─ extract_format_template(text) → format_template dict  ← NEW
  │
  ▼ 存入 BidProject
  │  - parsed_requirements_json
  │  - format_template_json  ← NEW
  │
POST /generate
  │
  ├─ generate_deep_outline(requirements, format_template=ft)  ← 改造
  │   └─ prompt 注入 format_template 的结构描述
  │
  ├─ [for each leaf section]
  │   └─ generate_section(..., format_template=ft)  ← 改造
  │       └─ prompt 注入该节的格式约束
  │
  ├─ build_final_chapters_payload(tree, sections)
  │
  ├─ verify_format(chapters_payload, format_template) → report  ← NEW
  │   └─ 自动修正 + 生成校验报告
  │
  └─ SSE: done + verification_report

POST /export
  │
  └─ render_bid_to_docx(chapters, format_template=ft)  ← 改造
```

### 4.2 格式模板的传递路径

```
format_extractor.py 产出
        │
        ▼
BidProject.format_template_json (持久化)
        │
        ├──→ outline_engine.py (约束大纲结构)
        ├──→ ai_pipeline.py (约束内容生成)
        ├──→ format_verifier.py (校验依据)
        └──→ render_engine.py (全局格式规则)
```

---

## 5. 错误处理与降级策略

| 场景 | 处理方式 |
|------|---------|
| 格式章节未找到/定位失败 | 降级到当前无格式约束模式，记录 warning |
| AI 提取格式模板 JSON 解析失败 | 重试1次，仍失败则降级 |
| 格式模板中某部分 confidence < 0.7 | 该部分不做强制约束，标记"建议人工核对" |
| 格式校验自动修正失败 | 保留原文，在校验报告中标记，推送到前端让用户看到 |
| 用户上传的招标文件无明确第六章 | 格式提取返回空模板，系统等价于当前无约束模式 |

---

## 6. 实现计划（按优先级）

### Phase 1: 格式提取（核心基础）
1. 新建 `format_extractor.py`，实现 `extract_format_template()`
2. 扩展 `parse_bid_requirements()` 同步调用格式提取
3. BidProject 模型新增 `format_template_json` 字段 + 数据库迁移

### Phase 2: 大纲约束
4. 改造 `outline_engine.py`，接收并强制遵循格式模板结构
5. 实现 `_format_template_to_prompt_text()` 转换函数

### Phase 3: 内容约束
6. 改造 `ai_pipeline.py` 的 `_get_section_guidance()`，注入表格/表单格式约束
7. 改造 `SYSTEM_PROMPT`，新增格式强制规范段落
8. 改造 `subsection_generator.py`，传递格式约束到每个叶子节点

### Phase 4: 格式校验
9. 新建 `format_verifier.py`，实现全部校验逻辑
10. 插入到 `generate_bid_with_deep_outline()` 流水线中
11. 校验结果 SSE 推送到前端

### Phase 5: 渲染适配
12. `render_engine.py` 支持格式模板的全局规则
13. 签章块特殊渲染

---

## 7. 测试策略

### 7.1 单元测试

- `format_extractor.py`: 用多个招标文件测试格式章节定位准确性（不同章节号）和格式提取准确性
- `format_verifier.py`: 用故意制造格式错误的章节内容测试校验检出率
- `_format_template_to_prompt_text()`: 验证输出文本包含正确的结构描述

### 7.2 集成测试

- 端到端测试：上传招标文件 → 生成标书 → 校验通过
- 用历史招标文件做回归，确保已有功能不受影响

### 7.3 人工验收标准

- 生成的标书目录结构与招标文件第六章规定的章节完全一致
- 表格列名、列数与招标文件表格完全一致
- 投标函、承诺书等固定表单的措辞符合招标文件原文
- 格式校验报告无 `fail` 项

---

## 8. 与现有系统的关系

| 现有模块 | 变更程度 | 说明 |
|---------|---------|------|
| `format_extractor.py` | **新增** | 核心新模块 |
| `format_verifier.py` | **新增** | 校验模块 |
| `ai_pipeline.py` | 中等改造 | 扩展 parse + 增强 prompt |
| `outline_engine.py` | 中等改造 | 接受并强制遵循格式模板 |
| `subsection_generator.py` | 小幅改造 | 传递格式约束 |
| `content_assembler.py` | 不变 | 组装逻辑无需变更 |
| `render_engine.py` | 微调 | 支持全局格式规则 \\
| `bid.py` (API) | 小幅改造 | SSE 推流中增加校验事件 |
| `BidProject` 模型 | 新增2字段 | 需数据库迁移 |
