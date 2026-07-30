# 招标文件格式强制遵循 v2 — 实现计划（精简版）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** 完整提取招标文件格式章节 → AI 标注填空位置 → 批量替换 → 技术章节 AI 生成 → 渲染 DOCX

**Architecture:** 一次完整提取（pdfplumber），一次 AI 扫描（标注所有变量位置），一次批量填充（全文替换），然后 AI 生成技术内容插入对应标题下。不再拆分章节单独处理。

**Tech Stack:** Python 3.11+, pdfplumber, python-docx, 复用 ai_adapter

---

## Global Constraints

- 格式章节全文一字不改，只替换标记的变量位置
- 封面、目录、表格结构全部原样保留
- 技术类章节（服务方案等）AI 生成后插入对应标题下
- 变量替换必须兜底扫描，确保无 `{xxx}` 残留
- 所有操作基于 pdfplumber 提取的纯文本 + 表格数据

---

## 核心流程

```
招标文件 PDF
      │
      ▼
pdfplumber 完整提取第六章（格式章节）
  ├── 纯文本（含封面、目录、正文、落款）
  └── 表格数据（开标一览表、投标报价一览表、投标人基本资料等）
      │
      ▼
AI 扫描标注
  ├── 识别所有填写位置 → 标注变量名
  └── 输出替换清单：{位置: 变量名}
      │
      ▼
批量填充
  ├── 文本替换：全文 {company_name} → 云南领航保安服务有限公司
  ├── 表格填充：对应格子填入公司数据
  └── 后处理兜底扫描
      │
      ▼
AI 生成技术章节（服务方案、应急预案等）
      │
      ▼
组装 → render_engine → .docx
```

---

## File Structure

```
backend/app/services/
├── pdf_extractor.py        [NEW]    pdfplumber 完整提取格式章节（文本 + 表格）
├── template_filler.py      [NEW]    变量替换引擎（文本 + 表格）
├── ai_pipeline.py           [MODIFY] 生成分叉：file→fill / tech→AI
├── render_engine.py         [MODIFY] 封面、目录、正文标题渲染适配

backend/app/api/
└── bid.py                   [MODIFY] 调用新流程
```

---

### Task 1: pdf_extractor — 完整提取格式章节

**Files:**
- Create: `backend/app/services/pdf_extractor.py`
- Create: `backend/tests/test_pdf_extractor.py`

**Interfaces:**
- Produces: `def extract_format_section(pdf_path: str) -> dict` — 返回 `{"full_text": str, "tables": list[dict], "pages": list[int]}`
- Produces: `def extract_tables_from_pages(pdf, start_page: int, end_page: int) -> list[dict]` — 提取表格
- Produces: `def locate_format_pages(pdf) -> tuple[int, int] | None` — 定位格式章节起止页

- [ ] **Step 1: 创建 pdf_extractor.py**

```python
"""宏曦标书 - PDF格式章节完整提取器.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pdfplumber

logger = logging.getLogger(__name__)

# 格式章节定位关键词（按优先级）
FORMAT_KEYWORDS = [
    "投标文件格式",
    "投标书格式",
    "投标文件组成",
]


def locate_format_pages(pdf) -> Tuple[int, int] | None:
    """定位招标文件中'投标文件格式'章节的起止页码.

    Returns (start_page, end_page) 0-indexed, or None.
    start_page 是章节首页（如"第六章 投标文件格式"所在页）。
    end_page 是文档末尾（格式章节通常包含到文档结束）。
    """
    for i, page in enumerate(pdf.pages):
        text = page.extract_text() or ""
        for kw in FORMAT_KEYWORDS:
            if kw in text:
                # 找到了——从这一页的章节标题开始
                # 格式章节通常到文档结束，或到"第七章"等下一章
                end_page = len(pdf.pages) - 1
                # 向后搜索下一章边界
                for j in range(i + 1, min(i + 3, len(pdf.pages))):
                    # 不做复杂判断，默认到文末
                    pass
                logger.info(
                    "Format section: pages %d-%d (keyword: '%s')",
                    i, end_page, kw,
                )
                return i, end_page
    return None


def extract_text_from_pages(pdf, start: int, end: int) -> str:
    """提取指定页码范围的所有文本."""
    parts = []
    for i in range(start, end + 1):
        text = pdf.pages[i].extract_text()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def extract_tables_from_pages(pdf, start: int, end: int) -> list[dict]:
    """提取指定页码范围的所有表格.

    Returns list of {"page": int, "table_index": int, "rows": list[list[str]]}
    """
    tables = []
    for i in range(start, end + 1):
        page_tables = pdf.pages[i].extract_tables()
        for j, rows in enumerate(page_tables):
            if rows and len(rows) >= 2:  # 过滤空表和单行"表"
                tables.append({
                    "page": i + 1,  # 1-indexed
                    "table_index": j,
                    "rows": rows,
                })
    return tables


def extract_format_section(pdf_path: str) -> dict:
    """完整提取招标文件格式章节.

    Args:
        pdf_path: PDF 文件路径

    Returns:
        {"full_text": str, "tables": list[dict], "pages": [int, int]}
        full_text 包含封面、目录、正文所有文字
        tables 包含所有表格数据
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pdf = pdfplumber.open(str(path))

    try:
        location = locate_format_pages(pdf)
        if not location:
            logger.warning("Format section not found, using full document")
            start, end = 0, len(pdf.pages) - 1
        else:
            start, end = location

        full_text = extract_text_from_pages(pdf, start, end)
        tables = extract_tables_from_pages(pdf, start, end)

        logger.info(
            "Extracted format section: %d chars text, %d tables from %d pages",
            len(full_text), len(tables), end - start + 1,
        )

        return {
            "full_text": full_text,
            "tables": tables,
            "start_page": start + 1,
            "end_page": end + 1,
        }
    finally:
        pdf.close()
```

- [ ] **Step 2: 编写测试**

```python
# backend/tests/test_pdf_extractor.py
import os
from app.services.pdf_extractor import extract_format_section, locate_format_pages

TENDER_PDF = os.path.join(os.path.dirname(__file__), "../..", "素材", "招标文件正文.pdf")


def test_extract_format_section():
    """完整提取招标文件格式章节."""
    if not os.path.exists(TENDER_PDF):
        return  # CI 环境可能无此文件
    result = extract_format_section(TENDER_PDF)
    assert result["full_text"]
    assert len(result["full_text"]) > 500
    assert "投标文件格式" in result["full_text"] or "投标函" in result["full_text"]
    print(f"Text: {len(result['full_text'])} chars, Tables: {len(result['tables'])}")


def test_tables_extracted():
    """表格正确提取."""
    if not os.path.exists(TENDER_PDF):
        return
    import pdfplumber
    pdf = pdfplumber.open(TENDER_PDF)
    start, end = locate_format_pages(pdf)
    from app.services.pdf_extractor import extract_tables_from_pages
    tables = extract_tables_from_pages(pdf, start, end)
    pdf.close()
    # 至少应有开标一览表、投标报价一览表、投标人基本资料等
    assert len(tables) >= 2
    print(f"Found {len(tables)} tables")
```

- [ ] **Step 3: 测试 + 提交**

```bash
cd backend && python -m pytest tests/test_pdf_extractor.py -v
git add backend/app/services/pdf_extractor.py backend/tests/test_pdf_extractor.py
git commit -m "feat: pdf_extractor — 完整提取格式章节（文本+表格）"
```

---

### Task 2: template_filler — 变量标注 + 批量填充

**Files:**
- Create: `backend/app/services/template_filler.py`
- Create: `backend/tests/test_template_filler.py`

**Interfaces:**
- Produces: `async def scan_and_mark_variables(full_text: str, tables: list[dict], ai_adapter) -> dict` — AI 扫描标注所有填写位置
- Produces: `def build_variable_values(company_profile: dict, requirements: dict) -> dict` — 构建变量值表
- Produces: `def batch_fill_text(text: str, variables: dict) -> str` — 批量文本替换
- Produces: `def batch_fill_tables(tables: list[dict], variables: dict) -> list[dict]` — 批量表格填充
- Produces: `def post_scan(text: str) -> list[str]` — 后处理兜底扫描

- [ ] **Step 1: AI 扫描标注**

```python
"""宏曦标书 - 模板填充引擎.

完整提取的格式章节全文 → AI 标注变量位置 → 批量替换 → 生成填充后的文档内容.
"""
import re
import json
import logging
from datetime import date
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SCAN_SYSTEM_PROMPT = """你是投标文件分析专家。招标文件的格式章节已完整提取。
你的任务是扫描全文，找出所有需要投标人**填写**的位置，标注变量名。

变量名只能从以下列表选取：
- company_name: 投标人公司名称
- legal_rep_name: 法定代表人姓名
- business_license_number: 统一社会信用代码/营业执照号
- address: 公司地址
- contact_phone: 联系电话
- website: 公司网站
- contact_person: 联系人
- fax: 传真
- zip_code: 邮编
- registered_capital: 注册资金
- account_number: 开户银行账号
- bank_name: 开户银行
- project_name: 招标项目名称
- tenderer_name: 招标人名称
- date: 日期
- bid_validity_days: 投标有效期天数

如果你不确定某个位置该对应哪个变量，用 unknown_1, unknown_2 等标记，并在 warnings 中说明。

表格中每个空单元格如果已有标签行标明该填什么，标注对应的变量名。

返回 JSON:
{
  "text_replacements": [
    {"original": "________", "var": "company_name", "context_before": "投标人名称："},
    {"original": "投标人名称：", "var": null, "note": "这是标签，不替换"},
  ],
  "table_fills": [
    {"page": 68, "table_index": 1, "row": 0, "col": 1, "var": "company_name"}
  ],
  "warnings": ["第X页'xxx'处不确定对应哪个变量"]
}
"""


async def scan_and_mark_variables(
    full_text: str,
    tables: list[dict],
    ai_adapter,
) -> dict:
    """AI 扫描全文，标注所有变量位置."""
    tables_json = json.dumps(tables[:10], ensure_ascii=False)  # 限制表格数量
    prompt = f"""请扫描以下招标文件格式章节，找出所有需要投标人填写的位置。

全文（含封面、目录、正文）：
---
{full_text[:15000]}
---

表格数据：
---
{tables_json}
---

请标注每个填写位置对应的变量名。直接返回JSON，不要其他文字。"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": SCAN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("Variable scanning failed: %s", exc)
        return {
            "text_replacements": [],
            "table_fills": [],
            "warnings": [f"AI扫描失败: {exc}"],
        }

    return result


def build_variable_values(
    company_profile: dict | None = None,
    requirements: dict | None = None,
) -> dict:
    """构建变量值映射."""
    company = company_profile or {}
    reqs = requirements or {}

    return {
        "company_name": company.get("company_name") or "[待补充]",
        "legal_rep_name": company.get("legal_rep_name") or "[待补充]",
        "business_license_number": company.get("business_license_number") or "[待补充]",
        "address": company.get("address") or "[待补充]",
        "contact_phone": company.get("contact_phone") or "[待补充]",
        "website": company.get("website") or "",
        "contact_person": company.get("contact_person") or "[待补充]",
        "fax": company.get("fax") or "",
        "zip_code": company.get("zip_code") or "",
        "registered_capital": company.get("registered_capital") or "",
        "account_number": company.get("account_number") or "",
        "bank_name": company.get("bank_name") or "",
        "project_name": reqs.get("project_name") or "[待补充]",
        "tenderer_name": reqs.get("project_name") or "[待补充]",
        "date": date.today().strftime("%Y年%m月%d日"),
        "bid_validity_days": "120",
    }


def batch_fill_text(text: str, text_replacements: list[dict]) -> str:
    """批量文本替换：将原文中的空白/占位符替换为实际值.

    按 replacement 的长度降序排列，避免短串先替换破坏长串。
    """
    # 按 original 长度降序
    sorted_reps = sorted(
        text_replacements,
        key=lambda r: len(r.get("original", "")),
        reverse=True,
    )

    result = text
    for rep in sorted_reps:
        var = rep.get("var")
        original = rep.get("original", "")
        if var and original:
            value = rep.get("value", f"[{var}]")
            result = result.replace(original, value, 1)  # 逐个替换，避免错误匹配

    return result


def batch_fill_tables(tables: list[dict], table_fills: list[dict], variables: dict) -> list[dict]:
    """批量表格填充：在指定位置填入变量值."""
    result = [{"page": t["page"], "table_index": t["table_index"], "rows": [list(row) for row in t["rows"]]} for t in tables]

    for fill in table_fills:
        page = fill.get("page")
        ti = fill.get("table_index")
        row = fill.get("row")
        col = fill.get("col")
        var = fill.get("var", "")
        value = variables.get(var, f"[{var}]")

        for t in result:
            if t["page"] == page and t["table_index"] == ti:
                if row < len(t["rows"]) and col < len(t["rows"][row]):
                    t["rows"][row][col] = value

    return result


def post_scan(text: str) -> list[str]:
    """后处理兜底：扫描残留的空白/占位符."""
    issues = []
    # 扫描残留的空白下划线
    blanks = re.findall(r'_{3,}', text)
    if blanks:
        issues.append(f"残留空白下划线: {len(blanks)}处")
    # 扫描残留的占位符
    placeholders = re.findall(r'\{(\w+)\}', text)
    if placeholders:
        issues.append(f"残留占位符: {placeholders}")
    # 扫描未填的空白行
    empty_lines = re.findall(r'(?<=\n)\s{10,}(?=\n)', text)
    if empty_lines:
        issues.append(f"疑似未填充的空白行: {len(empty_lines)}处")
    return issues
```

- [ ] **Step 2: 编写测试**

```python
# backend/tests/test_template_filler.py
import pytest
from app.services.template_filler import (
    build_variable_values,
    batch_fill_text,
    batch_fill_tables,
    post_scan,
)

MOCK_COMPANY = {
    "company_name": "云南领航保安服务有限公司",
    "legal_rep_name": "张三",
    "business_license_number": "91530000MA6N2XXX00",
    "address": "云南省昆明市官渡区XX路XX号",
    "contact_phone": "0871-12345678",
}

MOCK_REQS = {"project_name": "某单位保安服务采购项目"}


def test_build_variable_values():
    values = build_variable_values(MOCK_COMPANY, MOCK_REQS)
    assert values["company_name"] == "云南领航保安服务有限公司"
    assert values["project_name"] == "某单位保安服务采购项目"
    assert "年" in values["date"]
    assert values["unknown_field"] == "[待补充]"  # unknown fields


def test_batch_fill_text():
    text = "投标人名称：________________\n日期：____年__月__日"
    replacements = [
        {"original": "________________", "var": "company_name", "value": "云南领航保安服务有限公司"},
        {"original": "____年__月__日", "var": "date", "value": "2026年07月29日"},
    ]
    result = batch_fill_text(text, replacements)
    assert "云南领航保安服务有限公司" in result
    assert "2026年07月29日" in result
    assert "________________" not in result


def test_batch_fill_text_longer_first():
    """更长的字符串应先替换，避免短串破坏长串."""
    text = "投标人：__________ 法定代表人：__________"
    replacements = [
        {"original": "__________", "var": "legal_rep_name", "value": "李四"},
        {"original": "__________", "var": "company_name", "value": "测试公司"},
    ]
    result = batch_fill_text(text, replacements)
    assert "测试公司" in result


def test_post_scan_clean():
    text = "投标人名称：云南领航保安服务有限公司"
    issues = post_scan(text)
    assert len(issues) == 0


def test_post_scan_found_blanks():
    text = "投标人名称：________________"
    issues = post_scan(text)
    assert len(issues) > 0


def test_batch_fill_tables():
    tables = [
        {"page": 68, "table_index": 1, "rows": [["投标人名称", ""], ["注册地址", ""]]}
    ]
    fills = [
        {"page": 68, "table_index": 1, "row": 0, "col": 1, "var": "company_name"},
        {"page": 68, "table_index": 1, "row": 1, "col": 1, "var": "address"},
    ]
    variables = build_variable_values(MOCK_COMPANY, MOCK_REQS)
    result = batch_fill_tables(tables, fills, variables)
    assert result[0]["rows"][0][1] == "云南领航保安服务有限公司"
    assert result[0]["rows"][1][1] == "云南省昆明市官渡区XX路XX号"
```

- [ ] **Step 3: 测试 + 提交**

```bash
cd backend && python -m pytest tests/test_template_filler.py -v
git add backend/app/services/template_filler.py backend/tests/test_template_filler.py
git commit -m "feat: template_filler — AI扫描标注变量 + 批量文本/表格填充"
```

---

### Task 3: ai_pipeline + render_engine 改造

**Files:**
- Modify: `backend/app/services/ai_pipeline.py`
- Modify: `backend/app/services/render_engine.py`
- Modify: `backend/app/api/bid.py`

**Interfaces:**
- `generate_bid_with_deep_outline()` 新增格式模板内容注入逻辑
- `render_bid_to_docx()` 接收填充后的完整文本
- `bid.py` 端点调用 pdf_extractor + template_filler

- [ ] **Step 1: ai_pipeline 增加格式模板生成流程**

在 `generate_bid_with_deep_outline()` 中添加 Phase 2.5（格式模板填充），在 AI 生成技术章节之前，先填充文件类章节：

```python
# Phase 2.5: 文件类章节填充
# 从 pdf_extractor 提取的格式章节文本 → AI标注 → 填充 → 作为技术章节生成的上下文
file_section_content = ""
if project_id and db:
    # 读取格式模板
    try:
        from app.models.project import BidProject
        result = await db.execute(
            sa_select(BidProject).where(BidProject.id == project_id)
        )
        db_proj = result.scalar_one_or_none()
        if db_proj and db_proj.parsed_requirements_json:
            reqs = json.loads(db_proj.parsed_requirements_json)
            # 如果requirements中有format_section_text（从pdf_extractor提取的全文）
            format_text = reqs.get("format_section_text", "")
            if format_text:
                # AI 扫描标注变量
                from app.services.template_filler import scan_and_mark_variables, build_variable_values, batch_fill_text
                scan_result = await scan_and_mark_variables(
                    format_text, reqs.get("format_tables", []), ai_adapter,
                )
                variables = build_variable_values(company_profile, requirements)
                # 为每个 replacement 添加上 value
                for rep in scan_result.get("text_replacements", []):
                    var_name = rep.get("var", "")
                    if var_name:
                        rep["value"] = variables.get(var_name, f"[{var_name}]")
                file_section_content = batch_fill_text(
                    format_text, scan_result.get("text_replacements", []),
                )
    except Exception as exc:
        logger.warning("File section filling failed: %s", exc)
```

- [ ] **Step 2: 技术章节生成时注入格式上下文**

AI 生成技术章节时，prop中说明文件类内容已填充完毕：
```python
# 在技术章节的 generation guidance 中
if file_section_content:
    guidance += "\n注意：投标文件中的投标函、承诺书、法定代表人证明等文件类内容已由系统自动填充，你只需撰写技术方案部分。"
```

- [ ] **Step 3: render_engine 适配**

在 `render_bid_to_docx()` 中，对文件类章节的内容直接渲染（不做 markdown 转义），对技术类章节保持现有 markdown → docx 渲染。

- [ ] **Step 4: bid.py 端点调用**

`/upload-and-parse` 端点增加 pdf_extractor 调用，提取格式章节全文存入 requirements JSON：
```python
# 上传后
from app.services.pdf_extractor import extract_format_section
try:
    format_section = extract_format_section(str(saved_path))
    requirements["format_section_text"] = format_section["full_text"]
    requirements["format_tables"] = format_section["tables"]
    requirements["format_pages"] = [format_section["start_page"], format_section["end_page"]]
except Exception as e:
    logger.warning("Format section extraction failed: %s", e)
```

- [ ] **Step 5: 全量测试 + 提交**

```bash
cd backend && python -m pytest tests/ -v
git add backend/app/services/ai_pipeline.py backend/app/services/render_engine.py backend/app/api/bid.py
git commit -m "feat: 格式模板填充流程集成 — pdf_extractor + AI标注 + 批量填充 + 技术章节生成"
```

---

### Task 4: 端到端集成测试 + 部署

**Files:**
- Create: `backend/tests/test_v2_integration.py`

- [ ] **Step 1: 端到端测试**

```python
# 用真实招标文件测试完整流程
# pdf_extractor → AI扫描 → 填充 → 生成技术内容
```

- [ ] **Step 2: 全量测试**

```bash
cd backend && python -m pytest tests/ -v
```

- [ ] **Step 3: 部署**

更新 quick_deploy.py 部署清单，服务器迁移 + 重启。

---

## 依赖关系

```
Task 1 (pdf_extractor) ──┐
                          ├──→ Task 3 (pipeline + render + api 集成)
Task 2 (template_filler) ─┘
                                    │
                                    ▼
                            Task 4 (集成测试 + 部署)
```

Task 1 + 2 可并行。Task 3 依赖 1+2。Task 4 依赖全部。

共 **4 个 Task**，比 v1 的 10 个大幅精简。
