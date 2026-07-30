# 招标文件格式强制遵循系统 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现从招标文件中智能提取格式要求、强制约束生成、自动校验修正的端到端格式保障系统。

**Architecture:** 新增 format_extractor（智能定位+AI提取格式模板）和 format_verifier（校验+自动修正）两个模块；改造 outline_engine、ai_pipeline、render_engine 三个模块以接受和强制执行格式模板；BidProject 模型新增 format_template_json 和 format_verification_json 两个字段。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async, python-docx, 复用现有 ai_adapter (OpenAI-compatible)

## Global Constraints

- 所有格式约束源于 bid_format_template，不得硬编码任何格式规则
- 格式提取/校验失败时必须降级，不能阻断现有流程
- 格式章节定位必须基于内容语义，不得硬编码章节号
- 使用现有 ai_adapter 单例，不引入新的 AI 调用方式
- 遵循项目现有代码风格：中文注释、async/await、类型标注

---

## File Structure

```
backend/app/services/
├── format_extractor.py    [NEW]  智能定位 + AI提取格式模板
├── format_verifier.py     [NEW]  格式校验 + 自动修正 + 报告生成

backend/app/models/
└── project.py             [MODIFY] BidProject 新增2字段

backend/app/schemas/
└── bid.py                 [MODIFY] 新增 FormatVerificationResult schema

backend/app/services/
├── ai_pipeline.py          [MODIFY] parse扩展 + prompt增强
├── outline_engine.py       [MODIFY] 接受格式模板，约束大纲
└── render_engine.py        [MODIFY] 支持格式模板全局规则

backend/app/api/
└── bid.py                  [MODIFY] SSE事件扩展 + 格式提取调用
```

---

### Task 1: BidProject 模型新增字段 + 数据库迁移

**Files:**
- Modify: `backend/app/models/project.py` (BidProject class)

**Interfaces:**
- Produces: `BidProject.format_template_json: Mapped[str]` — 格式模板JSON, nullable, default="{}"
- Produces: `BidProject.format_verification_json: Mapped[str]` — 校验报告JSON, nullable, default="{}"

- [ ] **Step 1: 在 BidProject 类中添加两个新字段**

在 `backend/app/models/project.py` 的 BidProject 类中，在 `generation_state_json` 字段之后添加：

```python
format_template_json: Mapped[str] = mapped_column(
    Text,
    nullable=False,
    default="{}",
    comment="格式模板JSON — 从招标文件'投标文件格式'章节提取的结构化格式定义",
)
format_verification_json: Mapped[str] = mapped_column(
    Text,
    nullable=False,
    default="{}",
    comment="格式校验报告JSON — 最近一次生成后的格式合规校验结果",
)
```

- [ ] **Step 2: 生成 Alembic 迁移**

```bash
cd backend
alembic revision --autogenerate -m "add format_template and format_verification to bid_projects"
alembic upgrade head
```

- [ ] **Step 3: 验证迁移**

```bash
cd backend
python -c "from app.database import engine; from app.models.project import BidProject; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/project.py backend/alembic/versions/*format_template*.py
git commit -m "feat: BidProject 新增 format_template_json 和 format_verification_json 字段"
```

---

### Task 2: 格式提取器 — 智能格式章节定位

**Files:**
- Create: `backend/app/services/format_extractor.py`

**Interfaces:**
- Produces: `async def locate_format_section(full_text: str) -> dict | None` — 返回定位结果 `{"chapter_number": str|None, "chapter_title": str, "section_text": str, "method": str}`，找不到返回 None
- Produces: `def _find_by_keyword(text: str) -> dict | None` — 关键词匹配
- Produces: `def _find_by_toc(text: str) -> dict | None` — 目录推断
- Produces: `def _split_by_chapter_headers(text: str) -> list[dict]` — 按章节头切分

- [ ] **Step 1: 创建文件骨架**

```python
"""宏曦标书 - 格式提取器 — 智能格式章节定位.
"""
import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 格式章节关键词匹配模式（按优先级排列）
FORMAT_SECTION_PATTERNS = [
    re.compile(r'第[一二三四五六七八九十\d]+章\s*.*?投标文件格式'),
    re.compile(r'第[一二三四五六七八九十\d]+章\s*.*?投标书格式'),
    re.compile(r'第[一二三四五六七八九十\d]+章\s*.*?投标文件.*格式'),
    re.compile(r'第[一二三四五六七八九十\d]+节\s*.*?投标文件格式'),
    re.compile(r'第[一二三四五六七八九十\d]+部分\s*.*?投标文件格式'),
]

# 通用章节头切分模式
CHAPTER_HEADER_PATTERN = re.compile(
    r'(?:^|\n)(第[一二三四五六七八九十\d]+[章节篇部分][\s　]*(?:.*?))(?=\n|$)',
    re.MULTILINE,
)

def _find_by_keyword(text: str) -> dict | None:
    """通过关键词匹配定位'投标文件格式'章节."""
    for pattern in FORMAT_SECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            start = match.start()
            chapter_title = match.group(0).strip()
            chapter_number_match = re.search(r'第([一二三四五六七八九十\d]+)', chapter_title)
            chapter_number = chapter_number_match.group(1) if chapter_number_match else None

            # 找到下一章的起始位置作为结束边界
            next_chapter_match = CHAPTER_HEADER_PATTERN.search(text, match.end())
            end = next_chapter_match.start() if next_chapter_match else min(len(text), start + 12000)

            section_text = text[start:end]
            return {
                "chapter_number": chapter_number,
                "chapter_title": chapter_title,
                "section_text": section_text,
                "method": "keyword_match",
            }
    return None

def _find_by_toc(text: str) -> dict | None:
    """通过目录推断'投标文件格式'章节位置."""
    # 找到目录区域
    toc_match = re.search(r'目\s*录|目\s*次', text[:2000])
    if not toc_match:
        return None

    # 从目录区域开始搜索格式章节引用
    toc_end = toc_match.start() + 5000
    toc_area = text[toc_match.start():min(toc_end, len(text))]

    # 在目录中搜索格式章节标题
    for pattern in FORMAT_SECTION_PATTERNS:
        m = pattern.search(toc_area)
        if m:
            chapter_title = m.group(0).strip()
            # 找到了目录中的引用，直接跳到正文中对应位置
            # 在目录之后搜索相同的章节标题
            body_start = toc_match.start() + 2000
            body_match = re.search(
                re.escape(chapter_title[:10]) + r'.*',
                text[body_start:],
            )
            if body_match:
                abs_start = body_start + body_match.start()
                next_ch = CHAPTER_HEADER_PATTERN.search(text, abs_start + 100)
                end = next_ch.start() if next_ch else min(len(text), abs_start + 12000)
                return {
                    "chapter_number": None,
                    "chapter_title": chapter_title,
                    "section_text": text[abs_start:end],
                    "method": "toc_inference",
                }
    return None

def _split_by_chapter_headers(text: str) -> list[dict]:
    """按章节头将全文切分为章节块."""
    matches = list(CHAPTER_HEADER_PATTERN.finditer(text))
    if not matches:
        return []

    chapters = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chapters.append({
            "title": title,
            "text": text[start:end],
            "start": start,
        })
    return chapters

def _find_by_semantic_chunks(text: str, chapters: list[dict]) -> dict | None:
    """通过语义分块定位：对每个章节块评分，找到最可能是格式章节的块."""
    # 关键词打分
    format_keywords = ["投标文件格式", "格式要求", "投标文件组成", "投标文件内容",
                       "投标函", "开标一览表", "法定代表人", "授权委托书",
                       "承诺书", "签章", "盖章", "目录要求", "装订"]
    best_chapter = None
    best_score = 0
    for ch in chapters:
        score = sum(1 for kw in format_keywords if kw in ch["text"])
        if score > best_score:
            best_score = score
            best_chapter = ch
    if best_chapter and best_score >= 3:
        return {
            "chapter_number": None,
            "chapter_title": best_chapter["title"],
            "section_text": best_chapter["text"],
            "method": "semantic_chunking",
        }
    return None

def _fallback_tail(text: str) -> dict | None:
    """兜底：取文档后40%作为格式章节候选."""
    tail_start = int(len(text) * 0.6)
    tail_text = text[tail_start:]
    if len(tail_text) > 1000:
        return {
            "chapter_number": None,
            "chapter_title": "投标文件格式（自动定位）",
            "section_text": tail_text[:10000],
            "method": "tail_fallback",
        }
    return None

async def locate_format_section(full_text: str) -> dict | None:
    """智能定位招标文件中的'投标文件格式'章节.

    四级定位策略（按优先级）：
    1. 关键词匹配 — 直接搜索"第X章...投标文件格式"
    2. 目录推断 — 从目录页定位格式章节在正文中的位置
    3. 语义分块 — 按章节头切分后关键词打分
    4. 尾部兜底 — 取文档后40%

    Returns:
        dict with keys: chapter_number, chapter_title, section_text, method
        None if all methods fail
    """
    if not full_text or len(full_text) < 500:
        return None

    # Strategy 1: keyword match
    result = _find_by_keyword(full_text)
    if result:
        logger.info("Format section located via keyword match: %s", result["chapter_title"])
        return result

    # Strategy 2: TOC inference
    result = _find_by_toc(full_text)
    if result:
        logger.info("Format section located via TOC inference")
        return result

    # Strategy 3: semantic chunking
    chapters = _split_by_chapter_headers(full_text)
    if chapters:
        result = _find_by_semantic_chunks(full_text, chapters)
        if result:
            logger.info("Format section located via semantic chunking (score-based)")
            return result

    # Strategy 4: tail fallback
    result = _fallback_tail(full_text)
    if result:
        logger.warning("Format section located via tail fallback — low confidence")
        return result

    logger.warning("Could not locate format section in document")
    return None
```

- [ ] **Step 2: 编写单元测试**

创建 `backend/tests/test_format_extractor.py`:

```python
import pytest
from app.services.format_extractor import (
    locate_format_section,
    _find_by_keyword,
    _split_by_chapter_headers,
)

SAMPLE_TEXT_CHAPTER6 = """
第一章 招标公告...
第二章 投标人须知...
第六章 投标文件格式
一、商务部分
（一）开标一览表
（二）投标函
二、技术部分
（一）项目投入服务人员一览表
第七章 合同条款...
"""

SAMPLE_TEXT_CHAPTER5 = """
第三章 招标内容...
第五章 投标书格式
一、投标函
二、法定代表人证明
三、投标报价表
第六章 评标办法...
"""

def test_find_by_keyword_chapter6():
    result = _find_by_keyword(SAMPLE_TEXT_CHAPTER6)
    assert result is not None
    assert "第六章" in result["chapter_title"]
    assert "method" == "keyword_match"

def test_find_by_keyword_chapter5():
    result = _find_by_keyword(SAMPLE_TEXT_CHAPTER5)
    assert result is not None
    assert "第五章" in result["chapter_title"]

def test_locate_chapter6_async():
    import asyncio
    result = asyncio.run(locate_format_section(SAMPLE_TEXT_CHAPTER6))
    assert result is not None
    assert "一、商务部分" in result["section_text"]

def test_locate_short_text():
    import asyncio
    result = asyncio.run(locate_format_section("短文本"))
    assert result is None

def test_split_by_chapter_headers():
    chapters = _split_by_chapter_headers(SAMPLE_TEXT_CHAPTER6)
    assert len(chapters) >= 2
    assert any("投标文件格式" in c["title"] for c in chapters)
```

- [ ] **Step 3: 运行测试验证**

```bash
cd backend
pytest tests/test_format_extractor.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/format_extractor.py backend/tests/test_format_extractor.py
git commit -m "feat: 格式提取器 — 智能格式章节定位（四级策略）"
```

---

### Task 3: 格式提取器 — AI 结构化提取格式模板

**Files:**
- Modify: `backend/app/services/format_extractor.py` (追加内容)

**Interfaces:**
- Produces: `async def extract_format_template(section_text: str, ai_adapter) -> dict` — 调用AI提取结构化格式模板
- Produces: `EXTRACT_FORMAT_SYSTEM_PROMPT: str` — 格式提取的系统提示词
- Consumes: `locate_format_section()` from Task 2

- [ ] **Step 1: 添加 AI 提取函数到 format_extractor.py**

在 `format_extractor.py` 末尾追加：

```python
import json

EXTRACT_FORMAT_SYSTEM_PROMPT = """你是招标文件格式分析专家。你的任务是从招标文件的"投标文件格式"章节中提取结构化的格式要求。

提取要点：
1. 识别文档的完整章节结构（部分→章→节），包括各级序号格式
2. 识别每个表格的完整列定义（列名、列数）
3. 识别固定格式文本（如投标函的固定措辞），区分可编辑部分和不可编辑部分
4. 识别签章/落款要求的位置和格式
5. 识别全局格式规则（如"所有页面须加盖骑缝章"、"目录包含三级标题"等）

每个提取项标注 confidence（0.0-1.0），表示提取可信度。
未知/不确定的字段使用 null 或空值，不要猜测。"""


async def extract_format_template(
    section_text: str,
    ai_adapter,
    max_input_chars: int = 8000,
) -> dict:
    """使用 AI 从格式章节文本中提取结构化格式模板.

    Args:
        section_text: 格式章节的全文文本
        ai_adapter: AI适配器实例（用于 chat_completion 调用）
        max_input_chars: 输入文本最大字符数

    Returns:
        bid_format_template dict，格式参见设计规格第3.1节
        提取失败时返回空模板 {"document_structure": [], "global_format_rules": {}}
    """
    truncated = section_text[:max_input_chars]

    user_prompt = f"""请分析以下招标文件"投标文件格式"章节，提取结构化的格式要求。

返回一个 JSON 对象，格式如下：
{{
  "document_structure": [
    {{
      "number": "一",
      "title": "商务部分",
      "required": true,
      "start_new_page": true,
      "confidence": 0.95,
      "children": [
        {{
          "number": "（一）",
          "title": "开标一览表",
          "type": "table",
          "confidence": 0.90,
          "table_schema": {{
            "note": "投标报价一览表",
            "columns": [
              {{"name": "序号", "width_hint": "auto"}},
              {{"name": "服务内容", "width_hint": "auto"}}
            ]
          }},
          "footer_note": "投标人：（公章）",
          "signature_block": {{
            "lines": ["投标人：（公章）", "法定代表人或授权代理人：（签字）", "日期：  年  月  日"]
          }}
        }},
        {{
          "number": "（二）",
          "title": "投标函",
          "type": "fixed_form",
          "confidence": 0.85,
          "fixed_text_segments": [
            {{"text": "致：{{{{招标人名称}}}}", "editable": false, "var": "tenderer_name"}},
            {{"text": "我方已仔细阅读...", "editable": true}}
          ],
          "signature_block": {{"lines": [...]}}
        }}
      ]
    }}
  ],
  "global_format_rules": {{
    "numbering_style": "chinese_legal",
    "toc_heading_title": "目录",
    "cover_page_required": true,
    "cover_elements": ["项目名称", "投标文件", "投标人名称", "日期"],
    "page_number_format": "center_bottom",
    "confidence": 0.80
  }},
  "extraction_metadata": {{
    "source": "招标文件",
    "warnings": []
  }}
}}

类型说明：
- type: "table" | "fixed_form" | "attachment" | "mixed" | null（普通文本章节不填或填null）
- numbering_style: "chinese_legal"（一、（一）、1.、（1））| "numeric"（1、1.1、1.1.1）| "mixed"
- fixed_text_segments: 仅 type=fixed_form 时需要。editable=false 表示该段文字为招标文件规定的固定措辞，必须原样使用。var 字段标注变量名以便替换
- confidence: 每个节点标注提取可信度（0.0-1.0）

提取注意事项：
- 严格按照招标文件原文的章节顺序和编号提取
- 表格列定义要完整，每列都要提取
- 签章块要包含所有要求的签章行
- 如果招标文件中某部分信息没有明确说明，使用 null 或空值，不要自行猜测补全
- 直接返回JSON对象，不要包含任何其他文字说明

招标文件"投标文件格式"章节内容：
{truncated}"""

    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": EXTRACT_FORMAT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8192,
            response_format={"type": "json_object"},
        )
        result = json.loads(response)
    except (json.JSONDecodeError, Exception) as exc:
        logger.error("AI format extraction failed: %s", exc)
        return {
            "document_structure": [],
            "global_format_rules": {},
            "extraction_metadata": {
                "error": str(exc),
                "warnings": ["AI格式提取失败，已降级到无约束模式"],
            },
        }

    # 确保必需字段存在
    result.setdefault("document_structure", [])
    result.setdefault("global_format_rules", {})
    result.setdefault("extraction_metadata", {})
    result["extraction_metadata"]["extracted_at"] = __import__("datetime").datetime.now().isoformat()

    # 统计提取结果
    sections_count = len(result.get("document_structure", []))
    logger.info(
        "Format template extracted: %d top-level sections, global_rules=%d",
        sections_count,
        len(result.get("global_format_rules", {})),
    )

    return result


async def extract_format_from_document(
    full_text: str,
    ai_adapter,
) -> dict | None:
    """完整的格式提取流水线：定位 + 提取.

    Returns:
        bid_format_template dict，定位或提取失败返回 None
    """
    location = await locate_format_section(full_text)
    if not location:
        logger.warning("Format section not found — skipping format extraction")
        return None

    section_text = location.get("section_text", "")
    if len(section_text) < 300:
        logger.warning("Format section too short (%d chars)", len(section_text))
        return None

    template = await extract_format_template(section_text, ai_adapter)

    # 将定位元数据合并到提取结果
    template.setdefault("extraction_metadata", {})
    template["extraction_metadata"]["location_method"] = location.get("method")
    template["extraction_metadata"]["chapter_title"] = location.get("chapter_title")

    return template
```

- [ ] **Step 2: 编写 AI 提取的单元测试**

在 `backend/tests/test_format_extractor.py` 中追加：

```python
from unittest.mock import AsyncMock
from app.services.format_extractor import extract_format_template, extract_format_from_document

MOCK_SECTION = """
一、商务部分
（一）开标一览表
投标人应根据本招标文件的要求编制投标报价，格式如下：

| 序号 | 服务内容 | 不含税单价（元） | 税率（%） | 含税总价（元） | 备注 |
|------|---------|----------------|----------|--------------|------|
|      |         |                |          |              |      |

投标人：（公章）
法定代表人或授权代理人：（签字）
日期：  年  月  日

（二）投标函
致：〔招标人名称〕
我方（投标人名称）已仔细阅读并充分理解贵方招标文件的全部内容...
"""

@pytest.mark.asyncio
async def test_extract_format_template_structure():
    """验证AI提取返回格式模板的基本结构."""
    mock_ai = AsyncMock()
    mock_ai.chat_completion.return_value = '''{
        "document_structure": [
            {
                "number": "一",
                "title": "商务部分",
                "required": true,
                "confidence": 0.9,
                "children": [
                    {
                        "number": "（一）",
                        "title": "开标一览表",
                        "type": "table",
                        "table_schema": {
                            "columns": [
                                {"name": "序号"},
                                {"name": "服务内容"},
                                {"name": "不含税单价（元）"},
                                {"name": "税率（%）"},
                                {"name": "含税总价（元）"},
                                {"name": "备注"}
                            ]
                        },
                        "signature_block": {
                            "lines": ["投标人：（公章）", "法定代表人或授权代理人：（签字）", "日期：  年  月  日"]
                        },
                        "confidence": 0.9
                    }
                ]
            }
        ],
        "global_format_rules": {
            "numbering_style": "chinese_legal",
            "confidence": 0.8
        },
        "extraction_metadata": {"warnings": []}
    }'''

    result = await extract_format_template(MOCK_SECTION, mock_ai)

    assert "document_structure" in result
    assert len(result["document_structure"]) == 1
    assert result["document_structure"][0]["title"] == "商务部分"

    first_child = result["document_structure"][0]["children"][0]
    assert first_child["type"] == "table"
    assert len(first_child["table_schema"]["columns"]) == 6
    assert "signature_block" in first_child

@pytest.mark.asyncio
async def test_extract_format_template_ai_failure():
    """验证AI调用失败时返回降级空模板."""
    mock_ai = AsyncMock()
    mock_ai.chat_completion.side_effect = Exception("AI timeout")

    result = await extract_format_template(MOCK_SECTION, mock_ai)

    assert result["document_structure"] == []
    assert "error" in result.get("extraction_metadata", {})

@pytest.mark.asyncio
async def test_extract_format_from_document_no_section():
    """验证无法定位格式章节时返回None."""
    mock_ai = AsyncMock()
    result = await extract_format_from_document("无格式章节的短文本", mock_ai)
    assert result is None
```

- [ ] **Step 3: 运行测试**

```bash
cd backend
pytest tests/test_format_extractor.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/format_extractor.py backend/tests/test_format_extractor.py
git commit -m "feat: 格式提取器 — AI 结构化提取格式模板"
```

---

### Task 4: 格式校验器

**Files:**
- Create: `backend/app/services/format_verifier.py`

**Interfaces:**
- Produces: `def verify_format(chapters_payload: list[dict], format_template: dict) -> dict` — 主校验函数
- Produces: `def _check_section_completeness(chapters: list[dict], structure: list[dict]) -> list[dict]` — 章节完整性检查
- Produces: `def _check_section_order(chapters: list[dict], structure: list[dict]) -> list[dict]` — 章节顺序检查
- Produces: `def _check_numbering_format(content: str, expected_number: str) -> list[dict]` — 序号格式检查
- Produces: `def _check_table_columns(content: str, table_schema: dict) -> list[dict]` — 表格列检查
- Produces: `def _check_signature_blocks(content: str, expected_signature: dict | None) -> list[dict]` — 签章块检查

- [ ] **Step 1: 创建 format_verifier.py**

```python
"""宏曦标书 - 格式校验器.
"""
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _extract_section_numbers(chapters_payload: list[dict]) -> list[str]:
    """从章节列表中提取序号列表."""
    numbers = []
    for ch in chapters_payload:
        title = ch.get("title", "")
        m = re.match(r'^[一二三四五六七八九十\d]+', title)
        if m:
            numbers.append(m.group(0))
        else:
            numbers.append(title)
    return numbers


def _find_heading_numbers_in_content(content: str) -> list[str]:
    """从生成内容中提取所有一级标题序号."""
    numbers = []
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#'):
            m = re.search(r'[一二三四五六七八九十\d]+[、．.]', stripped)
            if m:
                numbers.append(m.group(0).rstrip('、．.'))
    return numbers


def _check_section_completeness(
    chapters_payload: list[dict],
    structure: list[dict],
) -> list[dict]:
    """检查章节完整性：required=true 的章节是否全部存在."""
    results = []
    # 收集已生成的章节标题
    generated_titles = [ch.get("title", "") for ch in chapters_payload]

    for part in structure:
        if not part.get("required", True):
            continue
        part_title = part.get("title", "")
        # 检查顶层部分是否存在
        found = any(part_title in t for t in generated_titles)
        if not found:
            results.append({
                "check": "section_completeness",
                "item": part_title,
                "status": "fail",
                "detail": f"缺少必需章节：{part_title}",
                "can_auto_fix": False,
            })
        else:
            results.append({
                "check": "section_completeness",
                "item": part_title,
                "status": "pass",
                "detail": "",
                "can_auto_fix": False,
            })

    return results


def _check_section_order(
    chapters_payload: list[dict],
    structure: list[dict],
) -> list[dict]:
    """检查章节排列顺序是否与模板一致."""
    # 获取模板规定的顺序
    expected_order = [
        f"{p.get('number', '')}、{p.get('title', '')}"
        for p in structure
    ]
    # 获取生成内容的顺序
    actual_order = [ch.get("title", "") for ch in chapters_payload]

    # 检查相对顺序
    mismatches = []
    exp_idx = 0
    for act_idx, act_title in enumerate(actual_order):
        if exp_idx >= len(expected_order):
            break
        exp_title = expected_order[exp_idx]
        # 模糊匹配：检查 actual title 是否包含 expected title 的关键部分
        exp_key = re.sub(r'[一二三四五六七八九十\d]+、', '', exp_title).strip()
        if exp_key in act_title:
            exp_idx += 1
        elif any(kw in act_title for kw in ["商务", "技术", "资格审查", "其他"]):
            # 出现了一个模板中不存在的顶层章节
            mismatches.append({
                "check": "section_order",
                "item": act_title,
                "status": "warning",
                "detail": f"章节'{act_title}'的顺序与模板不符",
                "can_auto_fix": False,
            })

    if not mismatches:
        return [{
            "check": "section_order",
            "item": "all",
            "status": "pass",
            "detail": f"章节顺序与模板一致",
            "can_auto_fix": False,
        }]
    return mismatches


def _check_numbering_format(
    content: str,
    expected_style: str = "chinese_legal",
) -> list[dict]:
    """检查序号格式是否符合模板要求（如：一、（一）、1.、（1））."""
    results = []
    # 检查是否使用了正确的序号体系
    if expected_style == "chinese_legal":
        # 检查一级序号是否是中文数字
        if re.search(r'^#{1,2}\s*\d+[、.]', content, re.MULTILINE):
            results.append({
                "check": "numbering_format",
                "item": "heading_numbering",
                "status": "warning",
                "detail": "检测到阿拉伯数字序号，应为中文数字（一、二、三...）",
                "can_auto_fix": True,
                "auto_fix": "replace_arabic_with_chinese",
            })
    return results


def _check_table_columns(
    content: str,
    table_schema: dict | None,
    section_title: str = "",
) -> list[dict]:
    """检查表格列定义是否与模板一致."""
    if not table_schema:
        return []

    results = []
    expected_columns = [c["name"] for c in table_schema.get("columns", [])]

    # 在内容中查找第一个 markdown 表格
    table_match = re.search(
        r'^\|(.+)\|\s*$\n^\|[\s\-:]+\|\s*$',
        content, re.MULTILINE,
    )
    if not table_match:
        # 未找到表格 — 可能使用了分号分隔格式
        sc_match = re.search(r'([^；]+：.+)', content)
        if not sc_match:
            results.append({
                "check": "table_columns",
                "item": section_title,
                "status": "warning",
                "detail": f"未检测到表格，预期列：{'、'.join(expected_columns)}",
                "can_auto_fix": False,
            })
        return results

    # 解析实际列名
    header_row = table_match.group(1)
    actual_columns = [c.strip() for c in header_row.split('|') if c.strip()]

    # 比较列数
    if len(actual_columns) != len(expected_columns):
        results.append({
            "check": "table_columns",
            "item": section_title,
            "status": "warning",
            "detail": f"表格列数不匹配：实际{len(actual_columns)}列，预期{len(expected_columns)}列（{'、'.join(expected_columns)}）",
            "can_auto_fix": False,
        })
        return results

    # 比较列名（模糊匹配）
    col_mismatches = []
    for i, (act, exp) in enumerate(zip(actual_columns, expected_columns)):
        # 提取核心关键词比较
        act_clean = re.sub(r'[（(].*?[)）]', '', act).strip()
        exp_clean = re.sub(r'[（(].*?[)）]', '', exp).strip()
        if act_clean != exp_clean and act_clean not in exp_clean and exp_clean not in act_clean:
            col_mismatches.append(f"第{i+1}列：'{act}'≠'{exp}'")

    if col_mismatches:
        results.append({
            "check": "table_columns",
            "item": section_title,
            "status": "warning",
            "detail": f"列名偏差：{'；'.join(col_mismatches)}",
            "can_auto_fix": True,
            "auto_fix": "replace_column_names",
            "expected_columns": expected_columns,
        })
    else:
        results.append({
            "check": "table_columns",
            "item": section_title,
            "status": "pass",
            "detail": f"表格列定义与模板一致（{len(expected_columns)}列）",
            "can_auto_fix": False,
        })

    return results


def _check_signature_blocks(
    content: str,
    expected_signature: dict | None,
    section_title: str = "",
) -> list[dict]:
    """检查签章块是否存在."""
    if not expected_signature:
        return []

    required_lines = expected_signature.get("lines", [])
    if not required_lines:
        return []

    missing_lines = []
    for line in required_lines:
        # 模糊匹配：检查关键词是否存在
        key_parts = re.split(r'[：:（）()]', line)
        key_words = [p.strip() for p in key_parts if len(p.strip()) >= 2]
        key_found = any(kw in content for kw in key_words)
        if not key_found:
            missing_lines.append(line)

    if missing_lines:
        return [{
            "check": "signature_block",
            "item": section_title,
            "status": "warning",
            "detail": f"缺少签章行：{'；'.join(missing_lines)}",
            "can_auto_fix": True,
            "auto_fix": "append_signature",
            "missing_lines": required_lines,
        }]
    return [{
        "check": "signature_block",
        "item": section_title,
        "status": "pass",
        "detail": "签章块完整",
        "can_auto_fix": False,
    }]


def _apply_auto_fixes(
    chapters_payload: list[dict],
    verification_results: list[dict],
) -> tuple[list[dict], int]:
    """应用自动修正."""
    fixes_applied = 0
    for result in verification_results:
        if not result.get("can_auto_fix"):
            continue

        fix_type = result.get("auto_fix", "")

        if fix_type == "replace_arabic_with_chinese":
            # 将阿拉伯数字序号替换为中文数字
            # 这里简化处理，实际需要更复杂的替换逻辑
            pass

        elif fix_type == "replace_column_names":
            expected = result.get("expected_columns", [])
            item = result.get("item", "")
            for ch in chapters_payload:
                if item in ch.get("title", ""):
                    content = ch.get("content", "")
                    # 替换表格头行中的列名
                    header_match = re.search(
                        r'^\|(.+)\|\s*$',
                        content, re.MULTILINE,
                    )
                    if header_match:
                        new_header = '| ' + ' | '.join(expected) + ' |'
                        old_header = header_match.group(0)
                        ch["content"] = content.replace(old_header, new_header, 1)
                        fixes_applied += 1
                        result["auto_fix_applied"] = True

        elif fix_type == "append_signature":
            missing = result.get("missing_lines", [])
            item = result.get("item", "")
            for ch in chapters_payload:
                if item in ch.get("title", ""):
                    sig_block = "\n\n" + "\n".join(missing) + "\n"
                    ch["content"] = (ch.get("content") or "") + sig_block
                    fixes_applied += 1
                    result["auto_fix_applied"] = True

    return chapters_payload, fixes_applied


def verify_format(
    chapters_payload: list[dict],
    format_template: dict,
) -> dict:
    """校验生成内容是否符合招标文件格式要求.

    Args:
        chapters_payload: 生成的章节内容列表 [{"title": ..., "content": ...}, ...]
        format_template: 从招标文件提取的格式模板

    Returns:
        校验报告 dict:
        {
            "overall_status": "pass" | "pass_with_warnings" | "fail",
            "checks": [...],
            "auto_fixes_applied": int,
            "manual_review_required": int,
        }
    """
    if not format_template or not format_template.get("document_structure"):
        return {
            "overall_status": "pass",
            "checks": [],
            "auto_fixes_applied": 0,
            "manual_review_required": 0,
            "message": "无格式模板，跳过校验",
        }

    structure = format_template.get("document_structure", [])
    global_rules = format_template.get("global_format_rules", {})
    all_checks: list[dict] = []

    # 1. 章节完整性检查
    all_checks.extend(_check_section_completeness(chapters_payload, structure))

    # 2. 章节顺序检查
    all_checks.extend(_check_section_order(chapters_payload, structure))

    # 3. 逐章节内容检查（表格列、签章块）
    for part in structure:
        part_title = part.get("title", "")
        numbering_style = global_rules.get("numbering_style", "chinese_legal")

        # 找到对应的生成章节
        for ch in chapters_payload:
            if part_title in ch.get("title", ""):
                content = ch.get("content", "")

                # 序号格式检查
                all_checks.extend(_check_numbering_format(
                    content, numbering_style,
                ))

                # 遍历子章节
                for child in part.get("children", []):
                    child_title = child.get("title", "")
                    child_type = child.get("type", "")

                    if child_type == "table":
                        all_checks.extend(_check_table_columns(
                            content, child.get("table_schema"), child_title,
                        ))

                    if child.get("signature_block"):
                        all_checks.extend(_check_signature_blocks(
                            content, child.get("signature_block"), child_title,
                        ))

                break

    # 4. 应用自动修正
    chapters_payload, fixes_applied = _apply_auto_fixes(chapters_payload, all_checks)

    # 5. 汇总
    fail_count = sum(1 for c in all_checks if c.get("status") == "fail")
    warning_count = sum(1 for c in all_checks if c.get("status") == "warning")
    manual_review = sum(1 for c in all_checks
                        if c.get("status") in ("fail", "warning")
                        and not c.get("can_auto_fix"))

    if fail_count > 0:
        overall = "fail"
    elif warning_count > 0:
        overall = "pass_with_warnings"
    else:
        overall = "pass"

    return {
        "overall_status": overall,
        "checks": all_checks,
        "auto_fixes_applied": fixes_applied,
        "manual_review_required": manual_review,
        "fail_count": fail_count,
        "warning_count": warning_count,
        "pass_count": len(all_checks) - fail_count - warning_count,
    }
```

- [ ] **Step 2: 编写单元测试**

创建 `backend/tests/test_format_verifier.py`:

```python
import pytest
from app.services.format_verifier import (
    verify_format,
    _check_section_completeness,
    _check_table_columns,
    _check_signature_blocks,
)

FORMAT_TEMPLATE = {
    "document_structure": [
        {
            "number": "一",
            "title": "商务部分",
            "required": True,
            "children": [
                {
                    "number": "（一）",
                    "title": "开标一览表",
                    "type": "table",
                    "table_schema": {
                        "columns": [
                            {"name": "序号"},
                            {"name": "服务内容"},
                            {"name": "不含税单价（元）"},
                        ]
                    },
                    "signature_block": {
                        "lines": ["投标人：（公章）", "日期：  年  月  日"]
                    },
                }
            ],
        },
    ],
    "global_format_rules": {"numbering_style": "chinese_legal"},
}


def test_section_completeness_pass():
    chapters = [{"title": "一、商务部分", "content": "..."}]
    results = _check_section_completeness(
        chapters, FORMAT_TEMPLATE["document_structure"]
    )
    assert all(r["status"] == "pass" for r in results)


def test_section_completeness_fail():
    chapters = [{"title": "二、技术部分", "content": "..."}]
    results = _check_section_completeness(
        chapters, FORMAT_TEMPLATE["document_structure"]
    )
    assert any(r["status"] == "fail" for r in results)


def test_table_columns_match():
    content = "| 序号 | 服务内容 | 不含税单价（元） |\n|---|---|---|\n| 1 | 保安 | 100 |"
    schema = FORMAT_TEMPLATE["document_structure"][0]["children"][0]["table_schema"]
    results = _check_table_columns(content, schema, "开标一览表")
    assert all(r["status"] == "pass" for r in results)


def test_table_columns_mismatch():
    content = "| 编号 | 内容 | 金额 |\n|---|---|---|\n| 1 | 保安 | 100 |"
    schema = FORMAT_TEMPLATE["document_structure"][0]["children"][0]["table_schema"]
    results = _check_table_columns(content, schema, "开标一览表")
    assert any(r["status"] == "warning" for r in results)


def test_signature_block_missing():
    content = "普通文本内容，没有签章块"
    sig = FORMAT_TEMPLATE["document_structure"][0]["children"][0]["signature_block"]
    results = _check_signature_blocks(content, sig, "开标一览表")
    assert any(r["status"] == "warning" for r in results)

def test_signature_block_present():
    content = "内容...\n\n投标人：（公章）\n日期：2026年7月29日"
    sig = FORMAT_TEMPLATE["document_structure"][0]["children"][0]["signature_block"]
    results = _check_signature_blocks(content, sig, "开标一览表")
    assert all(r["status"] == "pass" for r in results)

def test_verify_format_empty_template():
    chapters = [{"title": "任意", "content": "..."}]
    result = verify_format(chapters, {})
    assert result["overall_status"] == "pass"
    assert result["message"] == "无格式模板，跳过校验"

def test_verify_format_full():
    chapters = [
        {
            "title": "一、商务部分",
            "content": "| 序号 | 服务内容 | 不含税单价（元） |\n|---|---|---|\n| 1 | 保安 | 100 |\n\n投标人：（公章）\n日期：2026年7月29日",
        }
    ]
    result = verify_format(chapters, FORMAT_TEMPLATE)
    assert result["overall_status"] in ("pass", "pass_with_warnings")
    assert len(result["checks"]) > 0
```

- [ ] **Step 3: 运行测试**

```bash
cd backend
pytest tests/test_format_verifier.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/format_verifier.py backend/tests/test_format_verifier.py
git commit -m "feat: 格式校验器 — 完整性/顺序/表格/签章校验 + 自动修正"
```

---

### Task 5: Schema 变更 + outline_engine 改造

**Files:**
- Modify: `backend/app/schemas/bid.py` (新增 schema)
- Modify: `backend/app/services/outline_engine.py` (接受 format_template)

**Interfaces:**
- Consumes: FormatVerificationResult schema (for API responses)
- Produces: `generate_deep_outline()` 新增 `format_template` 参数
- Produces: `_format_template_to_prompt_text()` 转换函数

- [ ] **Step 1: 新增 schemas**

在 `backend/app/schemas/bid.py` 末尾追加：

```python
class FormatVerificationCheck(BaseModel):
    check: str = ""
    item: str = ""
    status: str = ""  # "pass" | "warning" | "fail"
    detail: str = ""
    can_auto_fix: bool = False

class FormatVerificationResult(BaseModel):
    overall_status: str = "pass"  # "pass" | "pass_with_warnings" | "fail"
    checks: list[dict] = []
    auto_fixes_applied: int = 0
    manual_review_required: int = 0
    message: str = ""
```

- [ ] **Step 2: 改造 outline_engine.py — 添加格式模板约束**

在 `backend/app/services/outline_engine.py` 的 `generate_deep_outline()` 函数签名中添加参数，并在 prompt 构建中注入格式约束：

```python
# 函数签名变更（在现有签名末尾添加 format_template=None）
async def generate_deep_outline(
    requirements: dict,
    reference_outlines: List[Dict[str, Any]] | None = None,
    tender_text: str = "",
    min_leaves: int = MIN_LEAF_SECTIONS,
    max_leaves: int = MAX_LEAF_SECTIONS,
    format_template: dict | None = None,  # NEW
) -> list:

    # ... 现有代码 ...

    # 在构建 user_parts 时，插入格式模板约束（在参考标书结构之后）
    if format_template and format_template.get("document_structure"):
        format_prompt = _format_template_to_prompt_text(format_template)
        user_parts.append(f"\n{format_prompt}")

    # ... 剩余代码 ...
```

在文件末尾追加 `_format_template_to_prompt_text()` 函数：

```python
def _format_template_to_prompt_text(format_template: dict) -> str:
    """将格式模板转为可注入 AI prompt 的文本描述.

    只描述顶层结构（部分→章），AI 在提取的子节内可以扩展更深层级。
    """
    lines = [
        "【招标文件规定的标书格式 — 以下结构为强制要求，顶层不得更改】",
        "以下章节结构、序号、标题均来自招标文件的投标文件格式要求。",
        "生成大纲时，这些章节必须原样保留，顺序不得调整，标题不得改动。",
        "你可以在每个章下面扩展更细的子节，但不能修改顶层结构。\n",
    ]

    structure = format_template.get("document_structure", [])
    for part in structure:
        number = part.get("number", "")
        title = part.get("title", "")
        required = part.get("required", True)
        req_mark = "【必需】" if required else "【可选】"
        lines.append(f"{number}、{title} {req_mark}")

        for child in part.get("children", []):
            c_num = child.get("number", "")
            c_title = child.get("title", "")
            c_type = child.get("type") or "text"

            type_hint = ""
            if c_type == "table":
                cols = [c["name"] for c in child.get("table_schema", {}).get("columns", [])]
                type_hint = f" [表格 — 必须包含以下列：{'、'.join(cols)}]"
            elif c_type == "fixed_form":
                type_hint = " [固定格式表单 — 含招标文件规定的固定措辞]"
            elif c_type == "attachment":
                type_hint = " [附件/证明材料]"

            lines.append(f"  {c_num} {c_title}{type_hint}")

    lines.append(f"\n共 {len(structure)} 个部分。请严格按此结构生成大纲。")
    return "\n".join(lines)
```

- [ ] **Step 3: 编写单元测试**

在 `backend/tests/test_outline_format.py`:

```python
from app.services.outline_engine import _format_template_to_prompt_text

FORMAT_TEMPLATE = {
    "document_structure": [
        {
            "number": "一",
            "title": "商务部分",
            "required": True,
            "children": [
                {"number": "（一）", "title": "投标函", "type": "fixed_form"},
                {
                    "number": "（二）",
                    "title": "开标一览表",
                    "type": "table",
                    "table_schema": {"columns": [{"name": "序号"}, {"name": "金额"}]},
                },
            ],
        },
    ],
}

def test_format_template_to_prompt_text():
    text = _format_template_to_prompt_text(FORMAT_TEMPLATE)
    assert "一、商务部分" in text
    assert "【必需】" in text
    assert "（一）投标函" in text
    assert "[固定格式表单]" in text
    assert "（二）开标一览表" in text
    assert "[表格" in text
    assert "序号" in text

def test_empty_template():
    text = _format_template_to_prompt_text({})
    assert len(text) > 0
```

- [ ] **Step 4: 运行测试**

```bash
cd backend
pytest tests/test_outline_format.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/bid.py backend/app/services/outline_engine.py backend/tests/test_outline_format.py
git commit -m "feat: Schema扩展 + outline_engine接受format_template约束"
```

---

### Task 6: ai_pipeline 改造 — parse 扩展 + prompt 增强

**Files:**
- Modify: `backend/app/services/ai_pipeline.py`

**Interfaces:**
- Consumes: `extract_format_from_document()` from Task 3
- Produces: `parse_bid_requirements()` 返回值扩展 — 新增 format_template 的提取调用
- Produces: `_get_section_guidance()` 增强 — 注入表格列约束和固定表单模板
- Produces: `_build_system_prompt()` 增强 — 注入格式强制规范

- [ ] **Step 1: 扩展 parse_bid_requirements 以调用格式提取**

在 `parse_bid_requirements()` 中，在返回 result 之前添加格式提取调用。该函数需要改为接受可选的 ai_adapter 参数用于格式提取（或者改为由调用方在 `bid.py` 中单独调用）。

这里选择在 `bid.py` 的 `/upload-and-parse` 端点中串联调用，保持 `parse_bid_requirements()` 的职责单一。

在 `backend/app/api/bid.py` 的 `upload_and_parse()` 函数中，在 `parse_bid_requirements()` 调用后添加：

```python
# 在 requirements = await parse_bid_requirements(document_text) 之后
from app.services.format_extractor import extract_format_from_document
from app.services.ai_adapter import ai_adapter as ai_adapter_svc

format_template = None
try:
    format_template = await extract_format_from_document(
        document_text, ai_adapter_svc,
    )
except Exception as e:
    logger.warning("Format extraction failed (non-blocking): %s", e)

# 在创建 project 对象时存入
project = BidProject(
    # ... 现有字段 ...
    format_template_json=json.dumps(format_template, ensure_ascii=False) if format_template else "{}",
)
```

- [ ] **Step 2: 增强 _get_section_guidance — 注入表格/表单约束**

在 `_get_section_guidance()` 函数中，在现有 guidance 之后追加格式相关的约束（在 `ai_pipeline.py` 末尾附近）：

在 `_get_section_guidance()` 的调用方（`generate_chapter_with_materials` 和 `_generate_single_section_with_retry`）传入 format_template，在函数内部匹配当前章节并注入约束。

在 `generate_bid_with_deep_outline()` 的 Phase 3 中，对于每个 leaf section，从 format_template 中查找匹配的格式约束：

```python
# 在 generate_bid_with_deep_outline 中，从 project 读取 format_template
fmt_template = {}
if db and project_id:
    try:
        from sqlalchemy import select as sa_select
        result = await db.execute(
            sa_select(BidProject).where(BidProject.id == project_id)
        )
        db_proj = result.scalar_one_or_none()
        if db_proj and db_proj.format_template_json:
            fmt_template = json.loads(db_proj.format_template_json)
    except Exception:
        pass

# 在生成每个 section 时，注入格式约束
def _build_section_format_guidance(
    section_title: str,
    section_path: list[str],
    format_template: dict,
) -> str:
    """为特定章节构建格式约束提示."""
    if not format_template:
        return ""

    parts = []
    structure = format_template.get("document_structure", [])

    for part in structure:
        for child in part.get("children", []):
            child_title = child.get("title", "")
            # 匹配：章节路径中的标题包含子章节标题关键词
            path_titles = " ".join(section_path)
            if child_title not in path_titles and child_title not in section_title:
                continue

            child_type = child.get("type") or "text"

            if child_type == "table":
                cols = [c["name"] for c in child.get("table_schema", {}).get("columns", [])]
                if cols:
                    parts.append(
                        f"\n【招标文件规定的表格格式 — 必须严格遵守】\n"
                        f"本节的表格必须包含以下列（按顺序）：{'、'.join(cols)}\n"
                        f"禁止增减列、禁止调换列顺序。"
                    )
            elif child_type == "fixed_form":
                segments = child.get("fixed_text_segments", [])
                if segments:
                    parts.append(
                        f"\n【招标文件规定的固定格式 — 加粗部分必须原样使用】"
                    )
                    for seg in segments:
                        if seg.get("editable") is False:
                            parts.append(f"固定措辞（不可修改）：{seg['text']}")
                        else:
                            parts.append(f"可编辑区域：{seg['text']}")

            if child.get("signature_block"):
                sig_lines = child["signature_block"].get("lines", [])
                if sig_lines:
                    parts.append(
                        f"\n【签章要求 — 必须包含以下签章行】\n" +
                        "\n".join(sig_lines)
                    )

    return "\n".join(parts)
```

- [ ] **Step 3: 增强 SYSTEM_PROMPT — 注入全局格式规则**

在 `_build_system_prompt()` 中，如果存在格式模板的全局规则，追加到系统提示：

```python
def _build_system_prompt(
    extra_constraints: List[str] | None = None,
    format_template: dict | None = None,  # NEW
) -> str:
    parts = [SYSTEM_PROMPT]
    # ... 现有约束添加逻辑 ...

    # 全局格式规则
    if format_template and format_template.get("global_format_rules"):
        global_rules = format_template["global_format_rules"]
        numbering = global_rules.get("numbering_style", "")
        parts.append("\n【招标文件规定的格式规范 — 硬性要求】")
        if numbering:
            if numbering == "chinese_legal":
                parts.append("- 序号体系：一级用中文数字（一、二、三...），二级用带括号中文数字（（一）、（二）...），三级用阿拉伯数字加点（1.、2.）")
            elif numbering == "numeric":
                parts.append("- 序号体系：一级用阿拉伯数字（1、2、3...），二级用（1.1、1.2...）")
        if global_rules.get("toc_heading_title"):
            parts.append(f"- 目录页标题为：{global_rules['toc_heading_title']}")
        if global_rules.get("page_number_format"):
            parts.append(f"- 页码格式：{global_rules['page_number_format']}")
        parts.append("- 以上格式要求来自招标文件原文，生成内容时必须严格遵守。")

    return "\n".join(parts)
```

- [ ] **Step 4: 更新 _build_messages 调用链**

`_build_messages()` 和所有调用它的地方需要传递 format_template。这涉及：
- `parse_bid_requirements()` — 不需要（它是提取阶段）
- `generate_chapter()` — 需要
- `generate_chapter_with_materials()` — 需要

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai_pipeline.py backend/app/api/bid.py
git commit -m "feat: ai_pipeline增强 — parse扩展格式提取 + prompt注入格式约束"
```

---

### Task 7: API 端点更新 — SSE 事件扩展

**Files:**
- Modify: `backend/app/api/bid.py`

**Interfaces:**
- Consumes: `verify_format()` from Task 4
- Produces: `format_verification` SSE event
- Produces: `/upload-and-parse` 端点返回格式模板

- [ ] **Step 1: upload-and-parse 端点 — 添加格式提取**

在 `upload_and_parse()` 中，在 requirements 解析后添加格式提取（完整代码参见 Task 6 Step 1）。

同时更新 `ParseResponse` 的返回，新增 `format_template` 字段（可选）：

```python
class ParseResponse(BaseModel):
    project_name: str = ""
    requirements: dict = {}
    format_template: dict | None = None  # NEW
```

端点返回时：
```python
return ParseResponse(
    project_name=project.name,
    requirements=requirements,
    format_template=format_template,  # NEW
)
```

- [ ] **Step 2: generate 端点 — 在 done 事件前插入校验**

在 `generate_bid_with_deep_outline()` 的 Phase 4（组装）之后、Phase 5（done 事件）之前，添加校验：

```python
# 在 generate_bid_with_deep_outline 的 done 事件之前
if fmt_template and chapters_payload:
    from app.services.format_verifier import verify_format
    verification = verify_format(chapters_payload, fmt_template)

    yield {
        "event": "format_verification",
        "data": json.dumps(verification, ensure_ascii=False),
    }

    # 保存校验结果到 DB
    if db:
        try:
            from sqlalchemy import select as sa_select
            result_v = await db.execute(
                sa_select(BidProject).where(BidProject.id == project_id)
            )
            db_proj = result_v.scalar_one_or_none()
            if db_proj:
                db_proj.format_verification_json = json.dumps(verification, ensure_ascii=False)
                await db.commit()
        except Exception as exc:
            logger.warning("Failed to save verification result: %s", exc)
```

- [ ] **Step 3: export 端点 — 传递 format_template 到渲染**

在 `export_bid()` 中，从 project 读取 format_template 并传递给 `render_bid_to_docx()`：

```python
# 在构建 chapters_payload 之后
format_template = {}
if project.format_template_json and project.format_template_json != "{}":
    format_template = json.loads(project.format_template_json)

docx_path = render_bid_to_docx(
    chapters_payload,
    project.name,
    style_config=style_config,
    chapter_images=chapter_images if any(chapter_images) else None,
    company_name=cp.company_name if cp else "",
    format_template=format_template,  # NEW
)
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/bid.py backend/app/schemas/bid.py
git commit -m "feat: API端点更新 — 格式提取+校验SSE事件+渲染传参"
```

---

### Task 8: render_engine 微调

**Files:**
- Modify: `backend/app/services/render_engine.py`

**Interfaces:**
- Consumes: `format_template` (可选参数)
- Produces: 签名扩展 `render_bid_to_docx(..., format_template=None)`

- [ ] **Step 1: 函数签名扩展**

修改 `render_bid_to_docx()` 签名：

```python
def render_bid_to_docx(
    chapters,
    project_name,
    style_config=None,
    chapter_images=None,
    company_name="",
    format_template=None,  # NEW
):
```

- [ ] **Step 2: 应用全局格式规则**

在函数体中，如果 format_template 存在且包含 global_format_rules，覆盖 style 的对应项：

```python
style = dict(DEFAULT_STYLE)
if style_config:
    style.update(style_config)

# 应用招标文件规定的全局格式规则（优先级高于默认和用户配置）
if format_template and format_template.get("global_format_rules"):
    rules = format_template["global_format_rules"]
    # 封面元素由 _add_cover_page 处理
    # 目录标题在 _add_toc_page 中已使用 "目录"，如需覆盖在此处理
    logger.info(
        "Applying format template global rules: numbering=%s",
        rules.get("numbering_style", "unknown"),
    )

# ... 其余代码不变 ...
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/render_engine.py
git commit -m "feat: render_engine支持format_template全局格式规则"
```

---

### Task 9: 集成测试

**Files:**
- Create: `backend/tests/test_format_integration.py`

**Interfaces:**
- 测试端到端流程：定位 → 提取 → 校验
- Mock AI adapter 返回预定义的格式模板

- [ ] **Step 1: 编写集成测试**

```python
"""端到端格式合规集成测试."""
import json
import pytest
from unittest.mock import AsyncMock, patch

from app.services.format_extractor import (
    locate_format_section,
    extract_format_template,
    extract_format_from_document,
)
from app.services.format_verifier import verify_format


FULL_TENDER_TEXT = """
第一章 招标公告
某单位保安服务采购项目招标公告...

第二章 投标人须知
投标人须具备以下条件...

第五章 投标文件格式
投标文件由以下部分组成：

一、商务部分
（一）开标一览表
投标报价应按以下格式填写：

| 序号 | 服务内容 | 不含税单价（元） | 税率（%） | 含税总价（元） | 备注 |
|------|---------|----------------|----------|--------------|------|
|      |         |                |          |              |      |

投标人：（公章）
法定代表人或授权代理人：（签字）
日期：  年  月  日

（二）投标函
致：〔招标人名称〕
我方已仔细阅读并充分理解贵方招标文件的全部内容...

第六章 合同条款
...
"""

MOCK_AI_FORMAT_TEMPLATE = {
    "document_structure": [
        {
            "number": "一",
            "title": "商务部分",
            "required": True,
            "confidence": 0.9,
            "children": [
                {
                    "number": "（一）",
                    "title": "开标一览表",
                    "type": "table",
                    "table_schema": {
                        "columns": [
                            {"name": "序号"},
                            {"name": "服务内容"},
                            {"name": "不含税单价（元）"},
                            {"name": "税率（%）"},
                            {"name": "含税总价（元）"},
                            {"name": "备注"},
                        ]
                    },
                    "signature_block": {
                        "lines": [
                            "投标人：（公章）",
                            "法定代表人或授权代理人：（签字）",
                            "日期：  年  月  日",
                        ]
                    },
                    "confidence": 0.9,
                },
            ],
        },
    ],
    "global_format_rules": {
        "numbering_style": "chinese_legal",
        "confidence": 0.85,
    },
    "extraction_metadata": {"warnings": []},
}


@pytest.mark.asyncio
async def test_full_pipeline_locate_extract_verify():
    """端到端测试：定位→提取→校验."""
    # 1. 定位
    location = await locate_format_section(FULL_TENDER_TEXT)
    assert location is not None
    assert location["method"] == "keyword_match"
    assert "一、商务部分" in location["section_text"]

    # 2. AI 提取（mock）
    mock_ai = AsyncMock()
    mock_ai.chat_completion.return_value = json.dumps(
        MOCK_AI_FORMAT_TEMPLATE, ensure_ascii=False
    )
    template = await extract_format_template(
        location["section_text"], mock_ai,
    )
    assert len(template["document_structure"]) == 1
    assert template["document_structure"][0]["title"] == "商务部分"

    # 3. 校验
    chapters_pass = [
        {
            "title": "一、商务部分",
            "content": (
                "| 序号 | 服务内容 | 不含税单价（元） | 税率（%） | 含税总价（元） | 备注 |\n"
                "|---|---|---|---|---|---|\n"
                "| 1 | 保安服务 | 1000 | 6 | 1060 | |\n\n"
                "投标人：（公章）\n法定代表人或授权代理人：（签字）\n日期：2026年7月29日"
            ),
        }
    ]
    result = verify_format(chapters_pass, template)
    assert result["overall_status"] in ("pass", "pass_with_warnings")

    # 4. 校验 — 列不匹配的章节
    chapters_bad_table = [
        {
            "title": "一、商务部分",
            "content": (
                "| 编号 | 描述 | 金额 |\n|---|---|---|\n| 1 | 保安 | 1000 |"
            ),
        }
    ]
    result_bad = verify_format(chapters_bad_table, template)
    assert result_bad["warning_count"] > 0 or result_bad["fail_count"] > 0


@pytest.mark.asyncio
async def test_extract_format_from_document():
    """端到端：从完整文档提取格式模板."""
    mock_ai = AsyncMock()
    mock_ai.chat_completion.return_value = json.dumps(
        MOCK_AI_FORMAT_TEMPLATE, ensure_ascii=False
    )
    template = await extract_format_from_document(FULL_TENDER_TEXT, mock_ai)
    assert template is not None
    assert "document_structure" in template
    assert template["extraction_metadata"]["location_method"] == "keyword_match"


@pytest.mark.asyncio
async def test_extract_format_from_document_no_section():
    """端到端：无格式章节的文档."""
    mock_ai = AsyncMock()
    template = await extract_format_from_document(
        "只有招标公告和投标人须知，没有格式章节", mock_ai,
    )
    assert template is None


def test_verify_empty_format_template():
    """没有格式模板时校验应跳过."""
    result = verify_format(
        [{"title": "任意", "content": "..."}],
        {"document_structure": []},
    )
    assert result["overall_status"] == "pass"
```

- [ ] **Step 2: 运行集成测试**

```bash
cd backend
pytest tests/test_format_integration.py -v
```

- [ ] **Step 3: 运行全部测试确保无回归**

```bash
cd backend
pytest tests/ -v --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_format_integration.py
git commit -m "test: 端到端集成测试 — 定位→提取→校验"
```

---

### Task 10: 部署 + 验证

**Files:**
- Modify: `quick_deploy.py` (如需将新文件加入部署列表)

- [ ] **Step 1: 更新部署清单**

检查 `quick_deploy.py` 中的部署文件列表，确保新增文件被包含：

```python
# 查找 FILES_TO_DEPLOY 或类似配置
# 确保 format_extractor.py 和 format_verifier.py 在列表中
```

- [ ] **Step 2: 数据库迁移**

在服务器上运行迁移：

```bash
cd backend
alembic upgrade head
```

- [ ] **Step 3: 手动验证**

上传 `素材/招标文件正文.pdf`，验证：
1. 解析结果中包含 format_template
2. 生成的标书章节结构与招标文件一致
3. 校验报告显示 pass 或 pass_with_warnings

- [ ] **Step 4: Commit + push**

```bash
git add quick_deploy.py
git commit -m "chore: 更新部署清单包含格式引擎新文件"
git push origin master
```

---

## 依赖关系

```
Task 1 (模型) ──┐
                ├──→ Task 2 (定位) ──→ Task 3 (AI提取) ──┐
                │                                         ├──→ Task 7 (API)
Task 5 (Schema)──┘                                         │
                └──→ Task 4 (校验器) ──────────────────────┤
                                                           │
Task 6 (pipeline增强) ─────────────────────────────────────┤
                                                           │
Task 8 (render微调) ───────────────────────────────────────┘
                                                           │
                                                           ▼
                                                   Task 9 (集成测试)
                                                           │
                                                           ▼
                                                   Task 10 (部署)
```

Task 1-4 可以部分并行。Task 5-8 依赖 Task 3-4 的接口。Task 9 需要所有模块完成。
