# 招标文件格式强制遵循系统 — 设计修正 v2

**日期**: 2026-07-29
**状态**: 设计阶段
**关联**: [[2026-07-29-bid-format-compliance-design]] (v1)

---

## 1. v1 的核心缺陷

v1 把所有章节都交给 AI 生成，但实际标书中：

| 类型 | 正确做法 | v1 做法 | 问题 |
|------|---------|--------|------|
| 文件类（投标函、承诺书等） | 照抄招标文件原文，只填空 | AI 自由生成 | 措辞偏离原文，废标 |
| 封面 | 照抄招标文件封面格式 | 系统固定模板 | 与招标文件不一致 |
| 目录标题 vs 正文标题 | 目录："一、投标函"（带序号靠左），正文："投标函"（无序号居中） | 统一用 "一、商务部分" | 格式不对 |
| 技术类（服务方案等） | AI 自主撰写 | AI 生成 | 这个是对的 |

---

## 2. 修正方案：双轨内容生成

```
招标文件 ─→ ①格式提取 ─→ bid_format_template JSON
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
   ②封面提取              ③文件类模板           ④技术类大纲
   (照抄招标文件          (提取原文+              (AI按格式
    封面文字布局)          标注填空变量)           约束生成)
         │                    │                    │
         ▼                    ▼                    ▼
   ⑤封面渲染              ⑥模板填充             ⑦AI生成
   (布局→docx)            (公司信息→替换)        (现有pipeline)
         │                    │                    │
         └────────────────────┼────────────────────┘
                              ▼
                    ⑧组装 + ⑨校验 → .docx
```

---

## 3. 章节分类

### 3.1 格式模板新增字段

每个章节节点新增 `generation_strategy` 字段：

```json
{
  "document_structure": [
    {
      "number": "一",
      "title": "投标函",
      "toc_title": "一、投标函",
      "body_heading": "投标函",
      "generation_strategy": "file_template",
      "children": []
    },
    {
      "number": "二",
      "title": "技术部分",
      "toc_title": "二、技术部分",
      "body_heading": "技术部分",
      "generation_strategy": "ai_generate",
      "children": [...]
    }
  ]
}
```

### 3.2 策略分类规则

| strategy | 判断依据 | 处理方式 |
|----------|---------|---------|
| `file_template` | 招标文件中该章节有完整的固定文本（投标函、各种承诺书、法定代表人证明、授权委托书、开标一览表等） | 提取原文模板→AI 标注变量→填充公司信息 |
| `ai_generate` | 招标文件中该章节只有标题或概要要求，无固定文本（服务方案、应急预案、培训方案等） | AI 按格式约束生成 |
| `cover_page` | 封面 | 提取原文布局→填充公司信息 |

---

## 4. 封面处理

### 4.1 封面提取

从招标文件格式章节（或招标文件第一页）中提取封面布局。不同招标文件封面格式不同，不能用固定模板。

**提取内容：**
- 封面各行的文字内容、顺序、字号（如果能识别）
- 各行是固定文字还是需填写
- 签章区域位置

**输出示例：**
```json
{
  "strategy": "cover_page",
  "lines": [
    {"text": "{招标项目名称}", "style": "title", "editable": true, "var": "project_name"},
    {"text": "投标文件", "style": "subtitle", "editable": false},
    {"text": "", "style": "spacer", "height": "fill"},
    {"text": "投标人：{公司名称}", "style": "body", "editable": true, "var": "company_name"},
    {"text": "法定代表人或其委托代理人：（签字）", "style": "body", "editable": true},
    {"text": "日期：{日期}", "style": "body", "editable": true, "var": "date"}
  ]
}
```

### 4.2 封面渲染

`render_engine.py` 根据封面模板渲染，不参与 TOC，不加页眉页脚，独占第一页。

---

## 5. 文件类章节处理

### 5.1 模板提取

AI 从招标文件原文中提取该章节的全部文字，标注：

- **固定措辞**（不可修改）：招标文件规定的原文
- **填空变量**（需替换）：公司名称、法人姓名、日期、投标有效期等

```json
{
  "section_number": "一",
  "toc_title": "一、投标函",
  "body_heading": "投标函",
  "strategy": "file_template",
  "template_segments": [
    {"type": "fixed", "text": "致："},
    {"type": "variable", "var": "tenderer_name", "text": "{招标人名称}"},
    {"type": "fixed", "text": "\n\n我方"},
    {"type": "variable", "var": "company_name", "text": "{投标人名称}"},
    {"type": "fixed", "text": "已仔细阅读并充分理解贵方招标文件的全部内容，包括所有补充、修改文件..."},
    {"type": "fixed", "text": "\n\n投标有效期：从投标截止日起"},
    {"type": "variable", "var": "bid_validity_days", "text": "120个日历天"},
    {"type": "fixed", "text": "\n\n投标人：（公章）\n法定代表人或授权代理人：（签字）\n日期：  年  月  日"}
  ],
  "variable_values": {
    "tenderer_name": "从招标文件自动提取",
    "company_name": "从公司资料自动填充",
    "bid_validity_days": "从招标文件自动提取或默认120"
  }
}
```

### 5.2 模板填充

`template_filler.py`（新增）：

1. 读取 `template_segments`
2. 对每个 `variable`，从变量源查询替换值
3. 变量源优先级：招标文件提取值 > 公司资料 > 用户输入 > 默认值 > "[待补充]"
4. 后处理兜底：全文搜索 `{xxx}` 格式的残留占位符，确保无遗漏

### 5.3 变量来源映射

| 变量 | 来源 |
|------|------|
| `company_name` | CompanyProfile.company_name |
| `legal_rep_name` | CompanyProfile.legal_rep_name |
| `business_license_number` | CompanyProfile.business_license_number |
| `address` | CompanyProfile.address |
| `contact_phone` | CompanyProfile.contact_phone |
| `tenderer_name` | requirements.project_name 或招标公告提取 |
| `project_name` | requirements.project_name |
| `bid_validity_days` | 招标文件提取，默认 120 |
| `date` | 当前日期 |

---

## 6. 目录与正文标题分离

### 6.1 当前问题

v1 中标题统一处理，目录条目 = 正文标题。但招标文件要求：
- 目录：「一、投标函」— 带序号，靠左，出现在 TOC 中
- 正文：「投标函」— 无序号，居中，**不出现在 TOC 中**

### 6.2 实现方案

格式模板中每个章节定义两个标题字段：
- `toc_title`: 目录中显示的标题（如"一、投标函"），渲染为一级目录项
- `body_heading`: 正文页面中的大标题（如"投标函"），居中、不入目录

`render_engine.py` 渲染时：
1. 目录页使用 `toc_title` 作为目录项
2. 正文页面使用 `body_heading` 作为该页大标题
3. `body_heading` 使用 Word Heading 样式但不纳入 TOC 域（或用自定义样式）

技术实现：正文标题使用 `WD_ALIGN_PARAGRAPH.CENTER` + **不加** Heading 样式（用大号黑体 + 居中模拟），这样 Word TOC 域不会把它收录进去。

---

## 7. 技术类章节处理（与 v1 一致）

`generation_strategy: "ai_generate"` 的章节由 AI 生成，但格式约束比 v1 更强：

1. 章节标题格式严格按模板（`toc_title` / `body_heading`）
2. 内部表格列定义从招标文件提取
3. 序号格式（一、（一）、1.）与招标文件一致

---

## 8. 模块变更总结

| 模块 | 变更 | 说明 |
|------|------|------|
| `format_extractor.py` | **改造** | 扩展提取：区分章节策略类型、提取文件类原文模板、标注变量 |
| `template_filler.py` | **新增** | 文件类模板填充引擎，变量替换 + 后处理兜底 |
| `ai_pipeline.py` | **改造** | 生成分叉：`file_template` 走填充流程，`ai_generate` 走 AI 流程 |
| `ai_pipeline.py` SYSTEM_PROMPT | **改造** | 技术类明确被告知"只写技术内容，不涉及文件类"，避免越界 |
| `outline_engine.py` | **改造** | 大纲节点新增 `toc_title` / `body_heading` / `generation_strategy` |
| `render_engine.py` | **改造** | 封面从模板渲染；目录/正文标题分离；body_heading 不入 TOC |
| `format_verifier.py` | **改造** | 文件类校验：检查变量是否完整填充、固定措辞是否原样保留 |
| `content_assembler.py` | **微调** | 支持 `toc_title` / `body_heading` 分离 |

---

## 9. 与 v1 的关系

v1 的基础设施复用：
- ✅ 智能格式章节定位（四级策略）
- ✅ AI 结构化提取（`response_format: json_object`）
- ✅ 格式校验框架
- ✅ format_template 数据流
- ✅ BidProject.format_template_json / format_verification_json

v2 新增/修改：
- ➕ 章节策略分类（file_template / ai_generate / cover_page）
- ➕ 文件类原文模板提取 + 变量标注
- ➕ template_filler（变量替换引擎）
- 🔧 封面从模板渲染
- 🔧 目录/正文标题分离
- 🔧 AI prompt 限制技术类不越界写文件类内容

---

## 10. 实现优先级

**Phase A（核心修正）：**
1. AI 提取增强：识别章节策略 + 标注变量
2. template_filler 模板填充引擎
3. 封面模板提取 + 渲染
4. 目录/正文标题分离

**Phase B（质量加固）：**
5. 文件类校验（变量填充完整性 + 固定措辞原文保留）
6. AI prompt 约束（技术类不写文件类内容）
7. 集成测试

## 11. 新增/变更的数据结构

### BidProject 模型（不变）

已有的 `format_template_json` 字段容纳 v2 扩展格式，无需新增字段。

### bid_format_template v2 完整示例

```json
{
  "document_type": "bid_response",
  "generation_strategy_overrides": {
    "auto_classify": true,
    "rules": "招标文件中含完整固定文本的章节 → file_template；仅有标题或概要的 → ai_generate"
  },
  "cover_page": {
    "strategy": "cover_page",
    "lines": [
      {"text": "{project_name}", "style": "title", "var": "project_name"},
      {"text": "投标文件", "style": "subtitle"},
      {"text": "", "style": "spacer_fill"},
      {"text": "投标人：{company_name}", "style": "info", "var": "company_name"},
      {"text": "法定代表人或其委托代理人：（签字）", "style": "info"},
      {"text": "日期：{date}", "style": "info", "var": "date"}
    ]
  },
  "document_structure": [
    {
      "number": "一",
      "title": "投标函",
      "toc_title": "一、投标函",
      "body_heading": "投标函",
      "generation_strategy": "file_template",
      "template_text": "致：{tenderer_name}\n\n我方{company_name}已仔细阅读...\n\n投标有效期：从投标截止日起{bid_validity_days}个日历天\n\n投标人：（公章）\n日期：  年  月  日",
      "template_segments": [
        {"type": "fixed", "text": "致："},
        {"type": "variable", "var": "tenderer_name"},
        {"type": "fixed", "text": "\n\n我方"},
        {"type": "variable", "var": "company_name"},
        {"type": "fixed", "text": "已仔细阅读并充分理解贵方招标文件的全部内容..."},
        {"type": "fixed", "text": "\n\n投标有效期：从投标截止日起"},
        {"type": "variable", "var": "bid_validity_days"},
        {"type": "fixed", "text": "个日历天"},
        {"type": "fixed", "text": "\n\n投标人：（公章）\n法定代表人或授权代理人：（签字）\n日期：  年  月  日"}
      ]
    },
    {
      "number": "二",
      "title": "技术部分",
      "toc_title": "二、技术部分",
      "body_heading": "技术部分",
      "generation_strategy": "ai_generate",
      "children": [
        {
          "number": "（一）",
          "title": "项目整体服务方案",
          "toc_title": "（一）项目整体服务方案",
          "body_heading": "项目整体服务方案",
          "generation_strategy": "ai_generate",
          "children": []
        }
      ]
    }
  ],
  "global_format_rules": {
    "numbering_style": "chinese_legal",
    "toc_style": "numbered_left_aligned",
    "body_heading_style": "unnumbered_centered",
    "toc_depth": 1
  }
}
```
