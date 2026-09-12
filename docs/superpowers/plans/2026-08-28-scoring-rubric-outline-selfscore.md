# 评标办法驱动目录 + 生成后自我评分 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 招标文件含评标办法时，提取结构化评分指标驱动目录生成（自动补内容型缺失项）并在标书生成后自动自我评分（总分 + 逐项得分 + 依据 + 改进建议）；提取不到时优雅降级 + 手动粘贴补入。

**Architecture:** 评分指标 `bid_projects.scoring_rubric_json` 是唯一真相源，一个数据源喂两处消费：Feature A（`gap_detect` 纯函数 + `/outline/confirm` 自动补节点，标记 `source: "scoring_rubric"`，`applied` 幂等）与 Feature B（`score_engine.run_scoring` 每维度一次 AI 判卷 + 代码汇总 `compute_report`，SSE 新增 `scoring_report` 事件 + 落库 + 可重评）。提取管线克隆 `locate_format_pages` 模式直接扫 PDF 页文本，绕开 `parse_bid_requirements` 15k 截断。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (async) + alembic 0007 + SSE async generator + React (antd) + 现有 `ai_adapter.chat_completion(messages, temperature, max_tokens, response_format={"type": "json_object"})`。

**Spec:** `docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md`（已提交 83d96d8；本计划从 spec 论证，执行者需同时读 spec 与本文档）

## Global Constraints

- 新文件中文 docstring + 版权头 `Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.`（照抄现有文件风格）。
- 测试 asyncio_mode STRICT：async 测试必须 `@pytest.mark.asyncio`；纯函数不加装饰器。已知 Windows 编码失败项 `test_format_template_to_prompt_text` 照常忽略。
- 现有 SSE 事件（status/section_start/section_done/format_verification/done/chapter_start/chapter_done/…）**不破坏**，只新增 `scoring_report`。
- `children_json` 兼容嵌套树 + 扁平任务列表（顶层带 `path` 键 = 扁平）；新节点只使用嵌套树形态。
- 字段/端点命名沿用现有风格（后端 snake_case，前端 camelCase 按各自既有习惯）。
- 评分指标/报告均为**项目级 JSON 列，无独立表**（YAGNI）。
- `config.py` 不入部署 tar 包（服务器本地 flash 配置保留）；部署 `alembic upgrade head`（0007 是**加列**，create_all 不建已有表的新列）。
- AI 调用统一走 `ai_adapter.chat_completion`；输出 `content` 为空时抛 `RuntimeError`（推理模型 token 预算问题）——所有 AI 提取/判卷都必须 try/except 降级，绝不阻塞生成。

---

### Task 1: 数据层 — BidProject 两列 + alembic 0007（加列迁移）

**Files:**
- Modify: `backend/app/models/project.py`（在 `format_verification_json` 列块之后、`chapter_structure_json` 之前插入两列）
- Create: `backend/alembic/versions/20260828_0007_add_scoring_columns.py`
- Test: `backend/tests/test_scoring_model_defaults.py`（新）

**Interfaces:**
- Produces: `BidProject.scoring_rubric_json: Mapped[str]`（default `"{}"`）、`BidProject.scoring_report_json: Mapped[str]`（default `"{}"`）；alembic revision `0007`，`down_revision="0006"`。后续 Task 3/4/6 读写这两列。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_scoring_model_defaults.py`：

```python
"""bid_projects 评分指标/评分报告列 + alembic 0007 迁移链 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from pathlib import Path

from app.models.project import BidProject

BACKEND_DIR = Path(__file__).resolve().parent.parent


def test_bid_project_has_scoring_columns():
    table = BidProject.__table__
    assert "scoring_rubric_json" in table.c
    assert "scoring_report_json" in table.c


def test_scoring_columns_default_to_empty_json_object():
    table = BidProject.__table__
    assert table.c.scoring_rubric_json.default.arg == "{}"
    assert table.c.scoring_report_json.default.arg == "{}"


def test_migration_head_is_0007():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("prepend_sys_path", str(BACKEND_DIR))
    script = ScriptDirectory.from_config(cfg)
    assert set(script.get_heads()) == {"0007"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_scoring_model_defaults.py -v`
Expected: `test_bid_project_has_scoring_columns` FAIL（列不存在）→ `scoring_rubric_json` 不在 `table.c`；`test_migration_head_is_0007` FAIL（heads 是 `{"0006"}`）。

- [ ] **Step 3: 加模型列 + 写迁移**

`backend/app/models/project.py`，在 `format_verification_json` 列块（`comment="格式校验报告JSON..."` 结束处）之后插入：

```python
    scoring_rubric_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        comment="评分指标JSON — 从招标文件评标办法章节提取/手动补入的结构化评分指标（§4.1）",
    )
    scoring_report_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="{}",
        comment="自我评分报告JSON — 最近一次按评分指标对生成内容的评分结果（§4.2）",
    )
```

`backend/alembic/versions/20260828_0007_add_scoring_columns.py`（照抄 0006 的文件头风格）：

```python
"""add scoring_rubric_json, scoring_report_json columns to bid_projects

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bid_projects",
        sa.Column("scoring_rubric_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "bid_projects",
        sa.Column("scoring_report_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("bid_projects", "scoring_report_json")
    op.drop_column("bid_projects", "scoring_rubric_json")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_scoring_model_defaults.py -v`
Expected: 3 个测试全 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/models/project.py backend/alembic/versions/20260828_0007_add_scoring_columns.py backend/tests/test_scoring_model_defaults.py
git commit -m "feat: bid_projects 加评分指标/评分报告两列 + alembic 0007（加列走 upgrade）"
```

---

### Task 2: 评分指标服务 scoring_rubric.py + pdf_extractor.locate_evaluation_section

**Files:**
- Create: `backend/app/services/scoring_rubric.py`（normalize_rubric / validate_rubric / extract_rubric / rubric_context_lines）
- Modify: `backend/app/services/pdf_extractor.py`（EVALUATION_KEYWORDS + locate_evaluation_section）
- Test: `backend/tests/test_scoring_rubric.py`（新）；`backend/tests/test_pdf_extractor.py`（追加一步）

**Interfaces:**
- Consumes: `ai_adapter.chat_completion(messages, temperature, max_tokens, response_format={"type": "json_object"}) -> str`（空 content 抛 RuntimeError）；`pdf_extractor.extract_text_from_pages(pdf, start, end)`（0-indexed，含 end）；`pdfplumber.PDF`（`pdf.pages[i].extract_text()`）。
- Produces:
  - `CONTENT_KINDS = {"content", "cert", "personnel", "performance"}`（Task 4/7 复用）
  - `normalize_rubric(raw, *, force_status=None) -> dict`（§4.1 结构，容错）
  - `validate_rubric(rubric) -> list[str]`（空 list = 通过；分值加总偏差 > 5 → 提示人工核对）
  - `async extract_rubric(text, ai_adapter) -> dict`（失败/无评分表 → status="none"，永抛不出异常）
  - `rubric_context_lines(rubric, scoring_context) -> list[str]`（Task 7 复用：把结构化指标转成生成提示行）
  - `locate_evaluation_section(pdf) -> tuple[int, int, str] | None`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_scoring_rubric.py`：

```python
"""评分指标服务（评标办法结构化）单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import pytest

from app.services.scoring_rubric import (
    CONTENT_KINDS,
    normalize_rubric,
    validate_rubric,
    rubric_context_lines,
)


class TestNormalizeRubric:
    def test_none_input_defaults_none(self):
        rubric = normalize_rubric(None)
        assert rubric["status"] == "none"
        assert rubric["items"] == []

    def test_cleans_key_terms_and_kind(self):
        raw = {
            "status": "found",
            "max_total": 45,
            "items": [
                {
                    "id": "tech-01", "dimension": "技术部分", "name": "服务方案",
                    "points": 15, "kind": "content", "criteria": "完整、针对本项目",
                    "key_terms": ["服务方案", "针对性", "x"],
                },
                {
                    "name": "历史业绩", "points": 10, "kind": "BAD_KIND",
                    "key_terms": "业绩，类似项目",
                },
            ],
        }
        rubric = normalize_rubric(raw)
        assert len(rubric["items"]) == 2
        # 单字符 key_term 剔除；字符串 key_terms 拆词
        assert rubric["items"][0]["key_terms"] == ["服务方案", "针对性"]
        # 未知 kind 归一为 quality（不补节点、参与评分）
        assert rubric["items"][1]["kind"] == "quality"
        assert rubric["items"][1]["key_terms"] == ["业绩", "类似项目"]
        assert rubric["items"][1]["id"] == "item-01"

    def test_content_kinds_are_content_driven(self):
        assert CONTENT_KINDS == {"content", "cert", "personnel", "performance"}


class TestValidateRubric:
    def test_points_sum_deviation_warns(self):
        rubric = {"max_total": 100, "items": [
            {"id": "a", "name": "甲", "criteria": "…", "points": 40},
            {"id": "b", "name": "乙", "criteria": "…", "points": 40},
        ]}
        problems = validate_rubric(rubric)
        assert any("偏差" in p for p in problems)

    def test_empty_items_warns(self):
        problems = validate_rubric({"max_total": 100, "items": []})
        assert any("为空" in p for p in problems)

    def test_clean_rubric_passes(self):
        problems = validate_rubric({"max_total": 100, "items": [
            {"id": "a", "name": "甲", "criteria": "…", "points": 50},
            {"id": "b", "name": "乙", "criteria": "…", "points": 50},
        ]})
        assert problems == []


class TestRubricContextLines:
    def test_renders_content_and_quality_lines(self):
        rubric = {"items": [
            {"name": "服务方案", "points": 15, "kind": "content", "criteria": "完整", "dimension": "技术部分"},
            {"name": "针对性", "points": 5, "kind": "quality", "criteria": "贴合项目实际", "dimension": "技术部分"},
            {"name": "报价合理性", "points": 30, "kind": "price", "criteria": "合理", "dimension": "报价"},
        ]}
        lines = rubric_context_lines(rubric, "")
        joined = "\n".join(lines)
        assert "服务方案" in joined and "报价合理性" not in joined  # price 不进生成提示
        assert "质量项作为写作指导" in joined


@pytest.mark.asyncio
async def test_extract_rubric_failure_degrades_none():
    from app.services.scoring_rubric import extract_rubric

    class BoomingAdapter:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("AI returned empty content")

    rubric = await extract_rubric("评标办法正文……", BoomingAdapter())
    assert rubric["status"] == "none"
    assert rubric["items"] == []
    assert "评标办法正文" in rubric["raw_text"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_scoring_rubric.py -v`
Expected: 失败（`ModuleNotFoundError: app.services.scoring_rubric`）。

- [ ] **Step 3: 实现 scoring_rubric.py**

`backend/app/services/scoring_rubric.py`：

```python
"""宏曦标书 - 评分指标（评标办法结构化）服务.

从招标文件「评标办法/评分标准」章节提取/校验/渲染结构化评分指标。
评分指标是「目录补全 + 自我评分」的唯一真相源，数据模型见
docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §4.1。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# 内容型 kind：需要在目录中有对应章节/小节，缺失则自动补
CONTENT_KINDS = {"content", "cert", "personnel", "performance"}
# 全部合法 kind
ALL_KINDS = CONTENT_KINDS | {"quality", "price"}

EXTRACT_RUBRIC_SYSTEM_PROMPT = (
    "你是招标文件评标办法解析专家。从用户给出的评标办法章节文本中"
    "抽取完整的评分指标表，严格输出 JSON，不要输出任何其他文字。"
)


def normalize_rubric(raw, *, force_status=None) -> dict:
    """把任意输入（AI 输出 / 手动编辑 / PUT 请求）归一化为 §4.1 评分指标结构.

    容错：非 dict 输入回退空结构；畸形 items 剔除；未知 kind 归一为 quality；
    字符串 key_terms（`;；、,，\n` 分隔）拆成词表，单字符剔除。
    """
    if not isinstance(raw, dict):
        raw = {}
    status = force_status or raw.get("status") or "none"
    items = raw.get("items")
    if not isinstance(items, list):
        items = []
    normalized_items = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        kind = it.get("kind", "")
        if kind not in ALL_KINDS:
            kind = "quality"
        key_terms = it.get("key_terms")
        if not isinstance(key_terms, list):
            key_terms = re.split(r"[;；、,，\n]", str(it.get("key_terms") or ""))
        normalized_items.append({
            "id": str(it.get("id") or f"item-{i:02d}"),
            "dimension": str(it.get("dimension") or "其他"),
            "name": str(it.get("name") or ""),
            "points": int(it.get("points") or 0),
            "kind": kind,
            "criteria": str(it.get("criteria") or ""),
            "key_terms": [
                t.strip() for t in key_terms
                if isinstance(t, str) and len(t.strip()) >= 2
            ],
        })
    return {
        "status": status,
        "method_name": str(raw.get("method_name") or ""),
        "max_total": int(raw.get("max_total") or 0),
        "applied": bool(raw.get("applied", False)),
        "raw_text": str(raw.get("raw_text") or ""),
        "items": normalized_items,
    }


def validate_rubric(rubric: dict) -> list[str]:
    """校验评分指标，返回问题列表（空 list = 通过）.

    - items 为空 → 提示（目录补全/自我评分无从驱动）
    - 单项缺 name/criteria → 提示
    - 分值加总与 max_total 偏差 > 5 → 提示需人工核对（非阻塞）
    """
    problems: list[str] = []
    items = rubric.get("items") or []
    if not items:
        problems.append("评分指标为空，无法驱动目录补全与自我评分")
    total_points = 0
    for it in items:
        if not it.get("name"):
            problems.append(f"指标 {it.get('id')} 缺少名称")
        if not it.get("criteria"):
            problems.append(f"指标 {it.get('id')}「{it.get('name')}」缺少给分标准")
        total_points += int(it.get("points") or 0)
    max_total = int(rubric.get("max_total") or 0)
    if items and max_total > 0 and abs(total_points - max_total) > 5:
        problems.append(
            f"各指标分值加总 {total_points} 与满分 {max_total} 偏差超过 5 分，请人工核对"
        )
    return problems


async def extract_rubric(text: str, ai_adapter) -> dict:
    """AI 从评标办法章节文本提取结构化评分指标（§4.1）.

    任何失败（JSON 畸形 / 空 content / API 错误 / 未检测到评分表）一律降级
    status="none" 并保留 raw_text，供前端手动粘贴/手填 —— 从不让提取失败阻塞生成。
    """
    user_prompt = f"""请从下面招标文件「评标办法」章节文本中提取完整的评分指标表。

严格按以下 JSON 输出（不要输出其他文字）：
{{
  "method_name": "综合评分法",
  "max_total": 100,
  "items": [
    {{
      "dimension": "技术部分",
      "name": "服务方案的完整性与针对性",
      "points": 15,
      "kind": "quality | content | cert | personnel | performance | price",
      "criteria": "方案完整、针对本项目…",
      "key_terms": ["服务方案", "针对性"]
    }}
  ]
}}

规则：
- kind 语义：content=方案/承诺/实施类内容，cert=资质证书，personnel=人员，performance=业绩，
  quality=完整性/针对性等纯评分维度，price=报价。不确定时用 quality。
- key_terms 给 2-6 个用于匹配标书目录标题的词（做包含匹配的关键词），每个至少 2 个字符。
- items 数量以原文实际评分项为准（通常 4-15 项）；丢项视为提取失败。
- points 必须是整数，加总应等于 max_total。
- 未检测到评分表（最低价法/无评分办法）时，items 返回空数组。
- method_name 填评标办法名称；没有则填空。

【评标办法章节文本】
{text[:30000]}
"""
    try:
        response = await ai_adapter.chat_completion(
            messages=[
                {"role": "system", "content": EXTRACT_RUBRIC_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=16384,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response)
    except Exception as exc:  # JSONDecodeError / RuntimeError(空 content) / API 错误
        logger.warning("评分指标提取失败，降级 none: %s", exc)
        return normalize_rubric({"raw_text": text[:20000]}, force_status="none")
    rubric = normalize_rubric(raw, force_status="found")
    rubric["raw_text"] = text[:20000]
    if not rubric["items"]:
        logger.info("评标办法文本中未检测到评分表（最低价法？），状态 none")
        rubric["status"] = "none"
    return rubric


def rubric_context_lines(rubric: dict, scoring_context: str) -> list[str]:
    """把结构化评分指标转成章节生成提示行.

    只渲染内容型 + 质量型（price 不进生成提示，quality 作为写作指导）。
    scoring_context 非空时按其过滤同维度指标。
    """
    items = rubric.get("items") or []
    if scoring_context:
        items = [it for it in items if it.get("dimension") == scoring_context]
    content = [it for it in items if it.get("kind") in CONTENT_KINDS]
    quality = [it for it in items if it.get("kind") == "quality"]
    lines: list[str] = []
    if content:
        lines.append("【评标要求·内容项需在正文中逐项覆盖】")
        lines.extend(f"- {it.get('name')}（{it.get('points')}分）：{it.get('criteria')}" for it in content)
    if quality:
        lines.append("【评分要点·质量项作为写作指导】")
        lines.extend(f"- {it.get('name')}（{it.get('points')}分）：{it.get('criteria')}" for it in quality)
    return lines
```

- [ ] **Step 4: 实现 locate_evaluation_section**

`backend/app/services/pdf_extractor.py`，在 `FORMAT_KEYWORDS` 之后追加：

```python
# 评标办法定位关键词（用户已确认清单，运行时仍可能扩展）
EVALUATION_KEYWORDS = [
    "评标办法",
    "评分办法",
    "综合评分法",
    "评分标准",
    "评审办法",
    "评审标准",
]
```

在 `locate_format_pages` 函数之后追加：

```python
def locate_evaluation_section(pdf):
    """定位招标文件中"评标办法/评分标准"章节的起止页码并提取全文.

    评标办法通常在文中部，parse_bid_requirements 只解析前 15,000 字符经常截断，
    这里直接按页扫描 PDF 全文，绕开截断（克隆 locate_format_pages 模式）。

    Returns:
        (start_page, end_page, text)，0-indexed；未定位返回 None。
        end_page 止于「投标文件格式」/「投标函」章节之前（评标办法通常在其后截止）。
    """
    num_pages = len(pdf.pages)
    start_page = None
    for i in range(num_pages):
        text = pdf.pages[i].extract_text() or ""
        if any(kw in text for kw in EVALUATION_KEYWORDS):
            start_page = i
            break
    if start_page is None:
        logger.info("No evaluation section found in %d pages", num_pages)
        return None

    end_page = num_pages - 1
    for i in range(start_page + 1, num_pages):
        text = pdf.pages[i].extract_text() or ""
        if any(kw in text for kw in FORMAT_KEYWORDS) or "投标函" in text:
            end_page = i - 1
            break

    section_text = extract_text_from_pages(pdf, start_page, end_page)
    if not section_text.strip():
        logger.info("Evaluation section located but empty (pages %d-%d)", start_page, end_page)
        return None
    logger.info(
        "Evaluation section: pages %d-%d (%d chars)",
        start_page, end_page, len(section_text),
    )
    return start_page, end_page, section_text
```

`backend/tests/test_pdf_extractor.py` 追加（复用文件顶部的 `pdfplumber` 打开方式，加一个轻量 fake）：

```python
class TestLocateEvaluationSection:
    """评标办法章节定位（fake pdf 冒烟）."""

    def test_returns_none_when_missing(self):
        from app.services.pdf_extractor import locate_evaluation_section

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([FakePage("第一章 招标公告"), FakePage("第二章 投标人须知")])
        assert locate_evaluation_section(pdf) is None

    def test_finds_section_and_stops_at_format(self):
        from app.services.pdf_extractor import (
            locate_evaluation_section,
            extract_text_from_pages,
        )

        class FakePage:
            def __init__(self, text): self._text = text
            def extract_text(self): return self._text

        class FakePdf:
            def __init__(self, pages): self.pages = pages

        pdf = FakePdf([
            FakePage("第一章 招标公告"),
            FakePage("第三章 评标办法 综合评分法 评分标准……技术部分 15 分"),
            FakePage("详细评分细则：服务方案 5 分、业绩 10 分"),
            FakePage("第六章 投标文件格式 投标函"),
            FakePage("（格式模板页）"),
        ])
        located = locate_evaluation_section(pdf)
        assert located is not None
        start, end, text = located
        assert start == 1 and end == 2
        assert "综合评分法" in text and "投标文件格式" not in text
        # 与 extract_text_from_pages 一致（0-indexed 含 end）
        assert text == extract_text_from_pages(pdf, 1, 2)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_scoring_rubric.py tests/test_pdf_extractor.py::TestLocateEvaluationSection -v`
Expected: 全 PASS（真实 PDF 用例 `_skip_if_no_pdf()` 无素材时跳过）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/scoring_rubric.py backend/app/services/pdf_extractor.py backend/tests/test_scoring_rubric.py backend/tests/test_pdf_extractor.py
git commit -m "feat: 评分指标服务 + locate_evaluation_section（绕开 15k 截断定位评标办法）"
```

---

### Task 3: 评分指标 API（GET/parse/PUT）+ upload_and_parse 接线

**Files:**
- Create: `backend/app/api/scoring.py`（router，prefix 由 router.py 挂 `/bid`）
- Modify: `backend/app/api/router.py`（注册 scoring_router）
- Modify: `backend/app/api/bid.py`（upload_and_parse 追加评分办法提取，非阻塞）
- Test: `backend/tests/test_scoring_rubric.py`（追加 extract→落库后 normalize 路径断言）

**Interfaces:**
- Consumes: Task 1 两列、Task 2 `locate_evaluation_section`/`extract_rubric`/`normalize_rubric`/`validate_rubric`。
- Produces:
  - `GET /bid/{project_id}/scoring-rubric` → `{"rubric": {...}}` 或 404
  - `POST /bid/{project_id}/scoring-rubric/parse` body `{"text": str}` → `{"rubric": {...}, "problems": [...]}`（落库，status="found"，若提取失败为 "none"）
  - `PUT /bid/{project_id}/scoring-rubric` body `{"rubric": {...}}` → `{"rubric": {...}, "problems": [...]}`（落库；items 变动 → applied 置 False）
  - `upload_and_parse` 创建项目时写入 `scoring_rubric_json`
- 持久化读写用 `json.loads(project.scoring_rubric_json or "{}")` / `json.dumps(rubric, ensure_ascii=False)`。

- [ ] **Step 1: 写失败测试（rubric API 返回形状 via 纯函数级测试）**

本仓库 API 测试均为纯函数风格；此处测「parse 响应组装」路径与会话无关的部分 —— 直接测 normalize 后 `validate_rubric` problems 透传（已在 Task 2 覆盖），再加一个 upload 侧「rubric 提取失败不抛」的桩测试。追加到 `backend/tests/test_scoring_rubric.py`：

```python
@pytest.mark.asyncio
async def test_upload_wiring_never_raises_even_when_extraction_fails():
    """upload_and_parse 的 rubric 提取槽位：任何异常都要吞掉并落 none 状态."""
    from app.services.scoring_rubric import normalize_rubric

    class ExplodingAdapter:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("boom")

    from app.services.scoring_rubric import extract_rubric
    rubric = await extract_rubric("正文", ExplodingAdapter())
    rubric = normalize_rubric(rubric)  # 落库前最后一次归一，永不抛
    assert rubric["status"] == "none"
    assert rubric["items"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_scoring_rubric.py -v`
Expected: 新用例通过（它只依赖 Task 2 已实现函数）——断言的是「提取失败不抛」契约；真正的新功能失败点在 Step 3 实现后才被 API 层验证。若 Step 2 全绿，属预期：本步先把「异常降级」契约钉住，接下来写 API 端点本身。

- [ ] **Step 3: 实现 scoring.py + 挂载**

`backend/app/api/scoring.py`：

```python
"""宏曦标书 - 评分指标 API（评标办法驱动的目录补全 + 自我评分的数据源）.

数据模型见 docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §4。
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import BidProject
from app.models.user import User
from app.services.ai_adapter import ai_adapter
from app.services.scoring_rubric import extract_rubric, normalize_rubric, validate_rubric

logger = logging.getLogger(__name__)
router = APIRouter()


class RubricParseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=50000)


class RubricUpdateRequest(BaseModel):
    rubric: dict = Field(..., description="编辑/核对后的评分指标（§4.1 结构）")


async def _get_bid_project(project_id: str, db: AsyncSession) -> BidProject:
    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/scoring-rubric")
async def get_scoring_rubric(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取评分指标 + status（found/none/manual）."""
    project = await _get_bid_project(project_id, db)
    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        rubric = {}
    return {"rubric": rubric}


@router.post("/{project_id}/scoring-rubric/parse")
async def parse_scoring_rubric(
    project_id: str,
    data: RubricParseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动粘贴评标办法文本 → AI 提取指标 → 落库（status="found"，失败则 "none"）."""
    project = await _get_bid_project(project_id, db)
    rubric = await extract_rubric(data.text, ai_adapter)
    rubric = normalize_rubric(rubric)  # 最后一次归一，保证结构完整
    project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)
    await db.commit()
    return {"rubric": rubric, "problems": validate_rubric(rubric)}


@router.put("/{project_id}/scoring-rubric")
async def update_scoring_rubric(
    project_id: str,
    data: RubricUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存核对/编辑后的指标（status 置 "manual"）；items 变动时 applied 置 False，
    使目录补全进入可再次触发状态（幂等防重复补的撤销条件，见 spec §6.3）。"""
    project = await _get_bid_project(project_id, db)
    try:
        prev = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        prev = {}
    rubric = normalize_rubric(data.rubric, force_status="manual")
    if prev.get("items") != rubric.get("items"):
        rubric["applied"] = False
    project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)
    await db.commit()
    return {"rubric": rubric, "problems": validate_rubric(rubric)}
```

`backend/app/api/router.py`：加 import + include（与 chapters_router 同样的 `/bid` 前缀）：

```python
from app.api.scoring import router as scoring_router
...
api_router.include_router(scoring_router, prefix="/bid", tags=["评分"])
```

- [ ] **Step 4: upload_and_parse 接线**

`backend/app/api/bid.py`，在 `-- Extract format template ...` 块（`logger.warning("Format extraction failed ...")` 结束）之后、`-- Create project record --` 之前插入：

```python
    # -- Extract scoring rubric (best-effort, non-blocking) --
    # 评标办法章节通常在文中部，parse_bid_requirements 的 15k 截断会丢；
    # 这里直接扫 PDF 页文本定位评标办法章节 → AI 结构化提取。失败不阻塞生成。
    scoring_rubric = None
    try:
        import pdfplumber
        from app.services.pdf_extractor import locate_evaluation_section
        from app.services.scoring_rubric import extract_rubric
        from app.services.ai_adapter import ai_adapter as ai_adapter_svc

        with pdfplumber.open(str(saved_path)) as pdf:
            located = locate_evaluation_section(pdf)
        if located:
            _start, _end, section_text = located
            scoring_rubric = await extract_rubric(section_text, ai_adapter_svc)
    except Exception as e:
        logger.warning("Scoring rubric extraction failed (non-blocking): %s", e)

    if scoring_rubric is None:
        scoring_rubric = normalize_rubric({})  # status="none"，前端显示粘贴入口
```

并给 `BidProject(...)` 构造器加一个实参（在 `status="collecting"` 之后，`created_by` 之前）：

```python
        scoring_rubric_json=json.dumps(scoring_rubric, ensure_ascii=False),
```

`bid.py` 顶部已 import `normalize_rubric` 了吗？没有——加进 try 块内 import 或顶部补 `from app.services.scoring_rubric import normalize_rubric`。**用顶部 import**（与同文件其他 import 风格一致）：在文件 import 区加一行。

- [ ] **Step 5: 回归验证**

Run: `cd backend && python -m pytest tests/test_scoring_rubric.py tests/test_outline_format.py tests/test_pdf_extractor.py -v`
Expected: 全 PASS（`test_outline_format.py` 验证 confirm 链路不回归）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/scoring.py backend/app/api/router.py backend/app/api/bid.py backend/tests/test_scoring_rubric.py
git commit -m "feat: 评分指标 API（GET/parse/PUT）+ upload_and_parse 非阻塞提取接线"
```

---

### Task 4: Feature A — 目录补全（gap_detect + 自动补节点 + confirm 接线 + format_verifier 重接）

**Files:**
- Create: `backend/app/services/rubric_gap.py`（gap_detect / build_rubric_nodes）
- Modify: `backend/app/api/chapters.py`（`_materialise_chapters` 返回 4 元组 + 补节点 + 重编号；`confirm_outline`/`lock_chapters` 适配；`OutlineConfirmResponse` 加 `added_from_rubric`；GET /chapters 加 `rubric_cover` 预览）
- Modify: `backend/app/services/format_verifier.py`（validate_chapter_structure 加 rubric 入参，替换 naive evaluation_criteria 拆分）
- Test: `backend/tests/test_rubric_gap.py`（新）；`backend/tests/test_format_verifier.py`（追加 rubric 驱动用例）

**Interfaces:**
- Consumes: `CONTENT_KINDS`（Task 2）、`BidProject.scoring_rubric_json`（Task 1）。
- Produces:
  - `gap_detect(rubric, chapter_titles: list[str]) -> list[dict]`，每项 `{"dimension": str, "item": {...}}`
  - `build_rubric_nodes(missing, titles) -> tuple[list[dict], dict[str, list[dict]], list[str]]` → `(new_top_nodes, attach_map, added_titles)`；节点带 `source: "scoring_rubric"` + `rubric_item_id`
  - `_materialise_chapters(...) -> (created, auto_added, validation, added_from_rubric)`
  - `validate_chapter_structure(chapters, format_template, requirements, rubric=None)`
  - GET /chapters 响应新增 `rubric_cover`（未落库仅预览）

- [ ] **Step 1: 写失败测试**

`backend/tests/test_rubric_gap.py`：

```python
"""评分指标驱动目录补全（Feature A 纯函数）单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.rubric_gap import gap_detect, build_rubric_nodes


def _rubric():
    return {"items": [
        {"id": "t1", "dimension": "技术部分", "name": "服务方案", "kind": "content",
         "points": 15, "criteria": "…", "key_terms": ["服务方案"]},
        {"id": "t2", "dimension": "技术部分", "name": "针对性", "kind": "quality",
         "points": 5, "criteria": "…", "key_terms": ["针对性"]},
        {"id": "p1", "dimension": "报价", "name": "报价合理性", "kind": "price",
         "points": 30, "criteria": "…", "key_terms": ["报价"]},
        {"id": "p2", "dimension": "业绩", "name": "类似项目业绩", "kind": "performance",
         "points": 10, "criteria": "…", "key_terms": ["业绩", "类似项目"]},
    ]}


class TestGapDetect:
    def test_quality_and_price_kinds_not_in_missing(self):
        missing = gap_detect(_rubric(), ["技术部分", "报价", "投标函"])
        assert len(missing) == 2  # 只有 content 的 t1 + performance 的 p2

    def test_hit_by_key_term_in_title(self):
        missing = gap_detect(_rubric(), ["技术部分", "类似项目业绩", "投标函"])
        ids = {m["item"]["id"] for m in missing}
        assert "p2" not in ids  # 标题命中 key_term「类似项目」→ 不算缺失
        assert "t1" in ids

    def test_empty_rubric_no_missing(self):
        assert gap_detect({"items": []}, ["技术部分"]) == []


class TestBuildRubricNodes:
    def test_attaches_to_existing_dimension_chapter(self):
        missing = gap_detect(_rubric(), ["技术部分", "投标函"])
        new_top, attach, added = build_rubric_nodes(missing, ["技术部分", "投标函"])
        assert new_top == []  # 技术部分已存在 → 不建新顶层
        assert list(attach.keys()) == ["技术部分"]
        assert attach["技术部分"][0]["source"] == "scoring_rubric"
        assert attach["技术部分"][0]["rubric_item_id"] == "t1"
        assert added == ["服务方案"]

    def test_creates_top_level_when_dimension_missing(self):
        missing = gap_detect(_rubric(), ["投标函", "技术部分"])
        new_top, attach, added = build_rubric_nodes(missing, ["投标函", "技术部分"])
        # 业绩维度无匹配章节 → 新建顶层「业绩」
        assert len(new_top) == 1
        assert new_top[0]["title"] == "业绩"
        assert new_top[0]["source"] == "scoring_rubric"
        assert len(new_top[0]["children"]) == 1
        assert added == ["类似项目业绩"]
        assert attach == {}
```

`backend/tests/test_format_verifier.py` 追加：

```python
def test_validate_chapter_structure_rubric_driven_coverage():
    from app.services.format_verifier import validate_chapter_structure

    template = {"document_structure": [{"title": "技术部分", "required": True}]}
    chapters = [{"title": "技术部分", "type": "ai_generated"}]
    rubric = {"items": [
        {"id": "x", "dimension": "技术部分", "name": "售后服务方案", "kind": "content",
         "points": 10, "criteria": "…", "key_terms": ["售后服务"]},
    ]}
    validation = validate_chapter_structure(chapters, template, {}, rubric=rubric)
    assert any("售后服务方案" in n["detail"] for n in validation["coverage_notes"])
    assert validation["overall_status"] == "pass_with_warnings"


def test_validate_chapter_structure_falls_back_without_rubric():
    from app.services.format_verifier import validate_chapter_structure

    template = {"document_structure": [{"title": "技术部分", "required": True}]}
    chapters = [{"title": "技术部分", "type": "ai_generated"}]
    validation = validate_chapter_structure(
        chapters, template, {"evaluation_criteria": "售后服务；针对性"},
    )
    # 无 rubric 时回退历史 naive 拆分
    assert any("售后服务" in n["keyword"] for n in validation["coverage_notes"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_rubric_gap.py tests/test_format_verifier.py::test_validate_chapter_structure_rubric_driven_coverage -v`
Expected: `ModuleNotFoundError: app.services.rubric_gap`；format_verifier 用例 FAIL（rubric 入参未实现 → coverage_notes 空）。

- [ ] **Step 3: 实现 rubric_gap.py**

`backend/app/services/rubric_gap.py`：

```python
"""宏曦标书 - 评分指标驱动目录补全（Feature A 纯函数）.

把评分指标中的内容型缺失项转成章节补入节点。数据模型见
docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §6。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.scoring_rubric import CONTENT_KINDS


def gap_detect(rubric: dict, chapter_titles: list[str]) -> list[dict]:
    """对每个内容型指标（kind ∈ CONTENT_KINDS），用其 key_terms（≥2 字符）
    对全部章节/小节标题做包含匹配；任一词未命中任一标题 → 该指标进 missing_items。

    kind ∈ {quality, price} 不参与（quality 仅生成指导，price 仅评分）。

    Returns:
        [{"dimension": str, "item": {...}}]
    """
    titles = [t for t in chapter_titles if t]
    missing: list[dict] = []
    for it in rubric.get("items") or []:
        if it.get("kind") not in CONTENT_KINDS:
            continue
        key_terms = [t for t in it.get("key_terms") or [] if len(t.strip()) >= 2]
        if not key_terms:
            continue
        if not any(any(term in title for term in key_terms) for title in titles):
            missing.append({
                "dimension": str(it.get("dimension") or "其他"),
                "item": it,
            })
    return missing


def _dimension_parent_exists(titles: list[str], dimension: str) -> bool:
    return bool(dimension) and any(dimension in t or t in dimension for t in titles)


def build_rubric_nodes(missing: list[dict], titles: list[str]):
    """为缺失指标构建补入节点。

    Returns:
        (new_top_nodes, attach_map, added_titles)
        - new_top_nodes: 无同名维度章节时新建的顶层节点列表（含 children）
        - attach_map: {dimension_title: [子节点]}，挂到已有 dimension 章节下
        - added_titles: 本次新增的标题列表（供前端提示与 confirm 响应）
    """
    new_top: list[dict] = []
    attach: dict[str, list[dict]] = {}
    added_titles: list[str] = []
    by_dim: dict[str, list[dict]] = {}
    for m in missing:
        by_dim.setdefault(str(m["dimension"] or "其他"), []).append(m["item"])

    for dimension, items in by_dim.items():
        def _leaf(it):
            return {
                "title": str(it.get("name") or ""),
                "type": "ai_generated",
                "source": "scoring_rubric",
                "rubric_item_id": str(it.get("id") or ""),
            }

        if _dimension_parent_exists(titles, dimension):
            attach[dimension] = [_leaf(it) for it in items]
        else:
            new_top.append({
                "title": dimension,
                "type": "ai_generated",
                "source": "scoring_rubric",
                "children": [_leaf(it) for it in items],
            })
        added_titles.extend(str(it.get("name") or "") for it in items)
    return new_top, attach, added_titles
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_rubric_gap.py -v`
Expected: 全 PASS。

- [ ] **Step 5: 重接 format_verifier**

`backend/app/services/format_verifier.py`：
1) 修改 `validate_chapter_structure` 签名（保持向后兼容，默认 `rubric=None`）：

```python
def validate_chapter_structure(
    chapters,
    format_template: dict | None,
    requirements: dict | None,
    rubric: dict | None = None,
) -> dict:
```

2) 加子标题收集（放在 `titles = [...]` 之后）：

```python
    def _walk_titles(ch) -> list[str]:
        out = [_title_of(ch)]
        children = []
        if isinstance(ch, dict):
            children = ch.get("children") or []
        elif hasattr(ch, "children_json"):
            try:
                children = json.loads(ch.children_json or "[]")
            except json.JSONDecodeError:
                children = []
        for c in children:
            out.extend(_walk_titles(c))
        return out

    # 全部层级标题（顶层 + 子小节），供评分项覆盖匹配
    all_titles = [t for c in chapters for t in _walk_titles(c)]
```

3) 替换第 3 段「评分项覆盖提示」（原 :519-534 naive 拆分）为：

```python
    # 3. 评分项覆盖提示（供标题细化阶段参考）
    #    优先用结构化评分指标（gap_detect，全层级标题匹配）；无指标时回退历史 naive 拆分。
    if rubric and rubric.get("items"):
        from app.services.rubric_gap import gap_detect
        missing = gap_detect(rubric, all_titles)
        for m in missing:
            name = m["item"].get("name", "")
            coverage_notes.append({
                "keyword": name,
                "detail": f"评分项「{name}」未在章节标题中体现，确认目录时将自动补充",
            })
    else:
        criteria = (requirements or {}).get("evaluation_criteria")
        keywords: list[str] = []
        if isinstance(criteria, str):
            keywords = [k.strip() for k in re.split(r'[;；、,，\n]', criteria) if k.strip()]
        elif isinstance(criteria, list):
            keywords = [str(k).strip() for k in criteria if str(k).strip()]
        for kw in keywords:
            if not kw:
                continue
            covered = any(kw in t or t in kw for t in titles if t)
            if not covered:
                coverage_notes.append({
                    "keyword": kw,
                    "detail": f"评分项「{kw}」未在锁定章节标题中体现，建议标题细化时覆盖",
                })
```

注意：`json` 需在 format_verifier.py 顶部导入（检查现有 import；通常已有 `import json`，没有则补）。

- [ ] **Step 6: Format_verifier 测试通过**

Run: `cd backend && python -m pytest tests/test_format_verifier.py -v`
Expected: 新旧用例全 PASS（新增 rubric 用例 + 历史回退用例；历史用例不受签名默认值影响）。

- [ ] **Step 7: confirm 接线（_materialise_chapters + 两个调用方 + 响应体）**

`backend/app/api/chapters.py`：

1) `OutlineConfirmResponse` 加字段：

```python
class OutlineConfirmResponse(BaseModel):
    success: bool = False
    chapters_count: int = 0
    status: str = ""
    added_from_rubric: list[str] = []
```

2) `_materialise_chapters` 改为返回 4 元组，并在格式模板自动补之后、统一重编号之前插入 rubric 补全块；末尾无条件重编号（rubric/模板补入后编号统一）：

```python
async def _materialise_chapters(
    project: BidProject,
    db: AsyncSession,
) -> tuple[list[ProjectChapter], list[str], dict, list[str]]:
    """...（docstring 追加一句：第 4 个返回值为 added_from_rubric）"""

    # ── 评标办法指标：先加载，供覆盖校验（validate_chapter_structure）与补全共用 ──
    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        rubric = {}

    ...（现有代码不变，但 validate_chapter_structure 调用改为传入 rubric）...
    # 现有：validation = validate_chapter_structure(chapters, format_template, requirements)
    # 新的：validation = validate_chapter_structure(chapters, format_template, requirements, rubric=rubric)
    if structure and validation.get("missing_required"):
        ...（现有格式补全块不变）...
        created = final_order

    # ── 评标办法补全：内容型缺失指标自动补章，标记「来自评标办法」，幂等防重 ----------
    added_from_rubric: list[str] = []
    fresh_rubric_items = rubric.get("items")
    if fresh_rubric_items and not rubric.get("applied"):
        from app.services.rubric_gap import gap_detect, build_rubric_nodes

        def _child_titles(nodes) -> list[str]:
            out = []
            for n in nodes or []:
                out.append(str(n.get("title") or ""))
                out.extend(_child_titles(n.get("children")))
            return out

        all_titles = [c.title for c in created] + [
            t for c in created for t in _child_titles(json.loads(c.children_json or "[]"))
        ]
        missing = gap_detect(rubric, all_titles)
        if missing:
            new_top, attach, added_from_rubric = build_rubric_nodes(missing, all_titles)
            # 已存在 dimension 章节 -> 追加小节 + 刷新 children_json
            for ch in created:
                if ch.title in attach:
                    try:
                        children = json.loads(ch.children_json or "[]")
                    except json.JSONDecodeError:
                        children = []
                    children.extend(attach[ch.title])
                    ch.children_json = json.dumps(children, ensure_ascii=False)
            # 无 dimension 章节 -> 新建顶层（走同一 _make_chapter，正常 token 预算分配）
            for node in new_top:
                chapter = _make_chapter(
                    title=node.get("title", ""),
                    order_index=0,
                    ch_type="ai_generated",
                    meta=_build_meta(node, None),
                    children=node.get("children", []),
                )
                db.add(chapter)
                created.append(chapter)
            # 幂等：补入成功后置 applied，rubric 内容再变动时才清除
            rubric["applied"] = True
            project.scoring_rubric_json = json.dumps(rubric, ensure_ascii=False)
    # 统一重编号（模板补入 / rubric 补入后 order_index 连续）
    for i, c in enumerate(created):
        c.order_index = i

    return created, auto_added, validation, added_from_rubric
```

注意 `_make_chapter` / `_build_meta` 在闭包内沿用现有实现；`_build_meta(node, None)` 对 rubric 节点不匹配任何 structure part（预期行为）。

3) 两个调用方解包 4 元组：

`confirm_outline`：

```python
    created, auto_added, _validation, added_from_rubric = await _materialise_chapters(project, db)
    project.status = "collecting"
    await db.commit()

    logger.info(
        "Outline confirmed for project %s: %d chapters (auto-added: %s, rubric-added: %s)",
        project_id, len(created), auto_added, added_from_rubric,
    )

    return OutlineConfirmResponse(
        success=True,
        chapters_count=len(created),
        status="collecting",
        added_from_rubric=added_from_rubric,
    )
```

`lock_chapters`：

```python
    created, auto_added, validation, added_from_rubric = await _materialise_chapters(project, db)
    ...
    if added_from_rubric:
        message += f" 已按评标办法自动补充：{'、'.join(added_from_rubric)}。"
```

- [ ] **Step 8: GET /chapters 加 rubric_cover 预览（补前可见）**

`get_chapters` 重排为「先解析 unlocked 结构 → 统一算 rubric_cover → 再分支返回」（locked 分支是提前 return，预览必须在它之前算完）：

```python
    # 解析 chapter_structure_json（unlocked 形态；locked 分支用 ProjectChapter 行）
    try:
        chapters = json.loads(project.chapter_structure_json) if project.chapter_structure_json else []
    except json.JSONDecodeError:
        chapters = []

    # ── 评标办法覆盖预览（未落库，仅供确认页「评标办法覆盖」提示条）──
    def _collect_titles(items) -> list[str]:
        out = []
        for n in items or []:
            if isinstance(n, dict):
                out.append(str(n.get("title") or ""))
                out.extend(_collect_titles(n.get("children")))
        return out

    try:
        preview_rubric = json.loads(project.scoring_rubric_json or "{}") if project.scoring_rubric_json else {}
    except json.JSONDecodeError:
        preview_rubric = {}
    rubric_cover = None
    if preview_rubric.get("items"):
        if project.chapters:
            all_titles = []
            for ch in sorted(project.chapters, key=lambda c: c.order_index):
                all_titles.append(ch.title)
                try:
                    all_titles.extend(_collect_titles(json.loads(ch.children_json or "[]")))
                except json.JSONDecodeError:
                    pass
        else:
            all_titles = _collect_titles(chapters)
        from app.services.rubric_gap import gap_detect
        missing = gap_detect(preview_rubric, all_titles)
        rubric_cover = {
            "status": preview_rubric.get("status"),
            "applied": bool(preview_rubric.get("applied")),
            "missing": [
                {"dimension": m["dimension"], "name": m["item"].get("name", "")}
                for m in missing
            ],
        }

    # locked 分支（提前 return，rubric_cover 已算好）
    if project.chapters:
        return {
            "locked": True,
            "chapters": [ ...现有 locked 分支的逐章 dict 构造... ],
            "rubric_cover": rubric_cover,
        }
    # unlocked 分支
    return {"locked": False, "chapters": chapters, "rubric_cover": rubric_cover}
```

（`missing` 为「该补但未补」的指标项；`rubric.applied` 为 True（已确认补过）时前端只展示状态、不再提示“将自动补充”。）

- [ ] **Step 9: 回归验证**

Run: `cd backend && python -m pytest tests/test_rubric_gap.py tests/test_format_verifier.py tests/test_outline_format.py -v`
Expected: 全 PASS。

- [ ] **Step 10: 提交**

```bash
git add backend/app/services/rubric_gap.py backend/app/services/format_verifier.py backend/app/api/chapters.py backend/tests/test_rubric_gap.py backend/tests/test_format_verifier.py
git commit -m "feat: 评标办法驱动目录补全 — gap_detect + confirm 自动补（source 标记 + applied 幂等）+ 覆盖预览 + format_verifier 重接"
```

---

### Task 5: score_engine 聚合纯函数（compute_report / _match_dimensions）

**Files:**
- Create: `backend/app/services/score_engine.py`（本任务只做纯函数部分；AI 判卷 `run_scoring`/`_grade_dimension` 在 Task 6）
- Test: `backend/tests/test_score_engine.py`（新，纯函数）

**Interfaces:**
- Consumes: 无（独立）。
- Produces（Task 6 直接消费）：
  - `compute_report(rubric: dict, graded: list[dict]) -> dict`（§4.2 报告）
  - `_match_dimensions(dims: dict[str, list[dict]], chapters: list[dict]) -> dict[str, str]`
  - 常量 `SCORE_STATUS_PASS = 0.90`、`SCORE_STATUS_PARTIAL = 0.60`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_score_engine.py`：

```python
"""自我评分汇总（Feature B 纯函数部分）单元测试.

判定阈值与折算规则见 spec §4.2：
- points_obtained ≥ 90%×points_total → pass；≥60% → partial；否则 fail
- 最终分数 = total / scored_total（unscored 项按其满分剔除折算）
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
import pytest

from app.services.score_engine import compute_report, _match_dimensions


def _rubric(items=None):
    return {
        "status": "found",
        "method_name": "综合评分法",
        "max_total": 45,
        "items": items or [
            {"id": "t1", "dimension": "技术部分", "name": "服务方案", "kind": "content", "points": 15, "criteria": "…", "key_terms": ["服务方案"]},
            {"id": "t2", "dimension": "技术部分", "name": "针对性", "kind": "quality", "points": 5, "criteria": "…", "key_terms": ["针对性"]},
            {"id": "p1", "dimension": "报价", "name": "报价合理性", "kind": "price", "points": 30, "criteria": "…", "key_terms": ["报价"]},
        ],
    }


def _g(nid, obtained, status="graded", evidence="5.2 服务方案（P18）"):
    return {"id": nid, "name": "", "dimension": "", "points_obtained": obtained,
            "status": status, "evidence": evidence, "gap": "", "suggestion": ""}

class TestComputeReport:
    def test_scored_and_unscored_totals(self):
        report = compute_report(_rubric(), [
            _g("t1", 13),           # graded
            _g("t2", 5),            # graded
            _g("p1", 0, status="unscored", evidence="未含报价"),  # price 无内容
        ])
        assert report["total"] == 18
        assert report["scored_total"] == 20        # 报价 30 分剔除
        assert report["max_total"] == 45
        assert "未含报价" in report["unscored_note"]
        assert report["items"][2]["status"] == "unscored"

    def test_status_thresholds(self):
        report = compute_report(_rubric(), [
            _g("t1", 15),   # 100% → pass
            _g("t2", 3),    # 60% → partial
            _g("p1", 5, status="unscored"),  # unscored 不参与阈值
        ])
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id["t1"]["status"] == "pass"
        assert by_id["t2"]["status"] == "partial"

    def test_overscore_clamped_with_warning(self):
        report = compute_report(_rubric(), [
            _g("t1", 99),   # 超过满分 15 → 钳制
            _g("t2", 0, status="unscored"),
            _g("p1", 0, status="unscored"),
        ])
        by_id = {i["id"]: i for i in report["items"]}
        assert by_id["t1"]["points_obtained"] == 15
        assert any("超满分" in w for w in report["warnings"])

    def test_no_unscored_scored_equals_max(self):
        report = compute_report(_rubric(), [_g("t1", 15), _g("t2", 5), _g("p1", 30)])
        assert report["scored_total"] == 45
        assert report["total"] == 50     # 这里 total 超过 max_total 属正常（判卷结果）
        assert report["unscored_note"] == ""

    def test_single_item_failure_marked_unscored_others_intact(self):
        report = compute_report(_rubric(), [_g("t1", 15), _g("t2", 5), _g("p1", 30)])
        # 覆盖：graded 全通过时没有 unscored
        assert all(i["status"] != "unscored" for i in report["items"])


class TestMatchDimensions:
    def test_title_contains_dimension(self):
        dims = {"技术部分": [{"id": "t1"}]}
        chapters = [{"title": "技术部分（一）", "content": "正文1"}, {"title": "投标函", "content": "x"}]
        matched = _match_dimensions(dims, chapters)
        assert matched["技术部分"] == "正文1"

    def test_no_match_returns_empty(self):
        dims = {"业绩": [{"id": "p2"}]}
        matched = _match_dimensions(dims, [{"title": "投标函", "content": "x"}])
        assert matched == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_score_engine.py -v`
Expected: `ModuleNotFoundError: app.services.score_engine`。

- [ ] **Step 3: 实现纯函数**

`backend/app/services/score_engine.py`：

```python
"""宏曦标书 - 自我评分引擎.

按评分指标对已生成内容判卷：每维度 AI 判卷（见 run_scoring，Task 6）+ 代码汇总
compute_report 合成 §4.2 评分报告。数据模型见
docs/superpowers/specs/2026-08-28-scoring-rubric-outline-selfscore-design.md §4.2。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SCORE_STATUS_PASS = 0.90
SCORE_STATUS_PARTIAL = 0.60


def _match_dimensions(dims: dict[str, list[dict]], chapters: list[dict]) -> dict[str, str]:
    """dimension 名 ↔ 章节标题 匹配（精确/包含，首个命中）。无匹配维度不在结果中。

    Returns:
        {dimension: content}
    """
    out: dict[str, str] = {}
    by_title = {str(c.get("title") or ""): c for c in chapters}
    for dim in dims:
        hit_title = next(
            (t for t in by_title if dim and (dim in t or t in dim)),
            None,
        )
        if hit_title:
            out[dim] = str(by_title[hit_title].get("content") or "")
    return out


def _clamp_points(points_total: float, points_obtained) -> float:
    p = float(points_obtained)
    return min(max(p, 0.0), max(float(points_total), 0.0))


def compute_report(rubric: dict, graded: list[dict]) -> dict:
    """代码汇总：把逐项判卷结果合成为 §4.2 评分报告（纯函数，可测）.

    - 最终分数 = total / scored_total（unscored 项按其满分剔除折算）
    - points_obtained > points_total → 钳制 + 警告
    - 单项 status：≥90% pass；≥60% partial；否则 fail；unscored 保持
    """
    max_total = int(rubric.get("max_total") or 0)
    items = rubric.get("items") or []
    by_id = {str(it.get("id")): it for it in items}
    out_items: list[dict] = []
    warnings: list[str] = []
    total = 0.0
    scored_total = 0.0
    unscored_names: list[str] = []

    for g in graded:
        item = by_id.get(str(g.get("id"))) or {}
        pts_total = float(item.get("points") or g.get("points_total") or 0)
        raw = float(g.get("points_obtained") or 0)
        name = str(item.get("name") or g.get("name") or "")
        if g.get("status") == "unscored":
            status = "unscored"
            obtained = 0.0
            unscored_names.append(name or "未命名指标")
        else:
            obtained = _clamp_points(pts_total, raw)
            if raw > pts_total:
                warnings.append(f"{name} 得分超出满分，已钳制为 {obtained}")
            ratio = obtained / max(pts_total, 1e-9)
            if ratio >= SCORE_STATUS_PASS:
                status = "pass"
            elif ratio >= SCORE_STATUS_PARTIAL:
                status = "partial"
            else:
                status = "fail"
            scored_total += pts_total
            total += obtained
        out_items.append({
            "id": str(g.get("id") or item.get("id") or ""),
            "dimension": str(item.get("dimension") or g.get("dimension") or ""),
            "name": name,
            "points_total": round(pts_total, 1),
            "points_obtained": round(obtained, 1),
            "status": status,
            "evidence": str(g.get("evidence") or ""),
            "gap": str(g.get("gap") or ""),
            "suggestion": str(g.get("suggestion") or ""),
        })

    note = ""
    if unscored_names:
        note = f"未参与计分：{'、'.join(dict.fromkeys(unscored_names))}"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method_name": str(rubric.get("method_name") or ""),
        "max_total": max_total,
        "total": round(total, 1),
        "scored_total": round(scored_total, 1),
        "unscored_note": note,
        "warnings": warnings,
        "items": out_items,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_score_engine.py -v`
Expected: 全 PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/score_engine.py backend/tests/test_score_engine.py
git commit -m "feat: self-score 汇总纯函数 — compute_report（折算/钳制/阈值/unscored 剔除）"
```

---

### Task 6: Feature B — AI 判卷（run_scoring）+ SSE scoring_report + 重评/报告端点

**Files:**
- Modify: `backend/app/services/score_engine.py`（追加 `run_scoring` / `_grade_dimension`）
- Modify: `backend/app/services/ai_pipeline.py`（generate_from_chapter_structure 收尾：format_verification 之后、done 之前插 scoring 阶段）
- Modify: `backend/app/api/scoring.py`（追加 POST /score + GET /scoring-report）
- Test: `backend/tests/test_score_engine.py`（追加 run_scoring 编排测试，mock AI）

**Interfaces:**
- Consumes: `compute_report`/`_match_dimensions`（Task 5）、`ai_adapter`（ai_pipeline.py:21 已导入）、`chapters_payload`（{title, content, order_index} 列表）、`project.scoring_rubric_json`/`scoring_report_json`（Task 1）。
- Produces:
  - `async run_scoring(rubric, chapters: list[dict], ai) -> dict`（每 dimension 一次 AI 调用、串行；无匹配维度用全部内容尽力评分；单项失败 → unscored + 原因）
  - SSE 事件 `scoring_report`（data = 完整报告 JSON）
  - `POST /bid/{project_id}/score`（手动重评，覆盖报告）→ report
  - `GET /bid/{project_id}/scoring-report` → report（无则 `{}`）

- [ ] **Step 1: 写失败测试（run_scoring 编排，mock AI）**

`backend/tests/test_score_engine.py` 追加（文件顶部需 `import json`）：

```python
import json

@pytest.mark.asyncio
async def test_run_scoring_orchestrates_per_dimension_and_degrades_failure():
    from app.services.score_engine import run_scoring

    rubric = _rubric()  # 3 项：技术部分×2 + 报价×1
    chapters = [{"title": "技术部分", "content": "服务方案正文……"}]

    calls = []

    class FakeAI:
        async def chat_completion(self, messages, **kwargs):
            calls.append(messages[1]["content"])
            if "报价" in messages[1]["content"]:
                return '{"items": [{"id": "p1", "points_obtained": 30, "status_source": "含报价", "gap": "", "suggestion": ""}]}'
            return json.dumps({"items": [
                {"id": "t1", "points_obtained": 13, "status_source": "技术部分（一）", "gap": "", "suggestion": ""},
                {"id": "t2", "points_obtained": 2, "status_source": "技术部分（二）", "gap": "缺针对性", "suggestion": "补充针对分析"},
            ]})

    report = await run_scoring(rubric, chapters, FakeAI())
    # 报价维度无匹配章节 → 用全部内容尽力评分（仍产出 unscored 仅当判卷标 unscored）
    assert report["total"] == 45
    assert report["items"][0]["evidence"] == "技术部分（一）"
    by_id = {i["id"]: i for i in report["items"]}
    assert by_id["t1"]["status"] == "pass"      # 13/15 = 86.7% → partial！
    assert by_id["p1"]["status"] == "pass"      # 30/30
    # 技术部分只调了一次 AI（按维度分组，串行）
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_run_scoring_single_dimension_failure_marks_unscored():
    from app.services.score_engine import run_scoring

    rubric = _rubric()
    chapters = [{"title": "技术部分", "content": "x"}]

    class BrokenAI:
        async def chat_completion(self, **kwargs):
            raise RuntimeError("判卷失败")

    report = await run_scoring(rubric, chapters, BrokenAI())
    assert report["total"] == 0
    assert report["scored_total"] == 0
    assert all(i["status"] == "unscored" for i in report["items"])
    assert any("判卷失败" in i["suggestion"] for i in report["items"])
```

注意 `by_id["t1"]["status"]`：13/15 = 0.867 < 0.90 → partial（上面注释已纠正）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && python -m pytest tests/test_score_engine.py -v`
Expected: 新用例 FAIL（`run_scoring` 未定义）。

- [ ] **Step 3: 实现 run_scoring + _grade_dimension**

`backend/app/services/score_engine.py` 追加（import 区加 `import json`）：

```python
JUDGE_SYSTEM_PROMPT = (
    "你是资深评标专家。根据招标文件评分办法中的评分项，对已生成的标书内容逐项判分。"
    "严格输出 JSON，不要输出任何其他文字。"
)


async def run_scoring(rubric: dict, chapters: list[dict], ai) -> dict:
    """对已生成内容按评分指标判卷，产出 §4.2 报告.

    - 每 dimension 一次 AI 调用，串行（medium token 预算）
    - dimension↔章节映射：_match_dimensions（精确/包含，首个命中）
    - 无匹配维度 → 用全部已组装内容尽力评分（单次调用）
    - 单项/单维度判卷失败 → 该项 unscored + 原因，其余照常，绝不整体失败

    Args:
        rubric: §4.1 评分指标
        chapters: [{title, content}] 已生成章节（与导出同一份数据）
        ai: ai_adapter（chat_completion）
    """
    items = rubric.get("items") or []
    dims: dict[str, list[dict]] = {}
    for it in items:
        dims.setdefault(str(it.get("dimension") or "其他"), []).append(it)

    matched = _match_dimensions(dims, chapters)
    all_content = "\n\n".join(c.get("content", "") for c in chapters)[:30000]
    graded: list[dict] = []
    for dim, dim_items in dims.items():
        content = matched.get(dim) or all_content
        try:
            graded.extend(await _grade_dimension(dim, dim_items, content, ai))
        except Exception as exc:
            logger.warning("评分维度 %s 判卷失败: %s", dim, exc)
            for it in dim_items:
                graded.append({
                    "id": it.get("id"),
                    "dimension": dim,
                    "name": it.get("name", ""),
                    "points_obtained": 0,
                    "status": "unscored",
                    "evidence": "",
                    "gap": "",
                    "suggestion": f"判卷失败：{exc}",
                })
    return compute_report(rubric, graded)


async def _grade_dimension(dimension: str, items: list[dict], content: str, ai) -> list[dict]:
    """单维度一次判卷调用，返回逐项判卷结果（[{"id", "points_obtained", "status", "evidence", "gap", "suggestion"}]）。

    status：evidence 为空或「未含报价」→ "unscored"；否则 "graded"（汇总层按分值定 pass/partial/fail）。
    """
    rows = "\n".join(
        f"- id={it.get('id')} name={it.get('name')} 满分={it.get('points')} "
        f"给分标准={it.get('criteria')} kind={it.get('kind')}"
        for it in items
    )
    user_prompt = f"""【评分维度】{dimension}

【评分项】
{rows}

【该维度已生成内容】
{content[:30000]}

请逐项输出：
{{
  "items": [
    {{
      "id": "...",
      "points_obtained": 12,
      "status_source": "内容实际出现的章节号/标题，作为给分依据",
      "gap": "失分点说明（无则空字符串）",
      "suggestion": "改进建议（无则空字符串）"
    }}
  ]
}}

要求：
- points_obtained 不得超过该项满分；实在无法判断给 0。
- 报价项（kind=price）若内容中无报价信息：points_obtained=0、status_source="未含报价"。
- status_source 必须具体到章节号/标题，不能写「内容中」「未知」。
"""
    response = await ai.chat_completion(
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=8192,
        response_format={"type": "json_object"},
    )
    raw = json.loads(response)
    result: list[dict] = []
    for g in raw.get("items") or []:
        evidence = str(g.get("status_source") or "")
        status = "graded" if evidence and evidence != "未含报价" else "unscored"
        result.append({
            "id": str(g.get("id") or ""),
            "points_obtained": float(g.get("points_obtained") or 0),
            "status": status,
            "evidence": evidence,
            "gap": str(g.get("gap") or ""),
            "suggestion": str(g.get("suggestion") or ""),
        })
    return result
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_score_engine.py -v`
Expected: 全 PASS（含新增两个 async 用例）。

- [ ] **Step 5: SSE scoring_report 接入 generate_from_chapter_structure 收尾**

`backend/app/services/ai_pipeline.py`，在 `-- Phase: Done --` 注释块之前（format_verification SSE yield 之后）插入：

```python
    # ── Phase: 自我评分（评标办法驱动，不阻塞 done）──
    try:
        rubric = json.loads(getattr(project, "scoring_rubric_json", "{}") or "{}")
    except json.JSONDecodeError:
        rubric = {}
    if rubric.get("status") in ("found", "manual") and rubric.get("items"):
        try:
            from app.services.score_engine import run_scoring
            report = await run_scoring(rubric, chapters_payload, ai_adapter)
            try:
                project.scoring_report_json = json.dumps(report, ensure_ascii=False)
                await db.commit()
            except Exception:
                pass
            yield {
                "event": "scoring_report",
                "data": json.dumps(report, ensure_ascii=False),
            }
        except Exception as exc:
            logger.warning("自我评分失败（不阻塞 done）: %s", exc)
```

（`ai_adapter` 已在文件顶部 import；`project`/`db` 在本生成器作用域已有。）

- [ ] **Step 6: POST /score + GET /scoring-report**

`backend/app/api/scoring.py` 追加（import 区加 `selectinload`、`ProjectChapter` 不需要——走 project.chapters 关系；加 `run_scoring`）：

```python
    from sqlalchemy.orm import selectinload
    from app.services.score_engine import run_scoring
```

```python
@router.post("/{project_id}/score")
async def rescore_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动重评：读已落库内容（final_content||ai_generated_content + children 小节标题）
    重跑判卷，覆盖报告。与导出同一份数据（spec §7.2）。"""
    result = await db.execute(
        select(BidProject)
        .where(BidProject.id == project_id)
        .options(selectinload(BidProject.chapters))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        rubric = json.loads(project.scoring_rubric_json or "{}")
    except json.JSONDecodeError:
        rubric = {}
    if rubric.get("status") not in ("found", "manual") or not rubric.get("items"):
        raise HTTPException(
            status_code=400,
            detail="该项目未检测到可用评分指标（状态 none），请先粘贴/编辑评分办法",
        )

    def _load_chapters() -> list[dict]:
        out = []
        for ch in sorted(project.chapters, key=lambda c: c.order_index):
            content = ch.final_content or ch.ai_generated_content or ""
            try:
                children = json.loads(ch.children_json or "[]")
            except json.JSONDecodeError:
                children = []
            titles: list[str] = []
            def _walk(nodes):
                for n in nodes or []:
                    titles.append(str(n.get("title") or ""))
                    _walk(n.get("children"))
            _walk(children)
            if titles:
                content = f"{content}\n\n小节：\n" + "\n".join(titles)
            out.append({"title": ch.title, "content": content})
        return out

    report = await run_scoring(rubric, _load_chapters(), ai_adapter)
    project.scoring_report_json = json.dumps(report, ensure_ascii=False)
    await db.commit()
    return report


@router.get("/{project_id}/scoring-report")
async def get_scoring_report(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取最新评分报告（重进项目展示）。"""
    project = await _get_bid_project(project_id, db)
    try:
        return json.loads(project.scoring_report_json or "{}")
    except json.JSONDecodeError:
        return {}
```

- [ ] **Step 7: 回归 + 提交**

Run: `cd backend && python -m pytest tests/test_score_engine.py tests/test_score_engine.py -v` **以及** `cd backend && python -m pytest tests/test_generate_chapter_flow.py -v`（确认 ai_pipeline 辅助函数未回归）
Expected: 全 PASS（`test_generate_chapter_flow.py` 只测纯辅助函数，不受收尾插入影响）。

```bash
git add backend/app/services/score_engine.py backend/app/services/ai_pipeline.py backend/app/api/scoring.py backend/tests/test_score_engine.py
git commit -m "feat: 自我评分 AI 判卷 + SSE scoring_report 事件 + POST /score 重评 + GET /scoring-report"
```

---

### Task 7: 生成期感知 — title_refiner 用结构化指标替换自由文本

**Files:**
- Modify: `backend/app/api/chapters.py`（refine_chapter_titles 端点 ~:600：requirements 合并 rubric）
- Modify: `backend/app/services/title_refiner.py`（:95-96 evaluation_criteria 分支改为 rubric 优先）
- Test: `backend/tests/test_title_refiner_rubric.py`（新，纯函数级测 `rubric_context_lines` 已覆盖渲染；本任务测「requirements 注入结构」→ 合并为纯函数 `_merge_rubric_into_requirements` 放 rubric_gap 后测？—— 简化：直接测 title_refiner.build_prompt 的增量。）

**Interfaces:**
- Consumes: `eval` 的 `requirements` dict、`chapter_meta.scoring_context`、`rubric_context_lines`（Task 2）。
- Produces: title_refiner 的提示行 `req_lines` 在 rubric 存在时来自 `rubric_context_lines(rubric, scoring_context)`（内容型 + 质量型），无 rubric 时回退 `evaluation_criteria`。

- [ ] **Step 1: 写失败测试**

`backend/tests/test_title_refiner_rubric.py`：

```python
"""生成期感知：结构化评分指标注入标题细化提示 单元测试.
Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""
from app.services.scoring_rubric import rubric_context_lines


def test_scoring_context_filters_dimension_items():
    rubric = {"items": [
        {"name": "服务方案", "points": 15, "kind": "content", "criteria": "完整", "dimension": "技术部分"},
        {"name": "团队配置", "points": 10, "kind": "content", "criteria": "齐全", "dimension": "人员部分"},
    ]}
    lines = rubric_context_lines(rubric, "技术部分")
    joined = "\n".join(lines)
    assert "服务方案" in joined and "团队配置" not in joined


def test_empty_rubric_renders_no_lines():
    assert rubric_context_lines({"items": []}, "") == []
    assert rubric_context_lines({}, "") == []
```

（渲染规则已在 Task 2 测过；这里补 dimension 过滤 + 空输入契约。Task 2 的 `rubric_context_lines` 已实现 dimension 过滤：`if scoring_context: items = [it for it in items if it.get('dimension') == scoring_context]`。）

- [ ] **Step 2: 跑测试确认通过**

Run: `cd backend && python -m pytest tests/test_title_refiner_rubric.py -v`
Expected: 直接 PASS（契约已由 Task 2 的 `rubric_context_lines` 满足）——本任务的主体是后面的「端点合并 rubric + title_refiner 接线」。

- [ ] **Step 3: 端点合并 rubric**

`backend/app/api/chapters.py` refine_chapter_titles（~:600 `requirements = json.loads(...)` 之后）加：

```python
        # 生成期感知（spec §6.1）：结构化评分指标喂标题细化提示
        try:
            rubric = json.loads(project.scoring_rubric_json or "{}")
        except json.JSONDecodeError:
            rubric = {}
        if rubric.get("items"):
            requirements = {**requirements, "scoring_rubric": rubric}
```

- [ ] **Step 4: title_refiner 用 rubric 渲染**

`backend/app/services/title_refiner.py`，把 `req_lines` 里的 evaluation_criteria 分支（:95-96）替换为：

```python
    rubric = requirements.get("scoring_rubric")
    if rubric and rubric.get("items"):
        from app.services.scoring_rubric import rubric_context_lines
        req_lines.extend(rubric_context_lines(rubric, scoring_context))
    elif requirements.get("evaluation_criteria"):
        req_lines.append(f"评标标准：{requirements['evaluation_criteria']}")
```

（`scoring_context` 在 :87 已取；rubric 有内容型+质量型时，req_lines 里同时出现两组「评标要求/评分要点」行，替换掉自由文本那一行。若 rubric 只有 quality/price 项，rubric_context_lines 可能返回空——此时不追加任何行，符合「quality 仅生成指导」语义。）

- [ ] **Step 5: 回归 + 提交**

Run: `cd backend && python -m pytest tests/test_title_refiner_rubric.py tests/test_scoring_rubric.py -v`
Expected: 全 PASS。

```bash
git add backend/app/api/chapters.py backend/app/services/title_refiner.py backend/tests/test_title_refiner_rubric.py
git commit -m "feat: 标题细化提示用结构化评分指标（rubric 优先，evaluation_criteria 回退）"
```

---

### Task 8: 前端评分指标面板 + 徽标 + 覆盖提示（OutlineConfirm）

**Files:**
- Create: `frontend/src/api/scoring.ts`（scoringApi + 类型）
- Create: `frontend/src/components/ScoringRubric/ScoringRubricPanel.tsx`
- Modify: `frontend/src/api/outline.ts`（OutlineChapter 加 `source?`；OutlineGetResponse 加 `rubric_cover?`；confirm 响应类型加 `added_from_rubric`）
- Modify: `frontend/src/pages/project/OutlineConfirm.tsx`（挂面板 + 覆盖提示条 + confirm toast 展示自动补列表）
- Modify: `frontend/src/components/OutlineEditor/OutlineTree.tsx`（节点 title 加「来自评标办法」金徽标）

**Interfaces:**
- Consumes: Task 3 的 GET/POST-parse/PUT `/bid/{pid}/scoring-rubric`；Task 4 的 GET /chapters `rubric_cover` + confirm `added_from_rubric`。
- Produces: scoringApi（getRubric / parseRubric / saveRubric / rescore / getReport——rescore+report 供 Task 9 用）。

- [ ] **Step 1: 前端类型 + API client**

`frontend/src/api/scoring.ts`：

```ts
import client from './client'

export type RubricKind = 'quality' | 'content' | 'cert' | 'personnel' | 'performance' | 'price'

export interface RubricItem {
  id: string
  dimension: string
  name: string
  points: number
  kind: RubricKind
  criteria: string
  key_terms: string[]
}

export interface ScoringRubric {
  status: 'found' | 'none' | 'manual'
  method_name: string
  max_total: number
  applied: boolean
  raw_text: string
  items: RubricItem[]
}

export interface RubricCover {
  status: string
  applied: boolean
  missing: Array<{ dimension: string; name: string }>
}

export interface ScoringReportItem {
  id: string
  dimension: string
  name: string
  points_total: number
  points_obtained: number
  status: 'pass' | 'partial' | 'fail' | 'unscored'
  evidence: string
  gap: string
  suggestion: string
}

export interface ScoringReport {
  generated_at: string
  method_name: string
  max_total: number
  total: number
  scored_total: number
  unscored_note: string
  warnings?: string[]
  items: ScoringReportItem[]
}

export const scoringApi = {
  getRubric: async (projectId: string): Promise<ScoringRubric> =>
    (await client.get(`/bid/${projectId}/scoring-rubric`)).data.rubric,
  parseRubric: async (projectId: string, text: string): Promise<{ rubric: ScoringRubric; problems: string[] }> =>
    (await client.post(`/bid/${projectId}/scoring-rubric/parse`, { text })).data,
  saveRubric: async (projectId: string, rubric: ScoringRubric): Promise<{ rubric: ScoringRubric; problems: string[] }> =>
    (await client.put(`/bid/${projectId}/scoring-rubric`, { rubric })).data,
  rescore: async (projectId: string): Promise<ScoringReport> =>
    (await client.post(`/bid/${projectId}/score`)).data,
  getReport: async (projectId: string): Promise<ScoringReport | Record<string, never>> =>
    (await client.get(`/bid/${projectId}/scoring-report`)).data,
}
```

`frontend/src/api/outline.ts`：

```ts
import type { RubricCover } from './scoring'

export interface OutlineChapter {
  ...
  /** 评分指标自动补入的节点标记（「来自评标办法」徽标） */
  source?: string
  ...
}

export interface OutlineGetResponse {
  chapters: OutlineChapter[]
  rubric_cover?: RubricCover
}

export interface OutlineConfirmResponse {
  success: boolean
  chapters_count: number
  status: string
  /** 本次确认按评标办法自动补充的章节/小节标题 */
  added_from_rubric: string[]
}
```

- [ ] **Step 2: ScoringRubricPanel**

`frontend/src/components/ScoringRubric/ScoringRubricPanel.tsx`：

```tsx
import React, { useEffect, useState } from 'react'
import { Card, Button, Empty, Input, InputNumber, Table, Tag, Space, message as antMessage, Typography } from 'antd'
import { scoringApi, ScoringRubric } from '../../api/scoring'

const KIND_LABELS: Record<string, string> = {
  content: '内容', cert: '资质', personnel: '人员', performance: '业绩',
  quality: '质量', price: '报价',
}

interface Props {
  projectId: string
}

/** 评分办法面板：found → 可编辑指标列表；none → 粘贴入口；manual → 可编辑 */
const ScoringRubricPanel: React.FC<Props> = ({ projectId }) => {
  const [rubric, setRubric] = useState<ScoringRubric | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [parsing, setParsing] = useState(false)

  const load = () => {
    setLoading(true)
    scoringApi.getRubric(projectId).then(setRubric).catch(() => setRubric(null)).finally(() => setLoading(false))
  }
  useEffect(load, [projectId])

  const handleParse = async () => {
    if (!pasteText.trim()) return
    setParsing(true)
    try {
      const { rubric: next, problems } = await scoringApi.parseRubric(projectId, pasteText)
      setRubric(next)
      problems.forEach((p) => antMessage.warning(p))
      antMessage.success(next.status === 'found' ? '已提取评分指标，可核对修改' : '未检测到评分表，可手动填写')
    } catch (err: any) {
      antMessage.error(err?.response?.data?.detail || '解析失败')
    } finally {
      setParsing(false)
    }
  }

  const handleSave = async () => {
    if (!rubric) return
    setSaving(true)
    try {
      const { rubric: next, problems } = await scoringApi.saveRubric(projectId, rubric)
      setRubric(next)
      problems.forEach((p) => antMessage.warning(p))
      antMessage.success('评分指标已保存')
    } catch (err: any) {
      antMessage.error(err?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const updateItem = (index: number, patch: Partial<ScoringRubric['items'][number]>) => {
    if (!rubric) return
    setRubric({ ...rubric, items: rubric.items.map((it, i) => (i === index ? { ...it, ...patch } : it)) })
  }

  if (loading) return <Card size="small" loading />
  if (!rubric || rubric.status === 'none') {
    return (
      <Card size="small" title="评分办法" style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary">
          未检测到评标办法。可粘贴招标文件中的评分办法文本由 AI 提取，或直接手动填写评分指标。
        </Typography.Text>
        <Input.TextArea
          rows={4} value={pasteText} onChange={(e) => setPasteText(e.target.value)}
          placeholder={'粘贴评分表文本，例如：\n技术部分 45 分……'}
          style={{ margin: '8px 0' }}
        />
        <Button type="primary" size="small" loading={parsing} onClick={handleParse}>解析评分办法</Button>
      </Card>
    )
  }

  return (
    <Card size="small" title={`评分办法（${rubric.method_name || rubric.status}）`} style={{ marginBottom: 12 }}
      extra={<Button size="small" onClick={handleSave} loading={saving}>保存指标</Button>}>
      {rubric.items.length === 0 ? (
        <Empty description="暂无评分指标" />
      ) : (
        <Table
          size="small" rowKey="id" pagination={false} dataSource={rubric.items}
          columns={[
            { title: '维度', dataIndex: 'dimension', width: 100 },
            {
              title: '指标', dataIndex: 'name', render: (v, _, i) => (
                <Input size="small" value={v} onChange={(e) => updateItem(i, { name: e.target.value })} />
              ),
            },
            {
              title: '分值', dataIndex: 'points', width: 80, render: (v, _, i) => (
                <InputNumber size="small" min={0} value={v} onChange={(n) => updateItem(i, { points: n ?? 0 })} />
              ),
            },
            { title: '类型', dataIndex: 'kind', width: 80, render: (v: string) => <Tag>{KIND_LABELS[v] ?? v}</Tag> },
            {
              title: '给分标准', dataIndex: 'criteria', render: (v, _, i) => (
                <Input.TextArea size="small" rows={1} value={v} onChange={(e) => updateItem(i, { criteria: e.target.value })} />
              ),
            },
          ]}
        />
      )}
    </Card>
  )
}

export default ScoringRubricPanel
```

- [ ] **Step 3: OutlineConfirm 挂载面板 + 覆盖提示条**

`frontend/src/pages/project/OutlineConfirm.tsx`：

1) import：

```tsx
import ScoringRubricPanel from '../../components/ScoringRubric/ScoringRubricPanel'
import type { RubricCover } from '../../api/scoring'
```

2) state + load 捕获 rubric_cover：

```tsx
  const [rubricCover, setRubricCover] = useState<RubricCover | null>(null)
```

在 `outlineApi.get(id).then((res) => { setChapters(res.chapters); setRubricCover(res.rubric_cover ?? null) })`（handleReextract 同加）。

3) 渲染（在现有树上方插入）：

```tsx
      <ScoringRubricPanel projectId={id} />
      {rubricCover && rubricCover.missing.length > 0 && (
        <Card size="small" style={{ marginBottom: 12, borderColor: '#faad14' }}>
          <Tag color="gold">评标办法覆盖</Tag>
          确认目录时将自动补充以下缺失内容项（可改可删）：
          <Space wrap style={{ marginTop: 4 }}>
            {rubricCover.missing.map((m, i) => (
              <Tag key={i} color="gold">{m.name}</Tag>
            ))}
          </Space>
        </Card>
      )}
```

4) handleConfirm 成功 toast 展示自动补列表：

```tsx
      const res = await outlineApi.confirm(id)
      const msg = `已确认 ${res.chapters_count} 个章节，进入信息搜集阶段`
      antMessage.success(res.added_from_rubric?.length
        ? `${msg}；已按评标办法自动补充：${res.added_from_rubric.join('、')}`
        : msg)
```

- [ ] **Step 4: OutlineTree 徽标**

`frontend/src/components/OutlineEditor/OutlineTree.tsx`，title 渲染（:164-169 区域）在类型 Tag 后加：

```tsx
          {ch.source === 'scoring_rubric' && (
            <Tag color="gold" style={{ marginRight: 0, marginLeft: 6 }}>来自评标办法</Tag>
          )}
```

- [ ] **Step 5: 构建验证**

Run: `cd frontend && npm run build`
Expected: 编译通过（TS 无错）。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/scoring.ts frontend/src/api/outline.ts frontend/src/components/ScoringRubric/ScoringRubricPanel.tsx frontend/src/pages/project/OutlineConfirm.tsx frontend/src/components/OutlineEditor/OutlineTree.tsx
git commit -m "feat(前端): 评分办法面板（粘贴解析/编辑保存）+ 来自评标办法徽标 + 确认前覆盖提示"
```

---

### Task 9: 前端评分报告卡（SSE scoring_report + 重评按钮）

**Files:**
- Create: `frontend/src/components/ScoringReportCard.tsx`
- Modify: `frontend/src/pages/project/ProjectWorkflow.tsx`（SSE case `scoring_report`；进项目加载 GET /scoring-report；结果卡下方渲染 + 重新评分按钮）

**Interfaces:**
- Consumes: scoringApi（Task 8）、SSE `scoring_report` 事件（Task 6）。

- [ ] **Step 1: ScoringReportCard 组件**

`frontend/src/components/ScoringReportCard.tsx`：

```tsx
import React from 'react'
import { Card, Tag, Button, Space, Progress, Empty, Typography, Spin } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ScoringReport } from '../api/scoring'

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  pass: { color: 'green', label: '达标' },
  partial: { color: 'orange', label: '部分达标' },
  fail: { color: 'red', label: '未达标' },
  unscored: { color: 'default', label: '未计分' },
}

interface Props {
  report: ScoringReport | null
  loading?: boolean
  onRescore?: () => void
  rescoring?: boolean
}

const ScoringReportCard: React.FC<Props> = ({ report, loading, onRescore, rescoring }) => {
  if (loading) return <Card size="small" loading style={{ marginBottom: 12 }} />
  if (!report || !report.items?.length) return null
  const ratio = report.scored_total > 0 ? Math.round((report.total / report.scored_total) * 100) : 0
  return (
    <Card
      size="small"
      title={`自我评分：${report.method_name || '综合评分'}`}
      style={{ marginBottom: 12 }}
      extra={onRescore && (
        <Button size="small" icon={<ReloadOutlined />} loading={rescoring} onClick={onRescore}>
          重新评分
        </Button>
      )}
    >
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <Space align="center">
          <Progress type="circle" size={64} percent={ratio} format={(p) => `${p ?? 0}%`} />
          <Space direction="vertical" size={0}>
            <Typography.Text strong>
              {report.total} / {report.scored_total}（可评总分）
            </Typography.Text>
            {report.max_total > 0 && (
              <Typography.Text type="secondary">
                指标满分 {report.max_total} 分
              </Typography.Text>
            )}
            {report.unscored_note && (
              <Tag color="default" style={{ marginTop: 4 }}>{report.unscored_note}</Tag>
            )}
          </Space>
        </Space>
        {report.items.map((it) => {
          const tag = STATUS_TAG[it.status] ?? STATUS_TAG.unscored
          return (
            <Card key={it.id} size="small" type="inner" style={{ marginBottom: 0 }}>
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Space wrap>
                  <Tag color={tag.color}>{tag.label}</Tag>
                  <Typography.Text strong>{it.name}</Typography.Text>
                  <Typography.Text type="secondary">{it.dimension}</Typography.Text>
                </Space>
                <Typography.Text>
                  {it.points_obtained} / {it.points_total} 分
                </Typography.Text>
              </Space>
              {it.evidence && (
                <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 12 }}>
                  依据：{it.evidence}
                </Typography.Paragraph>
              )}
              {it.gap && (
                <Typography.Paragraph type="warning" style={{ margin: '4px 0 0', fontSize: 12 }}>
                  失分点：{it.gap}
                </Typography.Paragraph>
              )}
              {it.suggestion && (
                <Typography.Paragraph style={{ margin: '4px 0 0', fontSize: 12 }}>
                  建议：{it.suggestion}
                </Typography.Paragraph>
              )}
            </Card>
          )
        })}
      </Space>
    </Card>
  )
}

export default ScoringReportCard
```

- [ ] **Step 2: ProjectWorkflow 接 SSE + 加载 + 渲染**

`frontend/src/pages/project/ProjectWorkflow.tsx`：

1) import + state：

```tsx
import ScoringReportCard from '../../components/ScoringReportCard'
import { scoringApi, ScoringReport } from '../../api/scoring'
...
  const [scoringReport, setScoringReport] = useState<ScoringReport | null>(null)
  const [rescoring, setRescoring] = useState(false)
```

2) 进项目时加载（现有项目加载 useEffect 内，`GET /bid/{id}` 成功后追加）：

```tsx
      scoringApi.getReport(id).then((r) => {
        if (r.items?.length) setScoringReport(r)
      }).catch(() => {})
```

3) SSE case（在 `case 'format_verification':` 之后加）：

```tsx
                case 'scoring_report': {
                  try { setScoringReport(JSON.parse(e.data)) } catch { /* 忽略畸形事件 */ }
                  break
                }
```

4) 重新评分按钮处理：

```tsx
  const handleRescore = async () => {
    if (!id || rescoring) return
    setRescoring(true)
    try {
      const report = await scoringApi.rescore(id)
      setScoringReport(report)
      message.success('重新评分完成')
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '重新评分失败')
    } finally {
      setRescoring(false)
    }
  }
```

5) 渲染：在 format_verification 卡片（~:960）之后：

```tsx
      <ScoringReportCard
        report={scoringReport}
        onRescore={handleRescore}
        rescoring={rescoring}
      />
```

- [ ] **Step 3: 构建验证**

Run: `cd frontend && npm run build`
Expected: 编译通过。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/ScoringReportCard.tsx frontend/src/pages/project/ProjectWorkflow.tsx
git commit -m "feat(前端): 自我评分报告卡（SSE scoring_report + 重新评分 + 重进项目展示）"
```

---

### Task 10: E2E 冒烟扩展 + 全量回归

**Files:**
- Modify: `backend/scripts/e2e_smoke.py`（预置评分指标 → confirm 断言自动补 → 生成断言收到 scoring_report 事件且字段完整）
- 回归：`backend` 全量 pytest + `frontend npm run build`

**Interfaces:**
- Consumes: Task 4 的 confirm `added_from_rubric`、Task 6 的 SSE `scoring_report`、Task 8/9 前端。

- [ ] **Step 1: 扩展 e2e_smoke.py**

e2e_smoke 的现实：`main()` **不调** extract-chapters / outline/confirm —— 直接 `db_seed(project_id)`（SQLAlchemy 写库）后 `POST /bid/generate` 流式收 SSE。因此本任务沿用该结构：**主流程靠 `db_seed` 预置评分指标**（确定性，绕过 AI 提取）；confirm 自动补的场景拆成独立一次性项目（PUT 真实端点 → confirm → 断言）。

**(a) 模块级预置指标 + `db_seed` 扩展**（`db_seed` 定义于 :240 附近，签名改带关键字参数；`proj.status = "draft"` 改为参数化）：

```python
RUBRIC_SEED = {
    "status": "manual",
    "method_name": "综合评分法",
    "max_total": 20,
    "applied": False,
    "raw_text": "",
    "items": [
        {
            "id": "tech-x", "dimension": "技术部分", "name": "售后服务方案",
            "points": 10, "kind": "content", "criteria": "售后响应及时",
            "key_terms": ["售后服务"],   # 技术部分章节标题不含该词 → gap_detect 判为缺失
        },
        {
            "id": "tech-q", "dimension": "技术部分", "name": "方案的针对性",
            "points": 10, "kind": "quality", "criteria": "贴合项目实际",
            "key_terms": ["针对性"],     # quality 不参与目录补全，只喂评分
        },
    ],
}


async def db_seed(project_id: str, *, status: str = "draft", seed_rubric: bool = True):
    """写 project 字段 + 章节（含 children_json 嵌套树）."""
    ...
        proj.target_pages = TARGET_PAGES
        if seed_rubric:
            proj.scoring_rubric_json = json.dumps(RUBRIC_SEED, ensure_ascii=False)
        proj.status = status
```

主流程 `await db_seed(project_id)` 调用处保持不变（默认参数生效）。生成结束时评分阶段读 `scoring_rubric_json`（status=manual + 2 items）→ 发出 `scoring_report` 事件。

**(b) `main()` 生成 SSE 断言**：现有事件清单（:406-407 的 `for name in ("status", ..., "done")` 元组）加入 `"scoring_report"`；在 format_verification 断言块（:430-433）后追加：

```python
    scoring = next((d for ev, d in events if ev == "scoring_report"), None)
    if scoring:
        checks.append(("scoring_report 总分字段完整", isinstance(scoring.get("total"), (int, float))))
        checks.append(("scoring_report 逐项字段完整", bool(scoring.get("items"))))
        print(f"  自我评分: total={scoring.get('total')} scored_total={scoring.get('scored_total')} "
              f"({len(scoring.get('items') or [])} 项)")
    else:
        checks.append(("scoring_report 发出", False))
```

**(c) 新增模块级 `confirm_gap_smoke`**（confirm 自动补 + source 标记的真实路径，独立一次性项目，避免与主项目生成流相互干扰）：

```python
async def confirm_gap_smoke(headers: dict) -> list[tuple[str, bool]]:
    """mini 场景：预置评分指标 → outline/confirm 自动补节点 → GET /chapters 断言 source 标记."""
    checks: list[tuple[str, bool]] = []
    pid = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
            r = await client.post(f"{API_BASE}/projects", headers=headers,
                                  json={"name": TEST_NAME + "-confirm"})
            r.raise_for_status()
            pid = r.json()["id"]
            await db_seed(pid, status="structure_ready", seed_rubric=False)  # 走真实 PUT 预置
            pr = await client.put(f"{API_BASE}/bid/{pid}/scoring-rubric",
                                  headers=headers, json={"rubric": RUBRIC_SEED})
            checks.append(("PUT /scoring-rubric 预置成功", pr.status_code == 200))
            cr = await client.post(f"{API_BASE}/bid/{pid}/outline/confirm", headers=headers)
            body = cr.json() if cr.status_code == 200 else {}
            checks.append(("confirm 成功", cr.status_code == 200 and body.get("success")))
            added = body.get("added_from_rubric") or []
            checks.append(("按评标办法自动补充节点", len(added) >= 1))
            print(f"  confirm 场景: 自动补充 {added}")

            gr = await client.get(f"{API_BASE}/bid/{pid}/chapters", headers=headers)
            flat: list[dict] = []

            def walk(nodes):
                for n in nodes or []:
                    if isinstance(n, dict):
                        flat.append(n)
                        walk(n.get("children"))

            walk((gr.json() or {}).get("chapters") or [])
            checks.append(("补入节点带 source=scoring_rubric",
                           any(n.get("source") == "scoring_rubric" for n in flat)))
            print(f"  confirm 场景: GET /chapters 含 source=scoring_rubric 节点: "
                  f"{any(n.get('source') == 'scoring_rubric' for n in flat)}")
    finally:
        if pid:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
                    await client.delete(f"{API_BASE}/projects/{pid}", headers=headers)
            except Exception:
                pass
    return checks
```

在 `main()` 的报告块（:607 的 `# ---- 报告 ----` 之前、配对学习块之后）调用：

```python
    # ---- mini 场景：评标办法驱动目录补全（confirm 自动补，独立项目）----
    checks += await confirm_gap_smoke(headers)
```

- [ ] **Step 2: 本地容器内冒烟**

Run（容器在线的开发环境；若本地无容器则记录为「部署后容器内执行」，见 Step 4 备注）:
`docker exec -w /app -e PYTHONPATH=/app hongxi-backend python scripts/e2e_smoke.py`
Expected: 新增 checks 全 ✅；既有 checks 不回退。

- [ ] **Step 3: 全量回归**

Run: `cd backend && python -m pytest -q`
Expected: 158+ 现有 + 新增全绿（已知 Windows 编码失败项 `test_format_template_to_prompt_text` 忽略）。
Run: `cd frontend && npm run build`
Expected: clean。

- [ ] **Step 4: 提交**

```bash
git add backend/scripts/e2e_smoke.py
git commit -m "test(e2e): 评分指标预置 → confirm 自动补 → scoring_report 事件断言"
```

---

## 附录：部署清单（实现 + 测试全绿后单独执行）

沿用 `memory/server-deployment-config` 已验证流程，**差异点：本次迁移是「给已有表加列」→ 必须跑 `alembic upgrade head`（不是 stamp）**。

1. 全量回归绿（pytest + npm build）。
2. 备份：`/hxbid/hongxi-bid/.deploy-backup-<ts>/`（db 导出 + 旧包）。
3. 打包 backend tar（**排除 `app/config.py`**，服务器本地 flash 配置保留）+ frontend src。
4. `pscp` 上传 → 解压 → `docker compose up -d --build backend`（Dockerfile COPY . .，改 app 代码必须重建）。
5. 容器内迁移：`docker exec hongxi-backend alembic upgrade head`（追加两列；**加列走 upgrade**，create_all 在已有表上不建新列——spec §4.3/§12）。
6. 验证：401/200、前端新 bundle 串、`psql -U hongxi -d hongxi_bid -c "\d bid_projects"` 见两列、日志无异常。
7. 容器内 E2E 冒烟 ×3（`scripts/run_e2e_x3.sh` 模式）全绿。
8. 回滚预案：内置列迁移 DROP COLUMN 即可（0007 downgrade），备份在 `.deploy-backup-<ts>/`。