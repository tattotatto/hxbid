# 章节结构可编辑 + 招标原文精确匹配 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户在「目录确认」页能拖拽排序、改标题、给每章指定类型（固定格式/表格/附件/AI 生成），选定固定格式或表格时按标题在招标原文中精确定位并把匹配结果固化，生成阶段据此照抄原文而非 AI 编造。

**Architecture:** 新增一个纯函数匹配器 `tender_section_matcher.py`（标题枚举 → 打分 → 定档 → 层级感知截取），它同时被「结构页实时试匹配」和「生成阶段回填」两条链路复用。匹配结果（源文本区间 + 命中标题 + 语料 hash）随章节节点存进已有的 `chapter_structure_json`，生成时直接按区间切片，不再按标题重猜。前端在 antd `Tree` 原生拖拽之上补齐编辑能力，所有编辑经新增的 `PUT chapter-structure` 落库。

**Tech Stack:** 后端 FastAPI + SQLAlchemy(async) + pytest；前端 React 18 + TypeScript + antd 5.20 + Vite；无新增第三方依赖。

**Spec:** `docs/superpowers/specs/2026-09-27-chapter-structure-editing-and-tender-matching-design.md`

## Global Constraints

- **无 schema 变更、无 alembic 迁移。** `match` / `attachments` 一律放进已有的 `chapter_structure_json`；物化后放进已有的 `ProjectChapter.chapter_meta_json`；全文路径放进已有的 `parsed_requirements_json`。
- **测试运行器：** 在 `D:\2026\投标软件\backend` 下执行 `venv/Scripts/python.exe -m pytest tests/<file>::<test> -v`。本机默认 `python`（miniconda）**没有 pytest**，必须用 venv 里的解释器。
- **匹配阈值（初值，由测试钉死）：** `MATCH_THRESHOLD = 0.75`、`STRONG_THRESHOLD = 0.9`、`AMBIGUITY_GAP = 0.15`、`MAX_CANDIDATES = 8`。
- **用户可选类型只有四类：** `fixed_form` / `table` / `attachment` / `ai_generated`。`mixed` 由 AI 提取产生，保留展示但**不可选**。
- **能力只在「目录确认」之前生效。** `PUT chapter-structure` 与 `POST match` 对 `status not in ("draft", "structure_ready")` 的项目返回 409。
- **附件上传白名单：** `png` / `jpg` / `jpeg` / `pdf`。其余 400。
- **不引第三方拖拽库**，用 antd `Tree` 原生 `draggable` + `onDrop`。
- **老项目无 `match` 字段时兜底为 `{"status": "na"}`、`attachments` 兜底为 `[]`**，不报错、不迁移历史数据。
- **失败不静默：** 所有降级/兜底一律写进 `format_verification_json`（`warn` 或 `error`）。
- **不破坏开标一览表特例：** `extract_bid_opening_data` / `build_bid_opening_table` 及其重复渲染跳过逻辑保持原样。
- **中文注释**，与仓库现有风格一致。

## Review Focus

这些是 spec 暗示、但任何单个任务的测试都不会自然覆盖的输入与失效模式，最可能咬到真实用户。每一条都在下面它归属的任务里有一个测试钉住。

1. **招标文件解析不出格式章节**（扫描件 PDF / `locate_format_pages` 失败）→ `format_section_text` 为空。此时所有固定格式章节应得到 `missing` 且**用户在结构页看到明确的红叉原因**，而不是满屏红叉却无解释，也不是静默退回 AI。
2. **同一标题在文档中出现多次**（目录条目 + 正文小节 + 附录里的同名行）→ 必须跳过目录条目（标题后只有页码）取正文那一处，不能取到第一个。
3. **用户把 `ai_generated` 章节改成 `fixed_form`，但招标文件里根本没有这一节** → 应得到 `missing` 并在结构页显示红叉徽标，不能装作匹配成功。**确认时不拦截**（用户 2026-09-27 选择静默放过），所以徽标是唯一的告知渠道，视觉上必须够重。
4. **附件引用的文件在生成前被删掉或移动**（用户清理 uploads）→ 渲染时应跳过该张并记 `warn`，**不能让整个导出崩掉**。
5. **用户从 Word 粘贴标题**，带全角空格 / 全角括号 / 手写编号（如「　（一）投标函　」）→ 归一化后仍应匹配上招标原文里的「一、投标函」。

---

### Task 1: 匹配器 — 标题枚举与层级

**Files:**
- Create: `backend/app/services/tender_section_matcher.py`
- Test: `backend/tests/test_tender_section_matcher.py`

**Interfaces:**
- Consumes: 无（本任务是全计划的地基）
- Produces:
  - `SectionHeader` frozen dataclass：`title: str`、`raw: str`、`start: int`、`content_start: int`、`level: int`
  - `enumerate_headers(text: str) -> list[SectionHeader]`
  - `normalize_title(s: str) -> str`
  - 模块常量 `MATCH_THRESHOLD = 0.75`、`STRONG_THRESHOLD = 0.9`、`AMBIGUITY_GAP = 0.15`、`MAX_CANDIDATES = 8`

- [ ] **Step 1: Write the failing tests**

创建 `backend/tests/test_tender_section_matcher.py`：

```python
"""宏曦标书 - 招标原文小节匹配器 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.tender_section_matcher import (
    enumerate_headers,
    normalize_title,
)


class TestNormalizeTitle:
    def test_strips_numbering_prefix(self):
        assert normalize_title("二、投标函") == "投标函"

    def test_strips_parenthesised_numbering(self):
        assert normalize_title("（一）投标函") == "投标函"

    def test_strips_multilevel_numbering(self):
        assert normalize_title("1.1 项目概况") == "项目概况"

    def test_fullwidth_and_whitespace(self):
        # 用户从 Word 粘贴：全角空格 + 全角括号
        assert normalize_title("　（一）投标函　") == "投标函"

    def test_plain_title_untouched(self):
        assert normalize_title("投标函") == "投标函"

    def test_empty(self):
        assert normalize_title("") == ""


class TestEnumerateHeaders:
    def test_all_numbering_styles_get_expected_levels(self):
        text = "\n".join([
            "第一节 投标文件格式",      # level 1
            "一、投标函",               # level 1
            "（一）投标函附录",          # level 2
            "(二)开标一览表",           # level 2
            "1、分项报价表",            # level 3
            "1.1 分项明细",             # level 4
        ])
        headers = enumerate_headers(text)
        got = [(h.title, h.level) for h in headers]
        assert got == [
            ("投标文件格式", 1),
            ("投标函", 1),
            ("投标函附录", 2),
            ("开标一览表", 2),
            ("分项报价表", 3),
            ("分项明细", 4),
        ]

    def test_multilevel_numbering_wins_over_single_level(self):
        # 「1.1 项目概况」同时匹配 level-4 与 level-3 两条正则，
        # 必须先判 4（更具体），否则层级会被误判成 3。
        headers = enumerate_headers("1.1 项目概况")
        assert len(headers) == 1
        assert headers[0].level == 4
        assert headers[0].title == "项目概况"

    def test_start_offsets_are_real(self):
        text = "一、投标函\n正文\n二、承诺书\n正文2"
        headers = enumerate_headers(text)
        assert [h.start for h in headers] == [0, text.index("二、承诺书")]
        assert text[headers[1].start:].startswith("二、承诺书")

    def test_empty_text_returns_empty(self):
        assert enumerate_headers("") == []
        assert enumerate_headers(None) == []

    def test_long_line_is_not_a_header(self):
        # 标题行文字上限 80 字符 —— 超长的是正文，不是标题
        long_body = "一、" + "字" * 90
        assert enumerate_headers(long_body) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_tender_section_matcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tender_section_matcher'`

- [ ] **Step 3: Write minimal implementation**

创建 `backend/app/services/tender_section_matcher.py`：

```python
"""宏曦标书 - 招标原文小节匹配器.

按用户输入的标题在招标文件原文中精确定位一个小节，供「目录确认页」实时
预览与生成阶段的原文回填共用。设计见
docs/superpowers/specs/2026-09-27-chapter-structure-editing-and-tender-matching-design.md

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 打分档位。0.75 以下不计候选；最高分 ≥ 0.9 且与次高分拉开 0.15 才算「确定命中」，
# 否则进「多候选」让用户挑。
MATCH_THRESHOLD = 0.75
STRONG_THRESHOLD = 0.9
AMBIGUITY_GAP = 0.15
MAX_CANDIDATES = 8

# 标题行正则：**(层级, 模式)**，**顺序即优先级**——多级阿拉伯数字（1.1）必须先于
# 单级（1.）尝试，否则「1.1 项目概况」会被判成 level 3。同一位置只取首个命中。
_HEADER_PATTERNS: list[tuple[int, re.Pattern[str]]] = [
    (1, re.compile(
        r"^[ \t]*第\s*[一二三四五六七八九十百]{1,4}\s*[章节][ \t　]*([^\n]{1,80})$",
        re.MULTILINE,
    )),
    (1, re.compile(
        r"^[ \t]*[一二三四五六七八九十]{1,3}\s*[、．\.][ \t　]*([^\n]{1,80})$",
        re.MULTILINE,
    )),
    (2, re.compile(
        r"^[ \t]*[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)][ \t　]*([^\n]{1,80})$",
        re.MULTILINE,
    )),
    (4, re.compile(
        r"^[ \t]*\d{1,2}(?:\.\d{1,2}){1,3}[ \t　]+([^\n]{1,80})$",
        re.MULTILINE,
    )),
    (3, re.compile(
        r"^[ \t]*\d{1,2}\s*[、．\.][ \t　]*([^\n]{1,80})$",
        re.MULTILINE,
    )),
]

# 全角 → 半角（数字、括号、句点、顿号、全角空格）
_FULLWIDTH_MAP = str.maketrans(
    "０１２３４５６７８９（）．、　",
    "0123456789().、 ",
)

# 标题行前的编号前缀，归一化时剥掉
_TITLE_NUM_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[一二三四五六七八九十百]{1,4}\s*[章节]"
    r"|[一二三四五六七八九十]{1,3}\s*[、．\.]"
    r"|[（(]\s*[一二三四五六七八九十]{1,3}\s*[）)]"
    r"|\d{1,2}(?:\.\d{1,2}){0,3}\s*[、．\.]?"
    r")\s*"
)


@dataclass(frozen=True)
class SectionHeader:
    """一个标题行及其在语料中的位置."""

    title: str          # 标题文字（不含编号），已 strip
    raw: str            # 标题整行原文（含编号）
    start: int          # 标题行起始 offset
    content_start: int  # 标题行之后的 offset（正文起点候选）
    level: int          # 1=章/一、  2=（一）  3=1、  4=1.1


def normalize_title(s: str) -> str:
    """归一化标题：全角转半角、剥编号前缀、去所有空白、转小写.

    用户标题常从 Word 粘贴带全角空格与手写编号，招标原文的标题行也带编号，
    两边都剥掉才能等价比较。
    """
    if not s:
        return ""
    s = s.translate(_FULLWIDTH_MAP)
    s = _TITLE_NUM_PREFIX_RE.sub("", s)
    return re.sub(r"\s+", "", s).strip().lower()


def enumerate_headers(text: str) -> list[SectionHeader]:
    """扫出全部标题行，按位置排序.

    同一位置只保留**首个命中**的层级——`_HEADER_PATTERNS` 已按"更具体优先"
    排好序，先到先得即可。
    """
    if not text:
        return []
    found: dict[int, SectionHeader] = {}
    for level, pattern in _HEADER_PATTERNS:
        for m in pattern.finditer(text):
            start = m.start()
            if start in found:
                continue
            found[start] = SectionHeader(
                title=m.group(1).strip(),
                raw=m.group(0).strip(),
                start=start,
                content_start=m.end(),
                level=level,
            )
    return [found[k] for k in sorted(found)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_tender_section_matcher.py -v`
Expected: PASS（12 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tender_section_matcher.py backend/tests/test_tender_section_matcher.py
git commit -m "feat: 招标小节匹配器 — 标题枚举与层级识别"
```

---

### Task 2: 匹配器 — 打分、定档与层级感知截取

**Files:**
- Modify: `backend/app/services/tender_section_matcher.py`
- Test: `backend/tests/test_tender_section_matcher.py`

**Interfaces:**
- Consumes: Task 1 的 `SectionHeader` / `enumerate_headers` / `normalize_title`
- Produces:
  - `MatchCandidate` frozen dataclass：`title`、`raw`、`level`、`start`、`end`、`score`、`page: int | None`
  - `section_body(text: str, headers: list[SectionHeader], idx: int) -> str`
  - `score_title(target_norm: str, cand_norm: str) -> float`
  - `classify(scored: list[MatchCandidate]) -> str`（返回 `"matched"` / `"ambiguous"`）

- [ ] **Step 1: Write the failing tests**

追加到 `backend/tests/test_tender_section_matcher.py`：

```python
from app.services.tender_section_matcher import (
    MatchCandidate,
    classify,
    score_title,
    section_body,
)

# 一份刻意混入目录条目、子级标题与同名正文的语料
SAMPLE = """目录
一、投标函 ................................ -32-
二、开标一览表 ............................ -33-

一、投标函

致：某某单位
我方已仔细阅读并充分理解贵方招标文件的全部内容。

投标人：（公章）
日期：  年  月  日

（一）投标函附录

附录内容一
附录内容二

二、开标一览表

序号 | 服务内容 | 报价
"""


class TestScoreTitle:
    def test_exact_is_one(self):
        assert score_title("投标函", "投标函") == 1.0

    def test_substring_is_scaled(self):
        # 「投标函」是「投标函附录」的子串 → 0.8 × 短/长
        s = score_title("投标函", "投标函附录")
        assert 0.4 < s < 0.8

    def test_unrelated_is_below_threshold(self):
        assert score_title("投标函", "应急预案") < 0.75

    def test_empty_inputs(self):
        assert score_title("", "投标函") == 0.0
        assert score_title("投标函", "") == 0.0


class TestSectionBody:
    def test_sub_level_heading_does_not_truncate(self):
        """关键修正：`（一）投标函附录` 是子级，不得截断 `一、投标函` 的正文。"""
        headers = enumerate_headers(SAMPLE)
        idx = next(i for i, h in enumerate(headers)
                   if h.title == "投标函" and "致：" in section_body(SAMPLE, headers, i))
        body = section_body(SAMPLE, headers, idx)
        assert "日期：  年  月  日" in body
        assert "（一）投标函附录" in body, "子级标题应当留在父级正文里"
        assert "二、开标一览表" not in body, "同级标题必须截断"

    def test_next_same_level_heading_truncates(self):
        headers = enumerate_headers(SAMPLE)
        idx = next(i for i, h in enumerate(headers)
                   if h.title == "开标一览表" and "序号" in section_body(SAMPLE, headers, i))
        body = section_body(SAMPLE, headers, idx)
        assert "序号" in body

    def test_last_section_runs_to_end(self):
        headers = enumerate_headers(SAMPLE)
        body = section_body(SAMPLE, headers, len(headers) - 1)
        assert body.strip().endswith("报价")


class TestClassify:
    def _c(self, score):
        return MatchCandidate(title="t", raw="t", level=1, start=0, end=1, score=score)

    def test_clear_winner_is_matched(self):
        assert classify([self._c(1.0), self._c(0.5)]) == "ambiguous"
        assert classify([self._c(1.0)]) == "matched"

    def test_close_scores_are_ambiguous(self):
        assert classify([self._c(0.95), self._c(0.9)]) == "ambiguous"

    def test_two_exact_matches_are_ambiguous(self):
        """同名小节出现两次（正文 + 附录）→ 必须让用户挑，不能随便取一个。"""
        assert classify([self._c(1.0), self._c(1.0)]) == "ambiguous"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_tender_section_matcher.py -v`
Expected: FAIL — `ImportError: cannot import name 'MatchCandidate'`

- [ ] **Step 3: Write minimal implementation**

追加到 `backend/app/services/tender_section_matcher.py`（放在 `enumerate_headers` 之后）：

```python
# 独占一行的页码，如「-72-」「72」「— 72 —」。用于判断标题行后面到底有没有
# 正文——目录条目后面只剩页码。
_PAGE_NUMBER_LINE_RE = re.compile(r"^[\s\-—–]*\d+[\s\-—–]*$", re.MULTILINE)


@dataclass(frozen=True)
class MatchCandidate:
    """一个候选匹配结果."""

    title: str
    raw: str
    level: int
    start: int          # 标题行起始 offset
    end: int            # 本节结束 offset（下一个同级/更高级标题前）
    score: float
    page: int | None = None


def section_end(headers: list[SectionHeader], idx: int, text_len: int) -> int:
    """本节结束位置：下一个**层级 ≤ 本级**的标题行起点；没有则到语料末尾.

    这是对旧实现 ``extract_fixed_form_section`` 的实质修正——旧版碰到任何
    「一、」就停，遇到 `（一）` 这类子级标题反而不会截断，而遇到正确边界
    之外的同级标题又会截错。
    """
    cur_level = headers[idx].level
    for nxt in headers[idx + 1:]:
        if nxt.level <= cur_level:
            return nxt.start
    return text_len


def section_body(text: str, headers: list[SectionHeader], idx: int) -> str:
    """取第 idx 个标题行之后、到本节结束之间的正文（不含标题行本身）."""
    return text[headers[idx].content_start:section_end(headers, idx, len(text))]


def _bigrams(s: str) -> set[str]:
    if not s:
        return set()
    if len(s) < 2:
        return {s}
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _bigram_dice(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def score_title(target_norm: str, cand_norm: str) -> float:
    """按归一化标题打分：完全相等 1.0；互为子串 0.8×长度比；否则 bigram Dice.

    打分要**保守**——宁可判 ambiguous 让用户挑，也不要把不相干的章节当成命中，
    因为命中的后果是"照抄这段原文进标书"。
    """
    if not target_norm or not cand_norm:
        return 0.0
    if target_norm == cand_norm:
        return 1.0
    if target_norm in cand_norm or cand_norm in target_norm:
        shorter, longer = sorted((target_norm, cand_norm), key=len)
        return 0.8 * (len(shorter) / len(longer))
    return 0.6 * _bigram_dice(target_norm, cand_norm)


def has_body(text: str, headers: list[SectionHeader], idx: int) -> bool:
    """剔掉独占行的页码后是否还有正文（目录条目后面只剩页码）."""
    raw = section_body(text, headers, idx)
    return bool(_PAGE_NUMBER_LINE_RE.sub("", raw).strip())


def classify(scored: list[MatchCandidate]) -> str:
    """把打分结果定档为 matched / ambiguous.

    ``scored`` 必须已按分数降序排好。最高分 ≥ STRONG_THRESHOLD 且与次高分
    拉开 AMBIGUITY_GAP 才算确定命中；否则交给用户挑。
    """
    if not scored:
        return "missing"
    top = scored[0].score
    if top >= STRONG_THRESHOLD:
        runner = scored[1].score if len(scored) > 1 else 0.0
        if top - runner >= AMBIGUITY_GAP:
            return "matched"
    return "ambiguous"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_tender_section_matcher.py -v`
Expected: PASS（23 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tender_section_matcher.py backend/tests/test_tender_section_matcher.py
git commit -m "feat: 匹配器打分与层级感知截取 — 子级标题不再截断父级正文"
```

---

### Task 3: 匹配器 — 全文兜底、表格匹配、corpus_hash、页码映射

`extract_clean_text_from_pages` 用 `"\n\n"` 拼页且**跳过空页**，所以页码无法从文本里反推。本任务加一个返回页偏移映射的兄弟函数（原函数改为委托它，行为不变），让匹配结果能报出真实的「第 N 页」。

**Files:**
- Modify: `backend/app/services/tender_section_matcher.py`
- Modify: `backend/app/services/pdf_extractor.py`（新增 `extract_clean_text_with_page_map`；`extract_clean_text_from_pages` 改为委托；`extract_format_section` 带出 `page_map`）
- Modify: `backend/app/api/bid.py:137-144`（存 `format_page_map`）
- Test: `backend/tests/test_tender_section_matcher.py`、`backend/tests/test_pdf_extractor.py`

**Interfaces:**
- Consumes: Task 2 的 `MatchCandidate` / `score_title` / `section_body` / `has_body` / `classify` / `section_end`
- Produces:
  - `MatchResult` dataclass：`status: str`、`source: str | None`、`best: MatchCandidate | None`、`candidates: list[MatchCandidate]`、`table_index: int | None`、`corpus_hash: str | None`
  - `corpus_hash(text: str) -> str`（返回 `"sha1:<hex>"`）
  - `page_at(page_map: list[dict] | None, offset: int) -> int | None`
  - `match_tender_section(title, *, chapter_type, format_section_text, full_text, format_tables, format_page_map) -> MatchResult`
  - `pdf_extractor.extract_clean_text_with_page_map(pdf, start, end) -> tuple[str, list[dict]]`（`[{"start": int, "page": int}]`，page 为 1-indexed）
  - `requirements["format_page_map"]`（`bid.py` 写入）

- [ ] **Step 1: Write the failing pdf_extractor test**

追加到 `backend/tests/test_pdf_extractor.py`。用假 PDF 对象 + monkeypatch 掉行清洗，只验证**页偏移映射本身**的算术——不必依赖真实 PDF 与 `_clean_page_lines` 的内部行为：

```python
class _FakePage:
    def __init__(self, lines):
        self._lines = lines

    def extract_text_lines(self):
        return [
            {"text": t, "x1": 100.0, "top": i * 10.0, "bottom": i * 10.0 + 8.0}
            for i, t in enumerate(self._lines)
        ]

    def find_tables(self):
        return []


class _FakePDF:
    def __init__(self, pages):
        self.pages = [_FakePage(p) for p in pages]


class TestCleanTextWithPageMap:
    def _patch_clean(self, monkeypatch):
        from app.services import pdf_extractor
        monkeypatch.setattr(
            pdf_extractor,
            "_clean_page_lines",
            lambda page, lines, boxes, right_edge: [ln["text"] for ln in lines],
        )

    def test_page_map_offsets_address_the_joined_text(self, monkeypatch):
        from app.services.pdf_extractor import extract_clean_text_with_page_map

        self._patch_clean(monkeypatch)
        text, page_map = extract_clean_text_with_page_map(
            _FakePDF([["第一页内容"], ["第二页内容"]]), 0, 1,
        )

        assert len(page_map) == 2
        assert page_map[0] == {"start": 0, "page": 1}
        assert text[page_map[0]["start"]:].startswith("第一页内容")
        assert text[page_map[1]["start"]:].startswith("第二页内容")

    def test_blank_page_is_skipped_but_page_numbers_stay_true(self, monkeypatch):
        """空页不产出文本，但后面页的页码必须仍是它的**真实页码**，不是序号。"""
        from app.services.pdf_extractor import extract_clean_text_with_page_map

        self._patch_clean(monkeypatch)
        text, page_map = extract_clean_text_with_page_map(
            _FakePDF([["第一页内容"], [], ["第三页内容"]]), 0, 2,
        )

        assert len(page_map) == 2
        assert page_map[0]["page"] == 1
        assert page_map[1]["page"] == 3, "跳过的空页不能把页码压成 2"
        assert text[page_map[1]["start"]:].startswith("第三页内容")

    def test_original_function_still_returns_plain_text(self, monkeypatch):
        """委托不能改变原函数的返回类型与内容。"""
        from app.services.pdf_extractor import extract_clean_text_from_pages

        self._patch_clean(monkeypatch)
        text = extract_clean_text_from_pages(_FakePDF([["甲"], ["乙"]]), 0, 1)
        assert text == "甲\n\n乙"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_pdf_extractor.py -k PageMap -v`
Expected: FAIL — `ImportError: cannot import name 'extract_clean_text_with_page_map'`

- [ ] **Step 3: Implement the page map in pdf_extractor**

在 `backend/app/services/pdf_extractor.py` 中，把现有 `extract_clean_text_from_pages`（约 361-410 行）改为委托新函数：

```python
def extract_clean_text_from_pages(pdf, start: int, end: int) -> str:
    """提取指定页码范围文本，按行几何还原被排版拆开的段落.

    （保留原 docstring 全文不变）
    """
    text, _page_map = extract_clean_text_with_page_map(pdf, start, end)
    return text


def extract_clean_text_with_page_map(
    pdf, start: int, end: int,
) -> tuple[str, list[dict]]:
    """同 extract_clean_text_from_pages，但额外返回每页在合并文本中的起始偏移.

    页与页以 ``"\\n\\n"`` 相连，且空页不产出文本（原实现行为，必须保持不变），
    所以页码**无法**从合并文本里数分隔符反推。需要报「第 N 页」的调用方
    （如章节匹配器）必须靠这份映射。

    Returns:
        (合并后的文本, [{"start": int, "page": int}, ...])，page 为 1-indexed，
        且只包含真正产出文本的页。
    """
    pages = []
    for i in range(start, end + 1):
        if i >= len(pdf.pages):
            break
        page = pdf.pages[i]
        lines = page.extract_text_lines()
        if not lines:
            continue
        pages.append((i, page, lines, [t.bbox for t in page.find_tables()]))

    edge_candidates = [
        (ln.get("x1") or 0.0)
        for _, _, lines, boxes in pages
        for ln in lines
        if not _in_table(ln, boxes)
    ] or [
        (ln.get("x1") or 0.0) for _, _, lines, _ in pages for ln in lines
    ]
    right_edge = max(edge_candidates) if edge_candidates else 0.0

    parts: list[str] = []
    page_map: list[dict] = []
    cursor = 0
    for page_no, page, lines, boxes in pages:
        cleaned = _clean_page_lines(page, lines, boxes, right_edge)
        if not cleaned:
            continue
        chunk = "\n".join(cleaned)
        page_map.append({"start": cursor, "page": page_no + 1})
        parts.append(chunk)
        cursor += len(chunk) + 2  # 与 "\n\n".join 的间隔一致
    return "\n\n".join(parts), page_map
```

在 `extract_format_section` 里改用带映射的版本并带出：

```python
        full_text, page_map = extract_clean_text_with_page_map(pdf, start, end)
        tables = extract_tables_from_pages(pdf, start, end)

        result = {
            "full_text": full_text,
            "tables": tables,
            "page_map": page_map,
            "start_page": start + 1,
            "end_page": end + 1,
            "total_pages": len(pdf.pages),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/test_pdf_extractor.py tests/test_format_integration.py -v`
Expected: PASS（含全部既有用例，确认委托没改变原行为）

- [ ] **Step 5: Write the failing matcher tests**

追加到 `backend/tests/test_tender_section_matcher.py`：

```python
from app.services.tender_section_matcher import (
    MatchResult,
    corpus_hash,
    match_tender_section,
    page_at,
)

FORMAT_SECTION = """一、投标函

致：某某单位
我方已仔细阅读。

二、开标一览表

序号 | 服务内容 | 报价
"""


class TestPageAt:
    def test_resolves_offset_to_page(self):
        pmap = [{"start": 0, "page": 32}, {"start": 100, "page": 33}]
        assert page_at(pmap, 0) == 32
        assert page_at(pmap, 99) == 32
        assert page_at(pmap, 100) == 33
        assert page_at(pmap, 500) == 33

    def test_no_map_returns_none(self):
        assert page_at(None, 10) is None
        assert page_at([], 10) is None


class TestCorpusHash:
    def test_stable_and_prefixed(self):
        h = corpus_hash("abc")
        assert h.startswith("sha1:")
        assert h == corpus_hash("abc")

    def test_changes_with_content(self):
        assert corpus_hash("abc") != corpus_hash("abd")

    def test_empty_text_still_hashes(self):
        assert corpus_hash("") == corpus_hash("")


class TestMatchTenderSection:
    def test_exact_match_returns_offsets_and_hash(self):
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "matched"
        assert res.source == "format_section"
        assert res.best is not None
        assert FORMAT_SECTION[res.best.start:res.best.end].startswith("一、投标函")
        assert "我方已仔细阅读" in FORMAT_SECTION[res.best.start:res.best.end]
        assert res.corpus_hash == corpus_hash(FORMAT_SECTION)

    def test_ai_generated_is_not_applicable(self):
        res = match_tender_section(
            "服务方案", chapter_type="ai_generated",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "na"
        assert res.best is None

    def test_missing_when_title_absent(self):
        res = match_tender_section(
            "根本不存在的一节", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "missing"
        assert res.best is None

    def test_missing_when_corpus_absent(self):
        """场景：招标文件解析不出格式章节（扫描件 PDF）。"""
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text=None, full_text=None,
        )
        assert res.status == "missing"
        assert res.source is None

    def test_empty_title_is_missing(self):
        res = match_tender_section(
            "   ", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "missing"

    def test_falls_back_to_full_text(self):
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text="", full_text=FORMAT_SECTION,
        )
        assert res.status == "matched"
        assert res.source == "full_text"

    def test_prefers_format_section_over_full_text(self):
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION, full_text="一、投标函\n别的正文",
        )
        assert res.source == "format_section"

    def test_skips_toc_entry_and_takes_real_section(self):
        """场景：同名标题在目录和正文各出现一次，必须取正文那份。"""
        corpus = (
            "目录\n"
            "一、投标函 ...................... -32-\n"
            "一、投标函\n\n致：某某单位\n"
        )
        res = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=corpus,
        )
        assert res.status == "matched"
        assert "致：某某单位" in corpus[res.best.start:res.best.end]

    def test_duplicate_real_sections_are_ambiguous(self):
        """场景：正文与附录有同名小节 → 交给用户挑，不替他决定。"""
        corpus = "一、投标函\n\n正文甲\n\n一、投标函\n\n正文乙\n"
        res = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=corpus,
        )
        assert res.status == "ambiguous"
        assert len(res.candidates) == 2
        assert all(c.score == 1.0 for c in res.candidates)

    def test_page_resolved_from_page_map(self):
        pmap = [{"start": 0, "page": 30}]
        res = match_tender_section(
            "投标函", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION, format_page_map=pmap,
        )
        assert res.best.page == 30

    def test_word_pasted_title_still_matches(self):
        """场景：用户从 Word 粘贴，带全角空格与手写编号。"""
        res = match_tender_section(
            "　一、投标函　", chapter_type="fixed_form",
            format_section_text=FORMAT_SECTION,
        )
        assert res.status == "matched"


class TestTableMatching:
    TABLES = [
        {"page": 33, "table_index": 0, "rows": [["序号", "服务内容", "报价"], ["1", "安保", ""]]},
    ]

    def test_table_chapter_picks_table_by_page_proximity(self):
        pmap = [{"start": 0, "page": 32}, {"start": 40, "page": 33}]
        res = match_tender_section(
            "开标一览表", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=self.TABLES,
            format_page_map=pmap,
        )
        assert res.status == "matched"
        assert res.source == "table"
        assert res.table_index == 0
        assert res.best is not None, "文字也命中了「二、开标一览表」"

    def test_table_chapter_matches_even_when_caption_absent(self):
        """表格章节的内容就是那张表，标题文案对不上小节也要按表头把表选出来。"""
        res = match_tender_section(
            "序号服务内容报价", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=self.TABLES,
        )
        assert res.status == "matched"
        assert res.source == "table"
        assert res.table_index == 0
        assert res.best is None

    def test_table_chapter_without_tables_degrades_to_text_result(self):
        """一张表都没有 → 退回普通文本匹配的结果，而不是硬报 matched。"""
        res = match_tender_section(
            "开标一览表", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=[],
        )
        assert res.table_index is None
        assert res.source == "format_section"
        assert res.status == "matched"

    def test_table_header_scoring_picks_best_of_several(self):
        tables = [
            {"page": 10, "table_index": 0,
             "rows": [["序号", "姓名", "职称"], ["1", "", ""]]},
            {"page": 11, "table_index": 0,
             "rows": [["序号", "服务内容", "报价"], ["1", "", ""]]},
        ]
        res = match_tender_section(
            "序号服务内容报价", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=tables,
        )
        assert res.table_index == 1

    def test_pages_are_preferred_over_header_score(self):
        """有页码时以页码就近为准：表紧跟在小节标题之后。"""
        tables = [
            {"page": 31, "table_index": 0,
             "rows": [["序号", "服务内容", "报价"], ["1", "", ""]]},
            {"page": 40, "table_index": 0,
             "rows": [["序号", "服务内容", "报价"], ["1", "", ""]]},
        ]
        pmap = [{"start": 0, "page": 32}]
        res = match_tender_section(
            "开标一览表", chapter_type="table",
            format_section_text=FORMAT_SECTION, format_tables=tables,
            format_page_map=pmap,
        )
        assert res.table_index == 1, "第 31 页在小节之前，应取其后的第 40 页那张"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_tender_section_matcher.py -v`
Expected: FAIL — `ImportError: cannot import name 'MatchResult'`

- [ ] **Step 7: Implement the matcher entry point**

追加到 `backend/app/services/tender_section_matcher.py`：

```python
import hashlib


@dataclass
class MatchResult:
    """一次匹配的完整结果."""

    status: str                                   # matched | ambiguous | missing | na
    source: str | None = None                     # format_section | full_text | table
    best: MatchCandidate | None = None
    candidates: list[MatchCandidate] = field(default_factory=list)
    table_index: int | None = None
    corpus_hash: str | None = None


def corpus_hash(text: str) -> str:
    """语料文本的 sha1，用于判断"原文变了没"."""
    return "sha1:" + hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def page_at(page_map: list[dict] | None, offset: int) -> int | None:
    """按偏移量查真实页码；没有映射表返回 None（调用方不要把 None 编造成页号）."""
    if not page_map:
        return None
    page = None
    for entry in page_map:
        if entry.get("start", 0) <= offset:
            page = entry.get("page")
        else:
            break
    return page


def _pick_table_index(
    target_norm: str, tables: list[dict] | None, section_page: int | None,
) -> int | None:
    """选出该章节对应的表格下标.

    优先按页码就近取——招标原文里表格紧跟在小节标题之后；没有页码信息时退到
    用首行（表头）文字打分。
    """
    if not tables:
        return None

    if section_page is not None:
        later = [i for i, t in enumerate(tables) if (t.get("page") or 0) >= section_page]
        if later:
            return later[0]

    best_i, best_score = None, 0.0
    for i, t in enumerate(tables):
        rows = t.get("rows") or []
        if not rows:
            continue
        header_norm = normalize_title("".join(str(c or "") for c in rows[0]))
        s = score_title(target_norm, header_norm)
        if s > best_score:
            best_i, best_score = i, s
    return best_i if best_score >= MATCH_THRESHOLD else None


def _score_corpus(
    target_norm: str, text: str, page_map: list[dict] | None,
) -> list[MatchCandidate]:
    """在单份语料里给所有标题行打分，返回 ≥ MATCH_THRESHOLD 的候选（降序）."""
    headers = enumerate_headers(text)
    scored: list[MatchCandidate] = []
    for idx, h in enumerate(headers):
        s = score_title(target_norm, normalize_title(h.title))
        if s < MATCH_THRESHOLD:
            continue
        if not has_body(text, headers, idx):
            continue
        scored.append(MatchCandidate(
            title=h.title,
            raw=h.raw,
            level=h.level,
            start=h.start,
            end=section_end(headers, idx, len(text)),
            score=s,
            page=page_at(page_map, h.start),
        ))
    scored.sort(key=lambda c: (-c.score, c.start))
    return scored


def match_tender_section(
    title: str,
    *,
    chapter_type: str = "fixed_form",
    format_section_text: str | None = None,
    full_text: str | None = None,
    format_tables: list[dict] | None = None,
    format_page_map: list[dict] | None = None,
) -> MatchResult:
    """按标题在招标原文中定位一个小节.

    语料优先级：``format_section_text``（投标文件格式章节）→ ``full_text``（全文）。
    ``chapter_type == "table"`` 时额外选出一张表并记 ``table_index``。

    注意：**不在此处做"匹配不上就降级 AI"的兜底**——那由调用方决定，
    本函数只如实报告 status，好让结构页能把红叉显示给用户。
    """
    target_norm = normalize_title(title)
    if not target_norm:
        return MatchResult(status="missing")

    if chapter_type == "ai_generated":
        return MatchResult(status="na")

    # 先按文字找小节（两种情况都要：文本类要靠它切片，表格类要靠它的页码定表）
    text_hit: MatchResult | None = None
    for source, text, pmap in (
        ("format_section", format_section_text, format_page_map),
        ("full_text", full_text, None),
    ):
        if not text:
            continue
        scored = _score_corpus(target_norm, text, pmap)
        if not scored:
            continue
        status = classify(scored)
        text_hit = MatchResult(
            status=status, source=source, best=scored[0],
            candidates=scored[:MAX_CANDIDATES] if status == "ambiguous" else [],
            corpus_hash=corpus_hash(text),
        )
        break

    if chapter_type != "table":
        return text_hit or MatchResult(status="missing")

    # 表格类：**那张表本身就是内容**，不要求标题文案先匹配上小节。
    # 有文字命中就拿它的页码就近取表；没有就直接用表头文字打分。
    section_page = text_hit.best.page if (text_hit and text_hit.best) else None
    table_index = _pick_table_index(target_norm, format_tables, section_page)
    if table_index is None:
        # 一张表都没有 → 退化成普通文本匹配的结果（可能 matched / ambiguous / missing）
        return text_hit or MatchResult(status="missing")

    return MatchResult(
        status="matched",
        source="table",
        best=text_hit.best if text_hit else None,
        candidates=[],
        table_index=table_index,
        corpus_hash=text_hit.corpus_hash if text_hit else None,
    )
```

同时把文件顶部的 `from dataclasses import dataclass` 改成 `from dataclasses import dataclass, field`，并加上 `import hashlib`。

- [ ] **Step 8: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_tender_section_matcher.py -v`
Expected: PASS（46 passed）

- [ ] **Step 9: Wire the page map into the upload path**

在 `backend/app/api/bid.py` 的格式章节提取块（137-144 行）里加一行：

```python
        requirements["format_section_text"] = format_section["full_text"]
        requirements["format_tables"] = format_section["tables"]
        requirements["format_pages"] = [format_section["start_page"], format_section["end_page"]]
        requirements["format_page_map"] = format_section.get("page_map", [])
```

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/tender_section_matcher.py backend/app/services/pdf_extractor.py backend/app/api/bid.py backend/tests/test_tender_section_matcher.py backend/tests/test_pdf_extractor.py
git commit -m "feat: 匹配器支持全文兜底/表格选取/页码映射，并修正页页码不可反推的问题"
```

---

### Task 4: template_filler 接入新匹配器

把 `extract_fixed_form_section` 改为委托新匹配器（顺带修掉它「碰到任何同级标题就停」的边界 bug），并让 `fill_fixed_form_section_from_template` 支持「原文片段直接传入」和「带出表格填充」。

**Files:**
- Modify: `backend/app/services/template_filler.py`（`extract_fixed_form_section` 约 458-525 行、`fill_fixed_form_section_from_template` 约 528-625 行）
- Test: `backend/tests/test_template_filler.py`、`backend/tests/test_v2_integration.py`（既有用例即回归网）

**Interfaces:**
- Consumes: Task 3 的 `match_tender_section` / `MatchResult` / `normalize_title`
- Produces:
  - `extract_fixed_form_section(format_section_text, section_title) -> str`（**签名与返回类型不变**）
  - `fill_fixed_form_section_from_template(section_title, format_section_text, format_tables=None, company_profile=None, requirements=None, ai_adapter=None, section_text_override=None) -> str`
  - `fill_fixed_form_section_from_template_with_tables(...) -> tuple[str, list[dict]]` —— 新增，返回 `(填充后文本, table_fills)`，其中 `table_fills` 是 `scan_and_mark_variables` 的结果**原样带出**（此前被丢弃）

- [ ] **Step 1: Write the failing tests**

追加到 `backend/tests/test_template_filler.py`：

```python
class TestBoundaryWithSubLevelHeadings:
    """回归：旧实现遇到「（一）」这类子级标题不会截断，遇到同级才截断；
    改造后必须严格按层级——子级留在父级正文里，同级才截断。"""

    CORPUS = (
        "一、投标函\n\n"
        "致：某某单位\n"
        "我方已仔细阅读。\n\n"
        "（一）投标函附录\n\n"
        "附录内容\n\n"
        "二、开标一览表\n\n"
        "序号 | 服务内容\n"
    )

    def test_parent_body_keeps_sub_level_section(self):
        out = extract_fixed_form_section(self.CORPUS, "投标函")
        assert "我方已仔细阅读" in out
        assert "附录内容" in out, "子级小节属于父级正文"
        assert "开标一览表" not in out, "同级标题必须截断"

    def test_child_section_extractable_on_its_own(self):
        out = extract_fixed_form_section(self.CORPUS, "投标函附录")
        assert "附录内容" in out
        assert "我方已仔细阅读" not in out


class TestSectionTextOverride:
    @pytest.mark.asyncio
    async def test_direct_text_skips_relocation(self):
        """传了原文片段就不再按标题去找——这是"所见即所得"的关键。"""
        adapter = AsyncMock()
        adapter.chat.completions.create = AsyncMock(side_effect=AssertionError(
            "override 路径不该调用 AI 定位"
        ))
        filled = await fill_fixed_form_section_from_template(
            section_title="投标函",
            format_section_text="一、投标函\n\n致：某某单位\n",
            section_text_override="投标函\n\n投标人名称：____\n",
            company_profile=MOCK_COMPANY,
            requirements=MOCK_REQS,
            ai_adapter=adapter,
        )
        assert "投标函" in filled

    @pytest.mark.asyncio
    async def test_override_without_ai_still_works(self):
        filled = await fill_fixed_form_section_from_template(
            section_title="投标函",
            format_section_text="一、投标函\n\n致：某某单位\n",
            section_text_override="投标人名称：____\n",
            company_profile=MOCK_COMPANY,
            requirements=MOCK_REQS,
            ai_adapter=None,
        )
        assert isinstance(filled, str)


class TestTableFillsCarriedOut:
    @pytest.mark.asyncio
    async def test_table_fills_are_returned_not_dropped(self):
        """旧实现把 scan_result['table_fills'] 丢掉了，表格填充是死代码。"""
        adapter = AsyncMock()
        adapter.chat.completions.create = AsyncMock(return_value=AsyncMock(
            choices=[AsyncMock(message=AsyncMock(content=json.dumps({
                "text_replacements": [],
                "table_fills": [
                    {"page": 33, "table_index": 0, "row": 1, "col": 1, "var": "company_name"}
                ],
                "warnings": [],
            })))]
        ))
        _text, table_fills = await fill_fixed_form_section_from_template_with_tables(
            section_title="开标一览表",
            format_section_text="一、开标一览表\n\n序号 | 服务内容\n",
            format_tables=[{"page": 33, "table_index": 0, "rows": [["序号", "服务内容"], ["1", ""]]}],
            company_profile=MOCK_COMPANY,
            requirements=MOCK_REQS,
            ai_adapter=adapter,
        )
        assert table_fills, "table_fills 必须带出来，不能再被丢弃"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/Scripts/python.exe -m pytest tests/test_template_filler.py -k "BoundaryWithSubLevelHeadings or SectionTextOverride or TableFillsCarriedOut" -v`
Expected: FAIL — `ImportError: cannot import name 'fill_fixed_form_section_from_template_with_tables'`，且 `TestBoundaryWithSubLevelHeadings::test_parent_body_keeps_sub_level_section` 断言失败（现在会截断在「（一）」）

- [ ] **Step 3: Implement the delegation and the two new capabilities**

在 `backend/app/services/template_filler.py` 顶部 import 区加入：

```python
from app.services.tender_section_matcher import (
    match_tender_section,
    normalize_title,
)
```

把 `extract_fixed_form_section`（458-525 行）整体替换为：

```python
def extract_fixed_form_section(format_section_text: str, section_title: str) -> str:
    """从格式章节全文中定位并截取指定固定格式小节.

    委托 ``tender_section_matcher`` 做匹配与截取，本函数只保留"返回纯文本"的
    老签名，供既有调用方继续使用。

    与旧实现的行为差异（**有意为之**）：截取边界改为"下一个层级 ≤ 本级的标题"，
    因此 `（一）投标函附录` 这类子级标题不再截断 `一、投标函` 的正文。

    ``ambiguous``（同名小节出现多次）时取分数最高、位置最靠前的一个——保持
    旧实现"取首个可用匹配"的宽容度，避免老项目突然大面积回退到 AI 生成；
    需要用户挑候选的是**结构页**，走 ``match_tender_section`` 的完整状态。

    Returns:
        该小节的正文（开头的重复标题行已去掉）；找不到返回空字符串。
    """
    if not format_section_text or not section_title:
        return ""

    result = match_tender_section(
        section_title,
        chapter_type="fixed_form",
        format_section_text=format_section_text,
    )
    if result.best is None:
        return ""

    section_text = format_section_text[result.best.start:result.best.end].strip()
    return _strip_duplicate_heading(section_text, section_title)
```

把 `fill_fixed_form_section_from_template`（528-625 行）改为接受 override 并委托带表格版本：

```python
async def fill_fixed_form_section_from_template(
    section_title: str,
    format_section_text: str,
    format_tables: list[dict] | None = None,
    company_profile: dict | None = None,
    requirements: dict | None = None,
    ai_adapter=None,
    section_text_override: str | None = None,
) -> str:
    """从招标文件的格式章节原文模板中提取并填充指定固定格式小节.

    Args:
        section_text_override: 已经切好的原文片段。传了就直接用它，**跳过按标题
            重新定位**——结构页固化的匹配区间必须被尊重，否则"用户看到的预览"
            与"最终进标书的内容"可能不是同一段（见设计文档 §8.1）。

    Returns:
        填充后的小节文本。找不到该小节、AI 扫描失败、或格式章节为空时返回空字符串，
        由调用方走 ``generate_file_section`` 兜底。
    """
    filled, _table_fills = await fill_fixed_form_section_from_template_with_tables(
        section_title=section_title,
        format_section_text=format_section_text,
        format_tables=format_tables,
        company_profile=company_profile,
        requirements=requirements,
        ai_adapter=ai_adapter,
        section_text_override=section_text_override,
    )
    return filled


async def fill_fixed_form_section_from_template_with_tables(
    section_title: str,
    format_section_text: str,
    format_tables: list[dict] | None = None,
    company_profile: dict | None = None,
    requirements: dict | None = None,
    ai_adapter=None,
    section_text_override: str | None = None,
) -> tuple[str, list[dict]]:
    """同 fill_fixed_form_section_from_template，但把 table_fills 一并带出.

    旧实现把 ``scan_result["table_fills"]`` 直接丢弃，导致 ``batch_fill_tables``
    成了死代码；表格类章节因此拿不到招标表格里的填值位置。

    Returns:
        (填充后文本, table_fills)；失败时返回 ``("", [])``。
    """
    if not format_section_text or not section_title:
        return "", []

    if section_text_override:
        section_text = _strip_duplicate_heading(section_text_override, section_title)
    else:
        section_text = extract_fixed_form_section(format_section_text, section_title)
    if not section_text:
        logger.info(
            "Section '%s' not found in format_section_text, caller should fallback",
            section_title,
        )
        return "", []

    variables = build_variable_values(company_profile, requirements)
    known_values = {k: v for k, v in variables.items() if v}

    scan_result = await scan_and_mark_variables(
        full_text=section_text,
        tables=format_tables or [],
        ai_adapter=ai_adapter,
        known_values=known_values,
    )

    if scan_result.get("warnings") and not scan_result.get("text_replacements"):
        logger.warning(
            "Scan for section '%s' returned no replacements (%s); caller should fallback",
            section_title, scan_result["warnings"],
        )
        return "", []

    # 把 variable 值注入到 replacement（如果 AI 没填）
    enriched = []
    unknown_vars: list[str] = []
    for rep in scan_result.get("text_replacements", []):
        var = rep.get("var")
        if not var:
            continue
        rep_copy = dict(rep)
        if "value" not in rep_copy or rep_copy["value"] is None:
            if var in variables:
                rep_copy["value"] = variables[var]
            else:
                unknown_vars.append(var)
                rep_copy["value"] = ""
        enriched.append(rep_copy)

    if unknown_vars:
        logger.warning(
            "Section '%s': AI marked %d variable(s) with no source, left untouched: %s",
            section_title, len(unknown_vars), unknown_vars,
        )

    filled_text = batch_fill_text(section_text, enriched)

    residual = post_scan(filled_text)
    if residual:
        logger.warning(
            "Section '%s' filled with residuals: %s",
            section_title, residual,
        )

    logger.info(
        "Filled fixed-form section '%s': %d chars, %d replacements applied",
        section_title, len(filled_text), len(enriched),
    )
    return filled_text, list(scan_result.get("table_fills") or [])
```

- [ ] **Step 4: Run the full template_filler + integration suites**

Run: `venv/Scripts/python.exe -m pytest tests/test_template_filler.py tests/test_v2_integration.py tests/test_format_integration.py -v`
Expected: PASS — 既有 92 条全绿（这是本次改造最重要的回归网），新增 5 条通过

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/template_filler.py backend/tests/test_template_filler.py
git commit -m "fix: 固定格式小节截取按层级判定边界 + 表格填充不再被丢弃"
```

---

### Task 5: 保存章节结构端点

**Files:**
- Modify: `backend/app/api/chapters.py`（新增路由；`ChapterType` 白名单常量）
- Test: `backend/tests/test_chapter_structure_api.py`（新建）

**Interfaces:**
- Consumes: `BidProject.chapter_structure_json`、`ProjectChapter` 模型
- Produces:
  - `PUT /api/v1/bid/{project_id}/chapter-structure`，请求体 `{"chapters": [...]}`，响应 `{"success": bool, "chapters_count": int, "pruned_attachments": list[str]}`
  - 模块常量 `USER_SELECTABLE_TYPES = ("fixed_form", "table", "attachment", "ai_generated")`

- [ ] **Step 1: Write the failing test**

创建 `backend/tests/test_chapter_structure_api.py`：

```python
"""宏曦标书 - 章节结构保存端点 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json

import pytest

from app.api.chapters import (
    USER_SELECTABLE_TYPES,
    _normalize_chapter_tree,
    _prune_attachments,
)


class TestNormalizeChapterTree:
    def test_order_index_is_renumbered(self):
        tree = [{"title": "甲", "type": "ai_generated"},
                {"title": "乙", "type": "ai_generated"}]
        out = _normalize_chapter_tree(tree)
        assert [c["order_index"] for c in out] == [0, 1]

    def test_invalid_type_falls_back_to_ai_generated(self):
        out = _normalize_chapter_tree([{"title": "甲", "type": "胡说"}])
        assert out[0]["type"] == "ai_generated"

    def test_mixed_is_preserved_but_not_selectable(self):
        out = _normalize_chapter_tree([{"title": "甲", "type": "mixed"}])
        assert out[0]["type"] == "mixed"
        assert "mixed" not in USER_SELECTABLE_TYPES

    def test_children_recursed(self):
        out = _normalize_chapter_tree([
            {"title": "甲", "type": "ai_generated",
             "children": [{"title": "甲一", "type": "table"}]},
        ])
        assert out[0]["children"][0]["order_index"] == 0
        assert out[0]["children"][0]["type"] == "table"

    def test_match_and_attachments_survive_roundtrip(self):
        tree = [{
            "title": "投标函", "type": "fixed_form",
            "match": {"status": "matched", "start": 10, "end": 40,
                      "corpus_hash": "sha1:x"},
            "attachments": [{"kind": "qualification", "id": "q1", "label": "营业执照",
                             "path": "ocr/a.png"}],
        }]
        out = _normalize_chapter_tree(tree)
        assert out[0]["match"]["start"] == 10
        assert out[0]["attachments"][0]["id"] == "q1"


class TestPruneAttachments:
    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    @pytest.mark.asyncio
    async def test_missing_library_row_is_pruned(self):
        """场景：资源库里那行资质已被删除。"""
        class _DB:
            async def execute(self, _stmt):
                return TestPruneAttachments._FakeResult([])

        tree = [{"title": "资质", "type": "attachment", "attachments": [
            {"kind": "qualification", "id": "gone", "label": "已删除的资质",
             "path": "ocr/x.png"},
        ]}]
        pruned = await _prune_attachments(tree, _DB())
        assert tree[0]["attachments"] == []
        assert "已删除的资质" in pruned

    @pytest.mark.asyncio
    async def test_path_outside_upload_dir_is_pruned(self):
        class _DB:
            async def execute(self, _stmt):
                return TestPruneAttachments._FakeResult([])

        tree = [{"title": "附件", "type": "attachment", "attachments": [
            {"kind": "upload", "id": "u1", "label": "越界文件",
             "path": "../../etc/passwd"},
        ]}]
        pruned = await _prune_attachments(tree, _DB())
        assert tree[0]["attachments"] == []
        assert "越界文件" in pruned

    @pytest.mark.asyncio
    async def test_valid_upload_survives(self):
        class _DB:
            async def execute(self, _stmt):
                return TestPruneAttachments._FakeResult([])

        tree = [{"title": "附件", "type": "attachment", "attachments": [
            {"kind": "upload", "id": "u1", "label": "保证金凭证",
             "path": "project_p1/receipt.png"},
        ]}]
        pruned = await _prune_attachments(tree, _DB())
        assert len(tree[0]["attachments"]) == 1
        assert pruned == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py -v`
Expected: FAIL — `ImportError: cannot import name 'USER_SELECTABLE_TYPES'`

- [ ] **Step 3: Write minimal implementation**

在 `backend/app/api/chapters.py` 顶部（`router = APIRouter()` 之后）加入常量与两个纯函数，并在文件末尾加上路由：

```python
# 用户可以在结构页选择的章节类型。mixed 由 AI 提取产生，保留展示但不可选。
USER_SELECTABLE_TYPES = ("fixed_form", "table", "attachment", "ai_generated")
_KNOWN_TYPES = set(USER_SELECTABLE_TYPES) | {"mixed"}

# 允许在结构页编辑的项目状态（「目录确认」之前）
_STRUCTURE_EDITABLE_STATUS = ("draft", "structure_ready")


def _normalize_chapter_tree(tree: list) -> list:
    """校验并归一化前端提交的章节树.

    - ``type`` 不在已知集合内 → 退回 ``ai_generated``（不报错，避免前端版本
      稍旧就整单失败）
    - ``order_index`` 一律按数组顺序重排，忽略前端传来的值（拖拽后的顺序就是
      数组顺序，这是唯一的真相）
    - ``match`` / ``attachments`` 原样保留
    """
    out = []
    for node in tree or []:
        if not isinstance(node, dict):
            continue
        ch_type = node.get("type") or "ai_generated"
        if ch_type not in _KNOWN_TYPES:
            ch_type = "ai_generated"
        normalized = {
            "order_index": len(out),
            "number": node.get("number", ""),
            "title": (node.get("title") or "").strip(),
            "type": ch_type,
            "required": bool(node.get("required", False)),
            "format_notes": node.get("format_notes"),
            "scoring_context": node.get("scoring_context"),
            "table_columns": node.get("table_columns"),
            "source": node.get("source"),
        }
        if node.get("match"):
            normalized["match"] = node["match"]
        normalized["attachments"] = list(node.get("attachments") or [])
        normalized["children"] = _normalize_chapter_tree(node.get("children") or [])
        out.append(normalized)
    return out


async def _prune_attachments(tree: list, db: AsyncSession) -> list[str]:
    """剔除失效附件（资源库行已删 / 路径越出 UPLOAD_DIR），返回被剔除的 label.

    剔除而非整单拒绝：用户可能填了十个附件、其中一个被删了，整单报错会让他
    白填一遍。
    """
    from pathlib import Path

    from sqlalchemy import select

    from app.config import settings
    from app.models.qualification import Qualification
    from app.models.personnel import PersonnelCertificate
    from app.models.contract import Contract

    _MODEL_BY_KIND = {
        "qualification": Qualification,
        "personnel_cert": PersonnelCertificate,
        "contract": Contract,
    }

    upload_root = Path(settings.UPLOAD_DIR).resolve()
    pruned: list[str] = []

    async def _walk_async(nodes: list) -> None:
        for node in nodes or []:
            kept = []
            for att in node.get("attachments") or []:
                label = att.get("label") or att.get("id") or "(未命名)"
                kind = att.get("kind")
                path = att.get("path") or ""
                try:
                    resolved = (upload_root / path).resolve()
                    if not str(resolved).startswith(str(upload_root)):
                        pruned.append(label)
                        continue
                except (OSError, ValueError):
                    pruned.append(label)
                    continue
                if kind in _MODEL_BY_KIND:
                    model = _MODEL_BY_KIND[kind]
                    found = (
                        await db.execute(select(model.id).where(model.id == att.get("id")))
                    ).scalars().all()
                    if not found:
                        pruned.append(label)
                        continue
                kept.append(att)
            node["attachments"] = kept
            await _walk_async(node.get("children") or [])

    await _walk_async(tree)
    return pruned


class ChapterStructureSaveRequest(BaseModel):
    chapters: list[dict] = []


class ChapterStructureSaveResponse(BaseModel):
    success: bool = False
    chapters_count: int = 0
    pruned_attachments: list[str] = []


@router.put("/{project_id}/chapter-structure", response_model=ChapterStructureSaveResponse)
async def save_chapter_structure(
    project_id: str,
    payload: ChapterStructureSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存用户在目录确认页编辑后的章节结构.

    服务端**不重新匹配**——``match`` 是前端从 /chapter-structure/match 拿到的
    结果原样带回的，服务端只做结构校验与附件有效性剔除。
    """
    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status not in _STRUCTURE_EDITABLE_STATUS:
        raise HTTPException(
            status_code=409,
            detail=f"项目已进入 {project.status} 阶段，不能再修改章节结构",
        )

    if not payload.chapters:
        raise HTTPException(status_code=400, detail="章节结构不能为空")

    tree = _normalize_chapter_tree(payload.chapters)
    pruned = await _prune_attachments(tree, db)

    project.chapter_structure_json = json.dumps(tree, ensure_ascii=False)
    await db.commit()

    logger.info(
        "Chapter structure saved for project %s: %d top-level chapters, %d attachments pruned",
        project_id, len(tree), len(pruned),
    )
    return ChapterStructureSaveResponse(
        success=True, chapters_count=len(tree), pruned_attachments=pruned,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Add cross-node content dedup to `_prune_attachments`**

同一张扫描件在资源库里常有两个存储路径（`uploads/company/x.png` 与 `ocr/y.png`），路径不同、字节相同。既有的 `drop_already_embedded` 就是按**内容 md5** 判重的（`render_engine.image_content_key`），附件也得守同一条规矩，否则同一张营业执照会在两个章节里各出一次图。

在 `_prune_attachments` 里加一个跨节点登记表：

```python
    from app.services.render_engine import image_content_key

    upload_root = Path(settings.UPLOAD_DIR).resolve()
    pruned: list[str] = []
    seen_content: set[str] = set()

    async def _walk_async(nodes: list) -> None:
        for node in nodes or []:
            kept = []
            for att in node.get("attachments") or []:
                label = att.get("label") or att.get("id") or "(未命名)"
                kind = att.get("kind")
                path = att.get("path") or ""
                try:
                    resolved = (upload_root / path).resolve()
                    if not str(resolved).startswith(str(upload_root)):
                        pruned.append(label)
                        continue
                except (OSError, ValueError):
                    pruned.append(label)
                    continue
                if kind in _MODEL_BY_KIND:
                    model = _MODEL_BY_KIND[kind]
                    found = (
                        await db.execute(select(model.id).where(model.id == att.get("id")))
                    ).scalars().all()
                    if not found:
                        pruned.append(label)
                        continue
                # 按内容判重：同一张扫描件在两个章节里只出一次图（与
                # materials_injection.drop_already_embedded 同一口径）
                key = image_content_key(path)
                if key in seen_content:
                    pruned.append(label)
                    continue
                seen_content.add(key)
                kept.append(att)
            node["attachments"] = kept
            await _walk_async(node.get("children") or [])

    await _walk_async(tree)
    return pruned
```

追加测试到 `backend/tests/test_chapter_structure_api.py`：

```python
    @pytest.mark.asyncio
    async def test_same_image_content_in_two_chapters_kept_once(self, tmp_path, monkeypatch):
        """场景：同一张营业执照挂了两个章节（两个不同路径、字节相同）。"""
        from app.config import settings
        from app.services import render_engine

        monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
        (tmp_path / "a.png").write_bytes(b"same-bytes")
        (tmp_path / "b.png").write_bytes(b"same-bytes")

        class _DB:
            async def execute(self, _stmt):
                return TestPruneAttachments._FakeResult([])

        tree = [
            {"title": "资质", "type": "attachment", "attachments": [
                {"kind": "upload", "id": "u1", "label": "营业执照", "path": "a.png"}]},
            {"title": "其他材料", "type": "attachment", "attachments": [
                {"kind": "upload", "id": "u2", "label": "营业执照副本", "path": "b.png"}]},
        ]
        pruned = await _prune_attachments(tree, _DB())
        assert len(tree[0]["attachments"]) == 1
        assert tree[1]["attachments"] == []
        assert "营业执照副本" in pruned
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py -v`
Expected: PASS（9 passed）

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/chapters.py backend/tests/test_chapter_structure_api.py
git commit -m "feat: 保存章节结构端点 — 手动编辑终于能落库"
```

---

### Task 6: 全文落盘（匹配兜底的语料）

`document_text` 目前只在 `upload-and-parse` 请求内用一次，**不落库**；而 spec §6 要求匹配能退到全文。本任务把它写到文件（避免为几百 KB 文本加一个 DB 列，也避免拖慢每次读 `parsed_requirements_json`）。

**Files:**
- Create: `backend/app/services/tender_corpus.py`
- Modify: `backend/app/api/bid.py`（`upload-and-parse` 内，紧接 `document_text = parse_document(...)` 之后）
- Test: `backend/tests/test_tender_corpus.py`（新建）

**Interfaces:**
- Produces:
  - `persist_full_text(upload_dir: str, stem: str, text: str) -> str | None` —— 返回相对路径（如 `parsed/ab12.txt`），失败返回 `None`
  - `load_full_text(requirements: dict, upload_dir: str) -> str | None`
  - `requirements["full_text_path"]`

- [ ] **Step 1: Write the failing test**

创建 `backend/tests/test_tender_corpus.py`：

```python
"""宏曦标书 - 招标全文语料持久化 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from app.services.tender_corpus import load_full_text, persist_full_text


class TestPersistAndLoad:
    def test_roundtrip(self, tmp_path):
        rel = persist_full_text(str(tmp_path), "ab12", "招标全文内容")
        assert rel == "parsed/ab12.txt"
        assert load_full_text({"full_text_path": rel}, str(tmp_path)) == "招标全文内容"

    def test_persist_empty_text_returns_none(self, tmp_path):
        assert persist_full_text(str(tmp_path), "ab12", "") is None

    def test_missing_path_returns_none(self, tmp_path):
        assert load_full_text({}, str(tmp_path)) is None
        assert load_full_text({"full_text_path": "parsed/nope.txt"}, str(tmp_path)) is None

    def test_path_escape_is_refused(self, tmp_path):
        """语料路径不可越出 UPLOAD_DIR。"""
        out = load_full_text({"full_text_path": "../../etc/passwd"}, str(tmp_path))
        assert out is None

    def test_persist_failure_does_not_raise(self, tmp_path):
        """落盘失败只该返回 None，不能把上传解析整个搞崩。"""
        assert persist_full_text(str(tmp_path / "nope" / "\0bad"), "x", "内容") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_tender_corpus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tender_corpus'`

- [ ] **Step 3: Write minimal implementation**

创建 `backend/app/services/tender_corpus.py`：

```python
"""宏曦标书 - 招标全文语料的落盘与读取.

章节匹配器需要全文作为「格式章节里找不到」时的兜底语料，而全文此前只在
upload-and-parse 请求内存在。落成文件而不是新增 DB 列：全文可达数百 KB～
数 MB，塞进被频繁读取的 parsed_requirements_json 会拖慢所有读该字段的路径。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SUBDIR = "parsed"


def persist_full_text(upload_dir: str, stem: str, text: str) -> str | None:
    """把全文写到 ``{upload_dir}/parsed/{stem}.txt``，返回**相对**路径.

    失败只记 warning 并返回 None —— 全文兜底是可选增强，不能阻断上传解析。
    """
    if not text or not text.strip() or not stem:
        return None
    try:
        target_dir = Path(upload_dir) / _SUBDIR
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{stem}.txt").write_text(text, encoding="utf-8")
        return f"{_SUBDIR}/{stem}.txt"
    except (OSError, ValueError) as exc:
        logger.warning("Failed to persist full text (%s): %s", stem, exc)
        return None


def load_full_text(requirements: dict, upload_dir: str) -> str | None:
    """读回全文；没有、越界或读失败都返回 None（调用方据此跳过全文兜底）."""
    rel = (requirements or {}).get("full_text_path")
    if not rel:
        return None
    try:
        root = Path(upload_dir).resolve()
        resolved = (root / rel).resolve()
        if not str(resolved).startswith(str(root)):
            logger.warning("Refused full-text path outside upload dir: %s", rel)
            return None
        if not resolved.is_file():
            return None
        return resolved.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        logger.warning("Failed to load full text (%s): %s", rel, exc)
        return None
```

- [ ] **Step 4: Wire it into the upload path**

在 `backend/app/api/bid.py` 的 `upload_and_parse` 里，`document_text = parse_document(str(saved_path))` 之后（约 128-129 行）加：

```python
    # 全文落盘，供章节匹配在「格式章节里找不到」时兜底（失败不阻断解析）
    from app.services.tender_corpus import persist_full_text
    rel_full_text = persist_full_text(str(upload_dir), saved_path.stem, document_text)
    if rel_full_text:
        requirements["full_text_path"] = rel_full_text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_tender_corpus.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/tender_corpus.py backend/app/api/bid.py backend/tests/test_tender_corpus.py
git commit -m "feat: 招标全文落盘 — 给章节匹配留全文兜底语料"
```

---

### Task 7: 试匹配端点

**Files:**
- Modify: `backend/app/api/chapters.py`
- Test: `backend/tests/test_chapter_structure_api.py`

**Interfaces:**
- Consumes: Task 3 的 `match_tender_section`、Task 6 的 `load_full_text`、Task 5 的 `_STRUCTURE_EDITABLE_STATUS`
- Produces:
  - `POST /api/v1/bid/{project_id}/chapter-structure/match`，请求 `{"title": str, "type": str}`，响应 `{"status", "source", "best", "candidates", "table_index", "corpus_hash"}`
  - 模块常量 `PREVIEW_MAX_CHARS = 4000`
  - 模块函数 `_load_match_inputs(project) -> tuple[dict, str | None]`

- [ ] **Step 1: Write the failing test**

追加到 `backend/tests/test_chapter_structure_api.py`：

```python
class TestMatchPayload:
    def test_best_carries_truncated_preview(self):
        from app.api.chapters import _to_match_payload

        corpus = "一、投标函\n\n" + "甲" * 5000
        result = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text=corpus,
        )
        payload = _to_match_payload(result, corpus)
        assert payload["status"] == "matched"
        assert payload["best"]["title"] == "投标函"
        assert payload["best"]["start"] == 0
        assert len(payload["best"]["preview"]) == 4000
        assert payload["best"]["preview_truncated"] is True

    def test_missing_has_no_best(self):
        from app.api.chapters import _to_match_payload

        result = match_tender_section(
            "不存在", chapter_type="fixed_form", format_section_text="一、投标函\n\n正文",
        )
        payload = _to_match_payload(result, "一、投标函\n\n正文")
        assert payload["status"] == "missing"
        assert payload["best"] is None

    def test_na_for_ai_generated(self):
        from app.api.chapters import _to_match_payload

        result = match_tender_section("服务方案", chapter_type="ai_generated")
        payload = _to_match_payload(result, "")
        assert payload["status"] == "na"

    def test_table_preview_present_when_no_text_section(self):
        """表格章节 best 为空时，抽屉靠 table_preview 显示命中内容。"""
        from app.api.chapters import _to_match_payload

        tables = [{"page": 33, "table_index": 0,
                   "rows": [["序号", "服务内容"], ["1", "安保"]]}]
        corpus = "一、别的章节\n\n正文\n"
        result = match_tender_section(
            "序号服务内容", chapter_type="table",
            format_section_text=corpus, format_tables=tables,
        )
        payload = _to_match_payload(result, corpus, tables)
        assert payload["best"] is None
        assert payload["table_index"] == 0
        assert "| 序号 | 服务内容 |" in payload["table_preview"]

    def test_table_preview_absent_for_text_matches(self):
        from app.api.chapters import _to_match_payload

        result = match_tender_section(
            "投标函", chapter_type="fixed_form", format_section_text="一、投标函\n\n正文",
        )
        payload = _to_match_payload(result, "一、投标函\n\n正文")
        assert payload["table_preview"] is None
```

> 该测试文件需要 `from app.services.tender_section_matcher import match_tender_section`——加在文件顶部 import 区。

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py::TestMatchPayload -v`
Expected: FAIL — `ImportError: cannot import name '_to_match_payload'`

- [ ] **Step 3: Write minimal implementation**

在 `backend/app/api/chapters.py` 中加入：

```python
# 匹配预览的原文片段上限——结构页只是给人看的，没必要把整个小节（可能上万字）
# 走一遍网络；超出截断并在响应里标记，前端据此提示。
PREVIEW_MAX_CHARS = 4000


def _load_match_inputs(project) -> tuple[dict, str | None]:
    """取出匹配需要的语料：requirements + 全文（可能为 None）."""
    from app.config import settings
    from app.services.tender_corpus import load_full_text

    try:
        requirements = json.loads(project.parsed_requirements_json or "{}")
    except json.JSONDecodeError:
        requirements = {}
    return requirements, load_full_text(requirements, settings.UPLOAD_DIR)


def _to_match_payload(result, corpus: str, tables: list[dict] | None = None) -> dict:
    """把 MatchResult 转成前端要的形状，附上预览片段.

    表格章节的 ``best`` 可能为空（标题文案对不上小节、只按表头选出了表），
    那不是"没匹配上"——所以额外给出 ``table_preview``，让抽屉有东西可显示。
    """
    from app.services.template_filler import rows_to_markdown

    def _cand(c) -> dict:
        preview = corpus[c.start:c.end]
        return {
            "title": c.title,
            "raw": c.raw,
            "level": c.level,
            "start": c.start,
            "end": c.end,
            "score": round(c.score, 4),
            "page": c.page,
            "preview": preview[:PREVIEW_MAX_CHARS],
            "preview_truncated": len(preview) > PREVIEW_MAX_CHARS,
        }

    table_preview = None
    if result.source == "table" and result.table_index is not None:
        rows = (tables or [])
        if 0 <= result.table_index < len(rows):
            table_preview = rows_to_markdown(
                [list(r) for r in (rows[result.table_index].get("rows") or [])][:20]
            )

    return {
        "status": result.status,
        "source": result.source,
        "best": _cand(result.best) if result.best else None,
        "candidates": [_cand(c) for c in result.candidates],
        "table_index": result.table_index,
        "table_preview": table_preview,
        # 服务端一律给 auto；用户从候选里点选后由前端改成 manual（见 §4.1）
        "picked": "auto",
        "corpus_hash": result.corpus_hash,
    }


class ChapterMatchRequest(BaseModel):
    title: str = ""
    type: str = "fixed_form"


@router.post("/{project_id}/chapter-structure/match")
async def match_chapter_section(
    project_id: str,
    payload: ChapterMatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """试匹配：给定标题与类型，返回命中的招标原文片段或候选列表.

    纯读操作，不改任何状态。前端在标题/类型变化后防抖调用它来刷新徽标。
    """
    from app.services.tender_section_matcher import match_tender_section

    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status not in _STRUCTURE_EDITABLE_STATUS:
        raise HTTPException(
            status_code=409,
            detail=f"项目已进入 {project.status} 阶段，不能再试匹配章节",
        )

    requirements, full_text = _load_match_inputs(project)
    match_result = match_tender_section(
        payload.title,
        chapter_type=payload.type,
        format_section_text=requirements.get("format_section_text"),
        full_text=full_text,
        format_tables=requirements.get("format_tables") or [],
        format_page_map=requirements.get("format_page_map") or [],
    )

    # 预览片段取自实际用到的语料
    if match_result.source == "full_text":
        corpus = full_text or ""
    else:
        corpus = requirements.get("format_section_text") or ""
    return _to_match_payload(
        match_result, corpus, requirements.get("format_tables") or [],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py -v`
Expected: PASS（12 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/chapters.py backend/tests/test_chapter_structure_api.py
git commit -m "feat: 试匹配端点 — 结构页可实时看到命中的招标原文"
```

---

### Task 8: 附件上传端点

**Files:**
- Modify: `backend/app/api/chapters.py`
- Test: `backend/tests/test_chapter_structure_api.py`

**Interfaces:**
- Produces:
  - `POST /api/v1/bid/{project_id}/attachments/upload`（multipart：`file` + `label`），响应 `{"kind": "upload", "id": str, "label": str, "path": str}`
  - 模块常量 `ALLOWED_ATTACHMENT_EXT = (".png", ".jpg", ".jpeg", ".pdf")`

> **必须有 `Form()`**：`label` 是前端 FormData 发的标量字段。不标 `Form()` 它会被当成 query 参数，接不到、**且不报错**，静默退化成 `file.filename`（2026-09-22 实测踩过：上传的需求名变成了「证明.png」）。

- [ ] **Step 1: Write the failing test**

追加到 `backend/tests/test_chapter_structure_api.py`：

```python
class TestAttachmentUploadRules:
    def test_allowed_extensions(self):
        from app.api.chapters import ALLOWED_ATTACHMENT_EXT

        assert ".png" in ALLOWED_ATTACHMENT_EXT
        assert ".pdf" in ALLOWED_ATTACHMENT_EXT
        assert ".exe" not in ALLOWED_ATTACHMENT_EXT

    def test_safe_attachment_filename_strips_path(self):
        from app.api.chapters import _safe_attachment_name

        assert "/" not in _safe_attachment_name("../../evil.png")
        assert _safe_attachment_name("../../evil.png").endswith("evil.png")
        assert _safe_attachment_name("证明 文件.PNG").endswith(".png")

    def test_unsupported_extension_is_rejected(self):
        from app.api.chapters import _is_allowed_attachment

        assert _is_allowed_attachment("a.png") is True
        assert _is_allowed_attachment("a.PDF") is True
        assert _is_allowed_attachment("a.exe") is False
        assert _is_allowed_attachment("noext") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py::TestAttachmentUploadRules -v`
Expected: FAIL — `ImportError: cannot import name 'ALLOWED_ATTACHMENT_EXT'`

- [ ] **Step 3: Write minimal implementation**

在 `backend/app/api/chapters.py` 中加入：

```python
ALLOWED_ATTACHMENT_EXT = (".png", ".jpg", ".jpeg", ".pdf")


def _is_allowed_attachment(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in ALLOWED_ATTACHMENT_EXT


def _safe_attachment_name(filename: str) -> str:
    """只取 basename 并统一小写扩展名，防目录穿越."""
    name = Path(filename or "file").name
    stem = Path(name).stem or "file"
    return f"{stem}{Path(name).suffix.lower()}"


@router.post("/{project_id}/attachments/upload")
async def upload_project_attachment(
    project_id: str,
    file: UploadFile = File(...),
    label: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传本项目专用附件（保证金凭证、基本账户证明等资源库里没有的文件）.

    只负责落盘并返回一条附件记录；清单本身住在章节树里，由前端塞进节点后
    随 ``PUT /chapter-structure`` 统一保存（节点没有稳定 id，按编号寻址会错位）。
    """
    result = await db.execute(select(BidProject).where(BidProject.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.status not in _STRUCTURE_EDITABLE_STATUS:
        raise HTTPException(
            status_code=409,
            detail=f"项目已进入 {project.status} 阶段，不能再添加附件",
        )

    if not _is_allowed_attachment(file.filename or ""):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型，仅接受 {'/'.join(ALLOWED_ATTACHMENT_EXT)}",
        )

    upload_dir = Path(settings.UPLOAD_DIR) / f"project_{project_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _safe_attachment_name(file.filename or "file")
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"
    (upload_dir / stored_name).write_bytes(await file.read())

    rel_path = f"project_{project_id}/{stored_name}"
    logger.info("Attachment uploaded for project %s: %s", project_id, rel_path)
    return {
        "kind": "upload",
        "id": uuid.uuid4().hex,
        "label": label or Path(safe_name).stem,
        "path": rel_path,
    }
```

同时确保文件顶部 import 里有：`from pathlib import Path`、`from fastapi import File, Form, UploadFile`、`import uuid`、`from app.config import settings`（若已有则复用，不要重复 import）。

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py -v`
Expected: PASS（15 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/chapters.py backend/tests/test_chapter_structure_api.py
git commit -m "feat: 本项目附件上传端点 — label 必须标 Form()"
```

---

### Task 9: 确认目录接受整棵树 + 物化带上 match/attachments

**Files:**
- Modify: `backend/app/api/chapters.py`（`confirm_outline` 306-356 行、`_build_meta` 410-423 行）
- Test: `backend/tests/test_chapter_structure_api.py`

**Interfaces:**
- Consumes: Task 5 的 `_normalize_chapter_tree` / `_prune_attachments`
- Produces:
  - `POST /api/v1/bid/{project_id}/outline/confirm` 接受可选 body `{"chapters": [...]}`
  - `ProjectChapter.chapter_meta_json` 新增 `match` / `attachments` 两个键

- [ ] **Step 1: Write the failing test**

追加到 `backend/tests/test_chapter_structure_api.py`：

```python
class TestBuildMetaCarriesMatchAndAttachments:
    def test_match_and_attachments_are_merged(self):
        from app.api.chapters import _build_chapter_meta

        ch_data = {
            "number": "一",
            "title": "投标函",
            "match": {"status": "matched", "start": 10, "end": 40,
                      "corpus_hash": "sha1:x"},
            "attachments": [{"kind": "upload", "id": "u1", "label": "凭证",
                             "path": "project_p1/a.png"}],
        }
        meta = json.loads(_build_chapter_meta(ch_data, None))
        assert meta["match"]["start"] == 10
        assert meta["attachments"][0]["label"] == "凭证"

    def test_absent_match_is_omitted_not_null(self):
        from app.api.chapters import _build_chapter_meta

        meta = json.loads(_build_chapter_meta({"title": "甲"}, None))
        assert "match" not in meta
        assert meta["attachments"] == []


class TestGenerationChapterType:
    def test_mixed_maps_to_ai_generated(self):
        """mixed 落库归到 ai_generated——否则它进不了生成管线的任何一个循环，
        最终渲染成「（待补充…）」占位文本（spec §8.4）。"""
        from app.api.chapters import _generation_chapter_type

        assert _generation_chapter_type("mixed") == "ai_generated"

    def test_four_selectable_types_pass_through(self):
        from app.api.chapters import _generation_chapter_type

        for t in ("fixed_form", "table", "attachment", "ai_generated"):
            assert _generation_chapter_type(t) == t

    def test_unknown_falls_back_to_ai_generated(self):
        from app.api.chapters import _generation_chapter_type

        assert _generation_chapter_type("") == "ai_generated"
        assert _generation_chapter_type("胡说") == "ai_generated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py::TestBuildMetaCarriesMatchAndAttachments -v`
Expected: FAIL — `ImportError: cannot import name '_build_chapter_meta'`

- [ ] **Step 3: Implement**

把 `_materialise_chapters` 里**内嵌**的 `_build_meta`（410-423 行）提升为模块级函数并加上两个键：

```python
def _build_chapter_meta(ch_data: dict, part: dict | None, global_rules: dict | None = None) -> str:
    """组装 ProjectChapter.chapter_meta_json.

    ``match`` / ``attachments`` 是 2026-09-27 新增的键：前者是结构页固化的
    原文匹配引用（生成阶段据此切片），后者是附件清单。老项目没有这两个键，
    读取侧一律兜底（match 视作 status=na，attachments 视作 []）。
    """
    meta = {
        "number": ch_data.get("number", ""),
        "format_notes": ch_data.get("format_notes", ""),
        "scoring_context": ch_data.get("scoring_context", ""),
        "table_columns": ch_data.get("table_columns", []),
        "attachments": list(ch_data.get("attachments") or []),
    }
    if ch_data.get("match"):
        meta["match"] = ch_data["match"]
    if part:
        meta["table_schema"] = part.get("table_schema", [])
        meta["fixed_text_segments"] = part.get("fixed_text_segments", [])
        meta["signature_block"] = part.get("signature_block", {})
        meta["numbering_style"] = (global_rules or {}).get("numbering_style", "chinese_legal")
    return json.dumps(meta, ensure_ascii=False)
```

在 `_materialise_chapters` 里把两处 `_build_meta(...)` 调用改为 `_build_chapter_meta(ch_data, ..., global_rules)` / `_build_chapter_meta({}, part, global_rules)`，并删掉内嵌的 `_build_meta` 定义。

再加一个类型归一化函数（模块级）：

```python
def _generation_chapter_type(raw: str) -> str:
    """把章节类型归一化成生成管线认识的四种.

    ``mixed`` 是 AI 提取的产物，生成管线只处理 ``fixed_form`` / ``table`` /
    ``attachment`` / ``ai_generated`` —— ``mixed`` 落进"两个循环都进不去"的空档，
    最终渲染成「（待补充…）」占位文本。落库时归到 ``ai_generated``。
    （``chapter_structure_json`` 里仍保留 ``mixed``，结构页照常展示。）
    """
    return raw if raw in USER_SELECTABLE_TYPES else "ai_generated"
```

并把 `_materialise_chapters` 里取类型那行改成：

```python
    for ch_data in chapters:
        ch_type = _generation_chapter_type(ch_data.get("type", "ai_generated"))
```

改造 `confirm_outline`：

```python
class OutlineConfirmRequest(BaseModel):
    chapters: list[dict] | None = None


@router.post("/{project_id}/outline/confirm", response_model=OutlineConfirmResponse)
async def confirm_outline(
    project_id: str,
    payload: OutlineConfirmRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """冻结用户审阅/编辑后的章节结构并推进到「信息搜集」阶段.

    这是「目录确认门」唯一合法的状态出口：从 structure_ready 推进到 collecting。
    物化 ProjectChapter 行的逻辑与 /chapters/lock 共享 _materialise_chapters。

    body 可选带整棵树——带了就先存再物化，避免"用户改了忘保存"导致白改。
    不带时行为与历史完全一致。
    """
    # ...（原有的查询与状态校验不变）

    if payload is not None and payload.chapters:
        tree = _normalize_chapter_tree(payload.chapters)
        await _prune_attachments(tree, db)
        project.chapter_structure_json = json.dumps(tree, ensure_ascii=False)
        await db.flush()

    chapters_json = project.chapter_structure_json
    if not chapters_json or chapters_json in ("[]", "{}", ""):
        raise HTTPException(
            status_code=400,
            detail="章节数据为空，请先 POST /extract-chapters",
        )

    created, auto_added, _validation, added_from_rubric = await _materialise_chapters(project, db)
    # ...（其余不变）
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/Scripts/python.exe -m pytest tests/test_chapter_structure_api.py tests/test_generate_chapter_flow.py tests/test_outline_format.py -v`
Expected: PASS — 新用例通过，且**物化链路的既有用例不回归**

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/chapters.py backend/tests/test_chapter_structure_api.py
git commit -m "feat: 确认目录接受整棵树，物化带 match/attachments"
```

---

### Task 10: 生成 — 固定格式按存储区间切片

**Files:**
- Modify: `backend/app/services/ai_pipeline.py`（文件章节循环 1424-1483 行）
- Modify: `backend/app/services/template_filler.py`（新增 `rows_to_markdown`）
- Test: `backend/tests/test_generate_stored_match.py`（新建）

**Interfaces:**
- Consumes: Task 3 的 `match_tender_section` / `corpus_hash`、Task 4 的 `fill_fixed_form_section_from_template_with_tables`、Task 9 的 `chapter_meta_json["match"]`
- Produces:
  - `ai_pipeline._resolve_section_text(chapter, meta, requirements, full_text) -> tuple[str | None, list[str]]` —— 返回 `(原文片段, 警告列表)`
  - `template_filler.rows_to_markdown(rows: list[list]) -> str`

- [ ] **Step 1: Write the failing test**

创建 `backend/tests/test_generate_stored_match.py`：

```python
"""宏曦标书 - 生成阶段使用已固化匹配区间 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json

from app.services.ai_pipeline import _resolve_section_text
from app.services.tender_section_matcher import corpus_hash

CORPUS = "一、投标函\n\n致：某某单位\n我方已仔细阅读。\n\n二、开标一览表\n\n序号 | 服务内容\n"


class _Chapter:
    def __init__(self, title, meta):
        self.title = title
        self.chapter_meta_json = json.dumps(meta)


class TestResolveSectionText:
    def test_uses_stored_offsets_verbatim(self):
        """存了区间就直接切片——这是"所见即所得"的核心。"""
        start = CORPUS.index("一、投标函")
        ch = _Chapter("投标函", {"match": {
            "status": "matched", "start": start, "end": start + 20,
            "corpus_hash": corpus_hash(CORPUS),
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text == CORPUS[start:start + 20]
        assert warnings == []

    def test_stale_hash_triggers_relookup_and_warns(self):
        """场景：原文变了（重新解析过），存的位置不可信。"""
        ch = _Chapter("投标函", {"match": {
            "status": "matched", "start": 0, "end": 5,
            "corpus_hash": "sha1:stale",
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text is not None
        assert text.startswith("一、投标函")
        assert any("原文位置已变化" in w for w in warnings)

    def test_stale_hash_and_no_relookup_reports_error(self):
        ch = _Chapter("不存在的小节", {"match": {
            "status": "matched", "start": 0, "end": 5, "corpus_hash": "sha1:stale",
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text is None
        assert any("未匹配到招标原文" in w for w in warnings)

    def test_no_match_meta_returns_none_silently(self):
        """老项目没有 match 字段 → 交给老的按标题定位路径，不算警告。"""
        ch = _Chapter("投标函", {})
        text, warnings = _resolve_section_text(
            ch, {}, {"format_section_text": CORPUS}, None,
        )
        assert text is None
        assert warnings == []

    def test_out_of_range_offsets_are_treated_as_stale(self):
        ch = _Chapter("投标函", {"match": {
            "status": "matched", "start": 0, "end": 999999,
            "corpus_hash": corpus_hash(CORPUS),
        }})
        meta = json.loads(ch.chapter_meta_json)
        text, warnings = _resolve_section_text(
            ch, meta, {"format_section_text": CORPUS}, None,
        )
        assert text is not None
        assert warnings, "越界区间必须触发重匹配并告警"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_generate_stored_match.py -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_section_text'`

- [ ] **Step 3: Implement `_resolve_section_text`**

在 `backend/app/services/ai_pipeline.py` 中（`generate_from_chapter_structure` 之前）加入：

```python
def _resolve_section_text(
    chapter, meta: dict, requirements: dict, full_text: str | None,
) -> tuple[str | None, list[str]]:
    """把章节对应的招标原文片段取出来，返回 (片段, 警告列表).

    优先用结构页固化的 ``match`` 区间**原样切片**——用户预览时看到的就是最终
    进标书的那段。只有区间失效（语料 hash 变了、越界）才按标题重新匹配一次并
    告警；再失败则返回 None 交给调用方走 AI 兜底。

    没有 ``match`` 的老项目返回 ``(None, [])``——那不是异常，是"走老路径"。
    """
    from app.services.tender_section_matcher import corpus_hash, match_tender_section

    match = (meta or {}).get("match") or {}
    if not match or match.get("status") not in ("matched", "ambiguous"):
        return None, []

    corpus = requirements.get("format_section_text") or ""
    source = match.get("source")

    def _slice(text: str) -> str | None:
        start, end = match.get("start"), match.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            return None
        if start < 0 or end <= start or end > len(text):
            return None
        return text[start:end]

    if source == "full_text":
        candidate = full_text or ""
        if match.get("corpus_hash") == corpus_hash(candidate):
            sliced = _slice(candidate)
            if sliced:
                return sliced, []
    else:
        if match.get("corpus_hash") == corpus_hash(corpus):
            sliced = _slice(corpus)
            if sliced:
                return sliced, []

    # 区间失效 → 按标题重匹配
    relookup = match_tender_section(
        chapter.title,
        chapter_type="fixed_form",
        format_section_text=corpus,
        full_text=full_text,
        format_page_map=requirements.get("format_page_map") or [],
    )
    if relookup.best is not None:
        text_used = full_text if relookup.source == "full_text" else corpus
        return (
            text_used[relookup.best.start:relookup.best.end],
            [f"「{chapter.title}」的原文位置已变化，已按标题重新匹配"],
        )
    return None, [f"「{chapter.title}」未匹配到招标原文，将改由 AI 撰写"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/Scripts/python.exe -m pytest tests/test_generate_stored_match.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Wire it into the file-section loop**

在 `generate_from_chapter_structure` 里，把固定格式那一支（1432-1451 行）替换为：

```python
            file_content = ""
            section_warnings: list[str] = []
            if chapter.chapter_type == "fixed_form" and requirements.get("format_section_text"):
                try:
                    from app.services.template_filler import (
                        fill_fixed_form_section_from_template_with_tables,
                    )
                    section_text_override, section_warnings = _resolve_section_text(
                        chapter, chapter_meta, requirements, full_text,
                    )
                    if section_warnings:
                        format_warnings.extend(section_warnings)
                    file_content, table_fills = (
                        await fill_fixed_form_section_from_template_with_tables(
                            section_title=chapter.title,
                            format_section_text=requirements["format_section_text"],
                            format_tables=requirements.get("format_tables", []),
                            company_profile=company_profile,
                            requirements=requirements,
                            ai_adapter=ai_adapter,
                            section_text_override=section_text_override,
                        )
                    )
                    if file_content:
                        logger.info(
                            "Filled '%s' from tender template (preserves wording)",
                            chapter.title,
                        )
                except Exception as exc:
                    logger.warning(
                        "Scan-and-fill failed for '%s': %s; falling back to AI generation",
                        chapter.title, exc,
                    )
                    section_warnings.append(f"「{chapter.title}」原文回填失败，已改用 AI 撰写")
                    format_warnings.extend(section_warnings)
```

在 `generate_from_chapter_structure` 开头（`file_chapters_output = []` 附近）加一个收集器：

```python
    format_warnings: list[str] = []   # 累计各类降级/兜底，最后写进校验报告
```

并在 `format_verification` 阶段（约 2022-2033 行）把 `format_warnings` 并入报告：

```python
        verification = verify_format(chapters_payload, format_template)
        if format_warnings:
            verification.setdefault("warnings", [])
            verification["warnings"].extend(format_warnings)
```

> `full_text` 需要在循环前取一次：`full_text = load_full_text(requirements, settings.UPLOAD_DIR)`（`from app.services.tender_corpus import load_full_text`、`from app.config import settings`）。

- [ ] **Step 6: Add `rows_to_markdown` to template_filler**

在 `backend/app/services/template_filler.py` 中加入（紧邻 `batch_fill_tables`）：

```python
def rows_to_markdown(rows: list[list]) -> str:
    """把 pdfplumber 的行列结构转成 markdown 表格（render_engine 走 markdown 渲染）.

    首行为表头。Cell 为 None 或空串时写空字符串而不是 "None"——"None" 会一路
    印进成品标书。
    """
    if not rows:
        return ""

    def _cell(v) -> str:
        text = "" if v is None else str(v)
        return text.replace("|", "\\|").replace("\n", " ").strip()

    header = [_cell(c) for c in rows[0]]
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows[1:]:
        cells = [_cell(c) for c in row]
        cells += [""] * (len(header) - len(cells))
        out.append("| " + " | ".join(cells[:len(header)]) + " |")
    return "\n".join(out)
```

测试追加到 `backend/tests/test_template_filler.py`：

```python
class TestRowsToMarkdown:
    def test_header_and_body(self):
        md = rows_to_markdown([["序号", "服务内容"], ["1", "安保"]])
        assert md.splitlines()[0] == "| 序号 | 服务内容 |"
        assert md.splitlines()[1] == "| --- | --- |"
        assert md.splitlines()[2] == "| 1 | 安保 |"

    def test_none_cell_becomes_empty_not_none_string(self):
        md = rows_to_markdown([["A"], [None]])
        assert "None" not in md

    def test_pipe_in_cell_is_escaped(self):
        md = rows_to_markdown([["A|B"], ["x"]])
        assert "A\\|B" in md

    def test_ragged_row_is_padded(self):
        md = rows_to_markdown([["A", "B", "C"], ["1"]])
        assert md.splitlines()[2] == "| 1 |  |  |"

    def test_empty_rows(self):
        assert rows_to_markdown([]) == ""
```

- [ ] **Step 7: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_generate_stored_match.py tests/test_template_filler.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/ai_pipeline.py backend/app/services/template_filler.py backend/tests/test_generate_stored_match.py backend/tests/test_template_filler.py
git commit -m "feat: 固定格式章节按结构页固化的区间切片，失效才回退重匹配"
```

---

### Task 11: 生成 — 表格章节照招标表格填

现状：`chapter_type == "table"` **掉进 `generate_file_section`（AI 自由生成）**，用户选了「表格」拿到 AI 编的表。本任务把它接到招标原文的表格上。

**Files:**
- Modify: `backend/app/services/ai_pipeline.py`（文件章节循环内新增 `table` 分支）
- Test: `backend/tests/test_generate_table_chapter.py`（新建）

**Interfaces:**
- Consumes: Task 10 的 `_resolve_section_text` 与 `rows_to_markdown`、Task 4 的 `fill_fixed_form_section_from_template_with_tables`、既有的 `batch_fill_tables` / `build_variable_values`
- Produces:
  - `ai_pipeline._generate_table_chapter(chapter, meta, requirements, company_profile, ai_adapter, full_text) -> tuple[str, list[str]]`

- [ ] **Step 1: Write the failing test**

创建 `backend/tests/test_generate_table_chapter.py`：

```python
"""宏曦标书 - 表格类章节生成 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_pipeline import _generate_table_chapter

CORPUS = "一、开标一览表\n\n序号 | 服务内容 | 报价\n"
TABLES = [{
    "page": 33, "table_index": 0,
    "rows": [["序号", "服务内容", "报价"], ["1", "安保服务", ""]],
}]


class _Chapter:
    def __init__(self, title, meta):
        self.title = title
        self.chapter_meta_json = json.dumps(meta)


@pytest.mark.asyncio
async def test_uses_tender_table_columns_not_ai():
    """场景：用户选了「表格」——必须照招标表格的列定义，不是 AI 编的表。"""
    ch = _Chapter("开标一览表", {"match": {
        "status": "matched", "source": "table", "table_index": 0,
        "start": 0, "end": len(CORPUS),
    }})
    meta = json.loads(ch.chapter_meta_json)

    content, warnings = await _generate_table_chapter(
        ch, meta, {"format_section_text": CORPUS, "format_tables": TABLES},
        company_profile={"company_name": "某某公司"}, ai_adapter=None, full_text=None,
    )
    assert "| 序号 | 服务内容 | 报价 |" in content
    assert "安保服务" in content


@pytest.mark.asyncio
async def test_falls_back_to_declared_columns_when_no_table():
    """表格索引失效且重匹配不到 → 只出 table_columns 声明的空表头，不编数据。"""
    ch = _Chapter("开标一览表", {
        "table_columns": ["序号", "服务内容"],
        "match": {"status": "missing"},
    })
    meta = json.loads(ch.chapter_meta_json)

    content, warnings = await _generate_table_chapter(
        ch, meta, {"format_section_text": CORPUS, "format_tables": []},
        company_profile={}, ai_adapter=None, full_text=None,
    )
    assert "| 序号 | 服务内容 |" in content
    assert any("未找到招标表格" in w for w in warnings)


@pytest.mark.asyncio
async def test_no_table_and_no_columns_returns_empty_with_warning():
    ch = _Chapter("某表", {"match": {"status": "missing"}})
    meta = json.loads(ch.chapter_meta_json)

    content, warnings = await _generate_table_chapter(
        ch, meta, {"format_section_text": CORPUS, "format_tables": []},
        company_profile={}, ai_adapter=None, full_text=None,
    )
    assert content == ""
    assert warnings


@pytest.mark.asyncio
async def test_never_calls_ai_free_generation():
    """表格章节不得再走 AI 自由生成——这正是要修的 bug。"""
    ch = _Chapter("开标一览表", {"match": {"status": "matched", "source": "table",
                                           "table_index": 0, "start": 0,
                                           "end": len(CORPUS)}})
    meta = json.loads(ch.chapter_meta_json)
    with patch("app.services.template_filler.generate_file_section",
               new=AsyncMock(side_effect=AssertionError("不该调用 AI 自由生成"))):
        content, _ = await _generate_table_chapter(
            ch, meta, {"format_section_text": CORPUS, "format_tables": TABLES},
            company_profile={}, ai_adapter=None, full_text=None,
        )
    assert content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_generate_table_chapter.py -v`
Expected: FAIL — `ImportError: cannot import name '_generate_table_chapter'`

- [ ] **Step 3: Implement**

在 `backend/app/services/ai_pipeline.py` 中加入：

```python
async def _generate_table_chapter(
    chapter, meta: dict, requirements: dict, company_profile: dict, ai_adapter, full_text: str | None,
) -> tuple[str, list[str]]:
    """按招标原文的表格定义产出表格类章节内容，返回 (markdown, 警告列表).

    取材顺序：
      1. 结构页固化的 ``match.table_index`` 指向的表
      2. 表索引失效 → 按标题重匹配一张
      3. 都没有 → 只输出 ``table_columns`` 声明的空表头（**绝不编数据**）

    无论哪条路径都不调用 AI 自由生成——那正是本次要修的 bug。
    """
    from app.services.template_filler import (
        batch_fill_tables, build_variable_values,
        fill_fixed_form_section_from_template_with_tables, rows_to_markdown,
    )

    warnings: list[str] = []
    tables = requirements.get("format_tables") or []
    match = (meta or {}).get("match") or {}
    table_index = match.get("table_index")

    if table_index is None or not (0 <= table_index < len(tables)):
        if table_index is not None:
            warnings.append(f"「{chapter.title}」的表格索引已失效，已按标题重新匹配")
        from app.services.tender_section_matcher import match_tender_section
        relookup = match_tender_section(
            chapter.title, chapter_type="table",
            format_section_text=requirements.get("format_section_text"),
            full_text=full_text,
            format_tables=tables,
            format_page_map=requirements.get("format_page_map") or [],
        )
        table_index = relookup.table_index

    if table_index is not None and 0 <= table_index < len(tables):
        rows = [list(r) for r in (tables[table_index].get("rows") or [])]
        # 让 AI 标出单元格里哪些位置要填值（拿不到就原样输出，不写占位符）
        section_text, _w = _resolve_section_text(chapter, meta, requirements, full_text)
        variables = build_variable_values(company_profile, requirements)
        if section_text:
            try:
                _filled, table_fills = await fill_fixed_form_section_from_template_with_tables(
                    section_title=chapter.title,
                    format_section_text=requirements.get("format_section_text") or "",
                    format_tables=[tables[table_index]],
                    company_profile=company_profile,
                    requirements=requirements,
                    ai_adapter=ai_adapter,
                    section_text_override=section_text,
                )
                if table_fills:
                    rows = batch_fill_tables(
                        [tables[table_index]], table_fills, variables,
                    )[0]["rows"]
            except Exception as exc:
                logger.warning("Table fill for '%s' failed: %s", chapter.title, exc)
                warnings.append(f"「{chapter.title}」表格填值失败，已输出空白模板")
        return rows_to_markdown(rows), warnings

    columns = meta.get("table_columns") or []
    if columns:
        warnings.append(f"「{chapter.title}」未找到招标表格，已输出空表头模板")
        return rows_to_markdown([list(columns)]), warnings

    warnings.append(f"「{chapter.title}」未找到招标表格，且无列定义，无法生成")
    return "", warnings
```

在文件章节循环里，把 `table` 接到这个函数（在 `if chapter.chapter_type in ("fixed_form", "table"):` 块内、`fixed_form` 那支之后）：

```python
            elif chapter.chapter_type == "table":
                file_content, table_warnings = await _generate_table_chapter(
                    chapter, chapter_meta, requirements, company_profile,
                    ai_adapter, full_text,
                )
                format_warnings.extend(table_warnings)
```

并把原来那段 `if not file_content: generate_file_section(...)` 的 AI 兜底**限制为只在 `fixed_form` 生效**：

```python
            if not file_content and chapter.chapter_type == "fixed_form":
                try:
                    file_content = await generate_file_section(
                        section_type=chapter.title,
                        company_profile=company_profile,
                        requirements=requirements,
                        project_name=requirements.get("project_name", "") if requirements else "",
                        ai_adapter=ai_adapter,
                    )
                    format_warnings.append(
                        f"「{chapter.title}」未匹配到招标原文，已由 AI 撰写"
                    )
                except Exception as exc:
                    logger.warning("File section '%s' generation failed: %s", chapter.title, exc)
```

- [ ] **Step 4: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_generate_table_chapter.py tests/test_generate_chapter_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai_pipeline.py backend/tests/test_generate_table_chapter.py
git commit -m "fix: 表格章节照招标表格填 — 不再掉进 AI 自由生成"
```

---

### Task 12: 生成 — 附件章节出图

现状：`attachment` 类型在生成管线的两个循环里都进不去，最终渲染成一行占位文本。

**Files:**
- Modify: `backend/app/services/ai_pipeline.py`（文件章节循环内新增 `attachment` 分支）
- Test: `backend/tests/test_generate_attachment_chapter.py`（新建）

**Interfaces:**
- Consumes: Task 9 的 `chapter_meta_json["attachments"]`、`render_engine._render_image_marker` 认识的 `[IMG:path|label]` 标记
- Produces:
  - `ai_pipeline._generate_attachment_chapter(chapter, meta) -> tuple[str, list[str]]`

- [ ] **Step 1: Write the failing test**

创建 `backend/tests/test_generate_attachment_chapter.py`：

```python
"""宏曦标书 - 附件类章节生成 单元测试.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import json

from app.services.ai_pipeline import _generate_attachment_chapter


class _Chapter:
    def __init__(self, title, meta):
        self.title = title
        self.chapter_meta_json = json.dumps(meta)


def test_emits_img_markers_in_order():
    ch = _Chapter("投标保证金凭证", {"attachments": [
        {"kind": "upload", "id": "u1", "label": "保证金缴纳凭证", "path": "project_p1/a.png"},
        {"kind": "qualification", "id": "q1", "label": "基本账户开户许可证", "path": "ocr/b.png"},
    ]})
    content, warnings = _generate_attachment_chapter(ch, {"attachments": json.loads(ch.chapter_meta_json)["attachments"]})
    assert content.splitlines() == [
        "[IMG:project_p1/a.png|保证金缴纳凭证]",
        "[IMG:ocr/b.png|基本账户开户许可证]",
    ]
    assert warnings == []


def test_empty_attachments_keeps_placeholder_and_warns():
    """场景：用户选了附件类型但一个都没挂。"""
    ch = _Chapter("其他材料", {})
    content, warnings = _generate_attachment_chapter(ch, {})
    assert content == ""
    assert any("未挂载任何附件" in w for w in warnings)


def test_label_defaults_to_filename_when_absent():
    ch = _Chapter("附件", {"attachments": [{"kind": "upload", "id": "u1", "path": "p/a.png"}]})
    content, _ = _generate_attachment_chapter(ch, json.loads(ch.chapter_meta_json))
    assert content == "[IMG:p/a.png|a.png]"


def test_attachment_without_path_is_skipped_and_warned():
    ch = _Chapter("附件", {"attachments": [
        {"kind": "upload", "id": "u1", "label": "坏记录"},
        {"kind": "upload", "id": "u2", "label": "好的", "path": "p/a.png"},
    ]})
    content, warnings = _generate_attachment_chapter(ch, json.loads(ch.chapter_meta_json))
    assert "坏记录" not in content
    assert any("坏记录" in w for w in warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/Scripts/python.exe -m pytest tests/test_generate_attachment_chapter.py -v`
Expected: FAIL — `ImportError: cannot import name '_generate_attachment_chapter'`

- [ ] **Step 3: Implement**

在 `backend/app/services/ai_pipeline.py` 中加入：

```python
def _generate_attachment_chapter(chapter, meta: dict) -> tuple[str, list[str]]:
    """把附件清单渲染成 ``[IMG:path|label]`` 标记行，返回 (内容, 警告列表).

    标记由 ``render_engine._render_image_marker`` 就地出图，两条渲染分支都认。
    去重不在这一步做——同一张扫描件挂了多个章节时，由保存时的
    ``_prune_attachments``（按内容 md5）拦掉，与
    ``materials_injection.drop_already_embedded`` 同一口径。
    """
    warnings: list[str] = []
    attachments = (meta or {}).get("attachments") or []
    if not attachments:
        return "", [f"「{chapter.title}」未挂载任何附件，将渲染为占位页"]

    lines: list[str] = []
    for att in attachments:
        path = (att or {}).get("path") or ""
        label = (att or {}).get("label") or ""
        if not path:
            warnings.append(
                f"「{chapter.title}」的附件「{label or (att or {}).get('id')}」缺少文件路径，已跳过"
            )
            continue
        if not label:
            label = path.rsplit("/", 1)[-1]
        lines.append(f"[IMG:{path}|{label}]")
    return "\n".join(lines), warnings
```

在文件章节循环里加上：

```python
            elif chapter.chapter_type == "attachment":
                file_content, att_warnings = _generate_attachment_chapter(
                    chapter, chapter_meta,
                )
                format_warnings.extend(att_warnings)
```

- [ ] **Step 4: Write the missing-file test**

生成阶段不读文件（只出标记），**读文件发生在导出渲染时**。所以「附件文件被删掉」这条 Review Focus 的测试要打在渲染层。追加到 `backend/tests/test_render_images.py`：

```python
def test_missing_image_file_is_skipped_not_crashed(tmp_path, monkeypatch):
    """场景：用户清理 uploads，附件引用的文件没了 → 跳过该张并继续导出。"""
    from docx import Document

    from app.config import settings
    from app.services import render_engine

    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    doc = Document()

    assert render_engine._render_image_marker(
        doc, "[IMG:不存在/的文件.png|丢失的凭证]", None,
    ) is True, "标记行应当被识别（返回 True），只是插不进图"

    assert len(doc.inline_shapes) == 0, "文件不存在时不能插图，也不能抛异常"
```

- [ ] **Step 5: Run tests**

Run: `venv/Scripts/python.exe -m pytest tests/test_generate_attachment_chapter.py tests/test_render_images.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ai_pipeline.py backend/tests/test_generate_attachment_chapter.py backend/tests/test_render_images.py
git commit -m "feat: 附件类章节出图 — 此前一直渲染成占位文本"
```

---

### Task 13: 前端 — API 层与拖拽排序

前端**没有测试框架**（`package.json` 里无 vitest/jest），所以本阶段所有任务的验证口径统一是：`npx tsc --noEmit` 通过 + `npm run build` 通过 + 下面每步列出的手工冒烟。新增的纯函数（如 `moveNode`）靠手工冒烟的边界用例覆盖。

**Files:**
- Modify: `frontend/src/api/outline.ts`
- Modify: `frontend/src/components/OutlineEditor/OutlineTree.tsx`
- Test: 手工冒烟（见 Step 5）

**Interfaces:**
- Consumes: Task 5 / 7 / 8 的三个端点
- Produces（`frontend/src/api/outline.ts`）：
  - `OutlineMatchResult` 接口：`{status, source, best: OutlineMatchCandidate | null, candidates: OutlineMatchCandidate[], table_index: number | null, corpus_hash: string | null}`
  - `OutlineMatchCandidate`：`{title, raw, level, start, end, score, page: number | null, preview: string, preview_truncated: boolean}`
  - `OutlineAttachment`：`{kind, id, label, path}`
  - `OutlineChapter` 新增可选字段 `match?: OutlineMatchResult | null`、`attachments?: OutlineAttachment[]`
  - `outlineApi.saveStructure(projectId, chapters)`
  - `outlineApi.match(projectId, title, type)`
  - `outlineApi.uploadAttachment(projectId, file, label)`
  - `outlineApi.confirm(projectId, chapters?)`（签名扩展，不传时行为不变）
- Produces（`OutlineTree.tsx`）：
  - `moveNode(chapters, fromPath, toPath, position) -> OutlineChapter[]`
  - `insertAt(chapters, parentPath, index, node) -> OutlineChapter[]`
  - `isDescendant(ancestorPath, candidatePath) -> boolean`
  - `shiftPath(path, removedPath) -> number[]`

- [ ] **Step 1: Extend the API module**

在 `frontend/src/api/outline.ts` 的 `OutlineChapter` 接口里加两个字段：

```ts
export type ChapterType = 'fixed_form' | 'table' | 'ai_generated' | 'attachment' | 'mixed'

/** 用户可在结构页选择的类型（mixed 由 AI 提取产生，不可选） */
export const USER_SELECTABLE_TYPES: ChapterType[] = [
  'fixed_form', 'table', 'attachment', 'ai_generated',
]

export interface OutlineMatchCandidate {
  title: string
  raw: string
  level: number
  start: number
  end: number
  score: number
  page: number | null
  preview: string
  preview_truncated: boolean
}

export interface OutlineMatchResult {
  status: 'matched' | 'ambiguous' | 'missing' | 'na'
  source: string | null
  best: OutlineMatchCandidate | null
  candidates: OutlineMatchCandidate[]
  table_index: number | null
  /** 表格章节的命中内容（best 为空时抽屉靠它显示） */
  table_preview: string | null
  /** auto=系统最佳；manual=用户从候选里点选过 */
  picked: 'auto' | 'manual'
  corpus_hash: string | null
}

export interface OutlineAttachment {
  kind: 'qualification' | 'personnel_cert' | 'contract' | 'upload'
  id: string
  label: string
  path: string
}
```

并在 `OutlineChapter` 里、`children` 之前插入：

```ts
  /** 结构页固化的招标原文匹配引用（生成阶段据此切片） */
  match?: OutlineMatchResult | null
  /** 附件清单（仅 type === 'attachment' 有意义） */
  attachments?: OutlineAttachment[]
```

在 `outlineApi` 里替换 `confirm` 并新增三个方法：

```ts
  /** 保存整棵章节结构树（拖拽/改名/改类型/挂附件后调用） */
  saveStructure: async (projectId: string, chapters: OutlineChapter[]): Promise<void> => {
    await client.put(`/bid/${projectId}/chapter-structure`, { chapters })
  },

  /** 试匹配：给定标题与类型，返回命中的招标原文片段或候选 */
  match: async (
    projectId: string,
    title: string,
    type: ChapterType,
  ): Promise<OutlineMatchResult> => {
    const res = await client.post<OutlineMatchResult>(
      `/bid/${projectId}/chapter-structure/match`,
      { title, type },
    )
    return res.data
  },

  /** 上传本项目专用附件，返回一条可塞进章节节点的附件记录 */
  uploadAttachment: async (
    projectId: string,
    file: File,
    label: string,
  ): Promise<OutlineAttachment> => {
    const form = new FormData()
    form.append('file', file)
    // label 是标量字段：后端用 Form() 接，必须走 FormData 而不是 query
    form.append('label', label)
    const res = await client.post<OutlineAttachment>(
      `/bid/${projectId}/attachments/upload`,
      form,
    )
    return res.data
  },

  /** 「目录确认门」唯一出口。带 chapters 时先存再物化，避免改了忘保存 */
  confirm: async (
    projectId: string,
    chapters?: OutlineChapter[],
  ): Promise<OutlineConfirmResponse> => {
    const res = await client.post<OutlineConfirmResponse>(
      `/bid/${projectId}/outline/confirm`,
      chapters ? { chapters } : {},
    )
    return res.data
  },
```

- [ ] **Step 2: Add the tree-move helpers**

在 `OutlineTree.tsx` 的 `insertNode` 之后加入：

```tsx
/** candidatePath 是否位于 ancestorPath 的子树内 */
function isDescendant(ancestorPath: number[], candidatePath: number[]): boolean {
  if (candidatePath.length <= ancestorPath.length) return false
  return ancestorPath.every((v, i) => candidatePath[i] === v)
}

/** 从 removedPath 删掉一个节点后，同一父级下排在它后面的兄弟下标要减一 */
function shiftPath(path: number[], removedPath: number[]): number[] {
  const parent = removedPath.slice(0, -1)
  const sameParent =
    path.length > parent.length && parent.every((v, i) => path[i] === v)
  if (!sameParent) return path
  const out = [...path]
  if (out[parent.length] > removedPath[removedPath.length - 1]) {
    out[parent.length] -= 1
  }
  return out
}

/** 在 parentPath 的第 index 个位置插入节点 */
function insertAt(
  chapters: OutlineChapter[],
  parentPath: number[],
  index: number,
  node: OutlineChapter,
): OutlineChapter[] {
  const clamp = (n: number, max: number) => Math.max(0, Math.min(n, max))
  if (parentPath.length === 0) {
    const next = [...chapters]
    next.splice(clamp(index, next.length), 0, node)
    return next
  }
  const [head, ...rest] = parentPath
  return chapters.map((ch, i) => {
    if (i !== head) return ch
    if (rest.length === 0) {
      const kids = [...(ch.children ?? [])]
      kids.splice(clamp(index, kids.length), 0, node)
      return { ...ch, children: kids }
    }
    return { ...ch, children: insertAt(ch.children ?? [], rest, index, node) }
  })
}

/**
 * 把 fromPath 的节点移动到 toPath 的相对位置。
 * position: 'before' | 'after' 挂成兄弟；'inside' 挂成子节点。
 * 拖进自己的子树会被拒绝（否则节点会被复制成自己的后代）。
 */
function moveNode(
  chapters: OutlineChapter[],
  fromPath: number[],
  toPath: number[],
  position: 'before' | 'after' | 'inside',
): OutlineChapter[] {
  const node = getNode(chapters, fromPath)
  if (!node) return chapters
  if (isDescendant(fromPath, toPath)) return chapters
  if (fromPath.join('.') === toPath.join('.')) return chapters

  const without = removeNode(chapters, fromPath)
  const target = shiftPath(toPath, fromPath)

  if (position === 'inside') {
    return updateNode(without, target, (n) => ({
      ...n,
      children: [...(n.children ?? []), node],
    }))
  }
  const parentPath = target.slice(0, -1)
  const index = target[target.length - 1] + (position === 'after' ? 1 : 0)
  return insertAt(without, parentPath, index, node)
}
```

- [ ] **Step 3: Wire dragging onto the Tree**

`OutlineTree.tsx` 里 import 加上 `TreeProps`：

```tsx
import type { DataNode, TreeProps } from 'antd/es/tree'
```

在组件内、`handleSelect` 之后加入：

```tsx
  const handleDrop: TreeProps['onDrop'] = (info) => {
    const dragPath = String(info.dragNode.key).split('.').map(Number)
    const dropPath = String(info.node.key).split('.').map(Number)
    // antd 的 dropPosition 是相对投影值：-1 之前、0 之内、1 之后
    const dropPos = info.node.pos.split('-')
    const dropOffset = info.dropPosition - Number(dropPos[dropPos.length - 1])

    const position: 'before' | 'after' | 'inside' =
      info.dropToGap ? (dropOffset < 0 ? 'before' : 'after') : 'inside'

    const next = moveNode(chapters, dragPath, dropPath, position)
    if (next === chapters) return
    onChange(next)
  }
```

并把 `<Tree ...>` 改为：

```tsx
            <Tree
              treeData={treeData}
              defaultExpandAll
              selectedKeys={selectedKey ? [selectedKey] : []}
              onSelect={handleSelect}
              draggable
              onDrop={handleDrop}
              blockNode
              showLine
            />
```

同时把底部脚注（337 行）改成反映新语义：

```tsx
        <div style={{ marginTop: 8, color: '#999', fontSize: 12 }}>
          共 {chapters.length} 个顶级章节 / {flatList.length} 个节点（含子章节）。拖拽可调整顺序与层级，修改会自动保存。
        </div>
```

- [ ] **Step 4: Type-check and build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 无 TS 错误，构建成功

- [ ] **Step 5: Manual smoke**

在浏览器打开任一 `structure_ready` 项目的 `/projects/{id}/outline`，逐条确认：

1. 拖到某节点**上方/下方**（节点间空隙）→ 变同级兄弟，顺序变化
2. 拖到某节点**正中间**（高亮整行）→ 变成它的子节点
3. 把父节点拖进**自己的子节点**里 → **无变化**（不允许）
4. 把第一个节点拖到最后一个节点之后 → 顺序正确、不重复、不丢节点
5. 拖动后刷新页面 → 顺序仍在（`saveStructure` 已防抖落库）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/outline.ts frontend/src/components/OutlineEditor/OutlineTree.tsx
git commit -m "feat: 章节结构支持拖拽排序与层级调整"
```

---

### Task 14: 前端 — 类型下拉、匹配徽标与抽屉

**Files:**
- Modify: `frontend/src/components/OutlineEditor/OutlineTree.tsx`
- Create: `frontend/src/components/OutlineEditor/MatchDrawer.tsx`
- Test: 手工冒烟（见 Step 4）

**Interfaces:**
- Consumes: Task 13 的 `outlineApi.match` / `USER_SELECTABLE_TYPES` / `OutlineMatchResult`
- Produces:
  - `MatchDrawer` 组件，props：`{ open: boolean, loading: boolean, result: OutlineMatchResult | null, title: string, onPick: (c: OutlineMatchCandidate) => void, onClose: () => void }`
  - `OutlineTreeProps` 新增 `onMatchChange`（可选）：`(path: number[], result: OutlineMatchResult | null) => void`

> 匹配的触发与落库由父组件（`OutlineConfirm`）统一负责：`OutlineTree` 只负责"节点标题/类型变了，通知父组件去重新匹配"。这样防抖与竞态处理只有一处。

- [ ] **Step 1: Create the drawer component**

创建 `frontend/src/components/OutlineEditor/MatchDrawer.tsx`：

```tsx
import React from 'react'
import { Drawer, Space, Tag, Alert, List, Typography, Empty, Spin } from 'antd'
import type { OutlineMatchCandidate, OutlineMatchResult } from '../../api/outline'

interface MatchDrawerProps {
  open: boolean
  loading: boolean
  title: string
  result: OutlineMatchResult | null
  onPick: (c: OutlineMatchCandidate) => void
  onClose: () => void
}

const STATUS_META: Record<string, { color: string; text: string }> = {
  matched: { color: 'success', text: '已匹配' },
  ambiguous: { color: 'warning', text: '多个候选' },
  missing: { color: 'error', text: '未匹配' },
  na: { color: 'default', text: '不适用（AI 生成）' },
}

const SOURCE_LABEL: Record<string, string> = {
  format_section: '投标文件格式章节',
  full_text: '招标文件全文',
  table: '招标文件表格',
}

const MatchDrawer: React.FC<MatchDrawerProps> = ({
  open, loading, title, result, onPick, onClose,
}) => {
  const meta = result ? STATUS_META[result.status] ?? STATUS_META.missing : null

  return (
    <Drawer
      title={`「${title}」的原文匹配`}
      open={open}
      onClose={onClose}
      width={560}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>
      ) : !result ? (
        <Empty description="尚未匹配" />
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <Tag color={meta!.color}>{meta!.text}</Tag>
            {result.source && <Tag>{SOURCE_LABEL[result.source] ?? result.source}</Tag>}
            {result.best?.page != null && <Tag color="blue">第 {result.best.page} 页</Tag>}
          </Space>

          {result.status === 'missing' && (
            <Alert
              type="error"
              showIcon
              message="招标文件里没找到这一节"
              description="该章节不会照抄招标原文，将由 AI 撰写。建议改标题、改类型，或从下方候选中选一个。"
            />
          )}
          {result.status === 'ambiguous' && (
            <Alert
              type="warning"
              showIcon
              message="有多个候选，请确认用哪一个"
              description="点选下面任意一条即固化该选择，生成时就用它。"
            />
          )}

          {result.picked === 'manual' && <Tag color="cyan">已手动指定</Tag>}

          {(result.best || result.table_preview) && (
            <>
              <Typography.Text strong>
                {result.best ? `当前命中：${result.best.raw}` : '命中的招标表格'}
              </Typography.Text>
              <div
                style={{
                  whiteSpace: 'pre-wrap',
                  maxHeight: 320,
                  overflow: 'auto',
                  background: '#fafafa',
                  border: '1px solid #f0f0f0',
                  borderRadius: 6,
                  padding: 12,
                  fontSize: 12,
                }}
              >
                {result.best ? result.best.preview : result.table_preview}
                {result.best?.preview_truncated && (
                  <div style={{ color: '#999', marginTop: 8 }}>
                    （预览已截断，生成时使用完整原文）
                  </div>
                )}
              </div>
            </>
          )}

          {result.candidates.length > 0 && (
            <List
              size="small"
              header={`候选（${result.candidates.length}）`}
              dataSource={result.candidates}
              renderItem={(c) => (
                <List.Item
                  style={{ cursor: 'pointer' }}
                  onClick={() => onPick(c)}
                  actions={[
                    <Typography.Text type="secondary" key="s">
                      {c.score.toFixed(2)}
                    </Typography.Text>,
                  ]}
                >
                  <Space>
                    <Tag>层级 {c.level}</Tag>
                    <span>{c.raw}</span>
                    {c.page != null && <Tag color="blue">第 {c.page} 页</Tag>}
                  </Space>
                </List.Item>
              )}
            />
          )}
        </Space>
      )}
    </Drawer>
  )
}

export default MatchDrawer
```

- [ ] **Step 2: Add the type dropdown and status badge to the tree**

在 `OutlineTree.tsx` 顶部 import 加上 `Select`、`Badge`，并在 `OutlineTreeProps` 里加回调：

```tsx
interface OutlineTreeProps {
  chapters: OutlineChapter[]
  onChange: (next: OutlineChapter[]) => void
  /** 标题或类型变化时通知父组件重新匹配（父组件负责防抖与落库） */
  onMatchRequest?: (path: number[], title: string, type: ChapterType) => void
  /** 点匹配徽标时通知父组件打开抽屉 */
  onOpenMatch?: (path: number[]) => void
}
```

`buildTreeData` 改为接受两个回调并渲染下拉与徽标：

```tsx
const MATCH_BADGE: Record<string, { status: 'success' | 'warning' | 'error' | 'default'; text: string }> = {
  matched: { status: 'success', text: '已匹配' },
  ambiguous: { status: 'warning', text: '多候选' },
  missing: { status: 'error', text: '未匹配' },
  na: { status: 'default', text: '—' },
}

function buildTreeData(
  chapters: OutlineChapter[],
  basePath: number[],
  onTypeChange: (path: number[], type: ChapterType) => void,
  onOpenMatch: (path: number[]) => void,
): DataNode[] {
  return chapters.map((ch, idx) => {
    const path = [...basePath, idx]
    const type = (ch.type ?? (ch as any).chapter_type ?? 'ai_generated') as ChapterType
    const matchStatus = ch.match?.status ?? (type === 'ai_generated' ? 'na' : 'missing')
    const badge = MATCH_BADGE[matchStatus] ?? MATCH_BADGE.missing
    const needsMatch = type === 'fixed_form' || type === 'table'

    return {
      key: path.join('.'),
      title: (
        <Space size={4}>
          <Select
            size="small"
            value={type}
            style={{ width: 110 }}
            onClick={(e) => e.stopPropagation()}
            onChange={(v) => onTypeChange(path, v as ChapterType)}
            options={USER_SELECTABLE_TYPES.map((t) => ({
              value: t,
              label: TYPE_LABELS[t],
            }))}
          />
          <span style={{ fontWeight: 500 }}>{ch.title || '(未命名)'}</span>
          {ch.source === 'scoring_rubric' && (
            <Tag color="gold" style={{ marginRight: 0 }}>来自评标办法</Tag>
          )}
          {needsMatch && (
            <Tooltip title={ch.match?.best?.raw ? `命中：${ch.match.best.raw}` : badge.text}>
              <Tag
                color={badge.status === 'default' ? 'default' : badge.status}
                style={{ marginRight: 0, cursor: 'pointer' }}
                onClick={(e) => { e.stopPropagation(); onOpenMatch(path) }}
              >
                {badge.text}
              </Tag>
            </Tooltip>
          )}
          {ch.attachments && ch.attachments.length > 0 && (
            <Tag color="orange" style={{ marginRight: 0 }}>
              {ch.attachments.length} 个附件
            </Tag>
          )}
        </Space>
      ),
      children: ch.children ? buildTreeData(ch.children, path, onTypeChange, onOpenMatch) : undefined,
    }
  })
}
```

在组件里接上（`treeData` 的 `useMemo` 依赖要跟着加）：

```tsx
  const handleTypeChange = (path: number[], type: ChapterType) => {
    const next = updateNode(chapters, path, (n) => ({ ...n, type }))
    onChange(next)
    const node = getNode(next, path)
    if (node) onMatchRequest?.(path, node.title, type)
  }

  const treeData = useMemo(
    () => buildTreeData(chapters, [], handleTypeChange, (p) => onOpenMatch?.(p)),
    [chapters, onMatchRequest, onOpenMatch],
  )
```

`commitTitle` 也要在改完标题后触发重新匹配：

```tsx
    const commitTitle = () => {
      if (!editingTitle || !selectedPath) {
        setEditingTitle(null)
        return
      }
      const next = updateNode(chapters, selectedPath, (n) => ({
        ...n, title: titleDraft.trim() || n.title,
      }))
      onChange(next)
      const node = getNode(next, selectedPath)
      if (node) {
        onMatchRequest?.(
          selectedPath,
          node.title,
          (node.type ?? 'ai_generated') as ChapterType,
        )
      }
      setEditingTitle(null)
    }
```

同时删掉 `buildTreeData` 里原来那行 `TYPE_COLORS` 标签（类型现在由下拉承担），`TYPE_COLORS` 若无其他引用则一并删除。

- [ ] **Step 3: Wire it in `OutlineConfirm.tsx`**

在 `OutlineConfirm.tsx` 里加入防抖匹配、抽屉与保存：

```tsx
  const [matchOpen, setMatchOpen] = useState(false)
  const [matchLoading, setMatchLoading] = useState(false)
  const [matchResult, setMatchResult] = useState<OutlineMatchResult | null>(null)
  const [matchPath, setMatchPath] = useState<number[] | null>(null)
  const saveTimer = useRef<number | null>(null)
  const matchTimer = useRef<number | null>(null)

  // 保存防抖：拖拽/改名/改类型/挂附件后都会调它
  const scheduleSave = (next: OutlineChapter[]) => {
    if (!id) return
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      outlineApi.saveStructure(id, next).catch((err: any) => {
        antMessage.error(err?.response?.data?.detail || '章节结构保存失败')
      })
    }, 800)
  }

  const applyTree = (next: OutlineChapter[]) => {
    setChapters(next)
    scheduleSave(next)
  }

  // 重新匹配（防抖 500ms）
  const requestMatch = (path: number[], title: string, type: ChapterType) => {
    if (!id || (type !== 'fixed_form' && type !== 'table')) return
    if (matchTimer.current) window.clearTimeout(matchTimer.current)
    matchTimer.current = window.setTimeout(async () => {
      try {
        const res = await outlineApi.match(id, title, type)
        setChapters((prev) => {
          const next = updateNodeOutside(prev, path, (n) => ({ ...n, match: res }))
          scheduleSave(next)
          return next
        })
      } catch {
        /* 匹配失败不打断编辑；徽标沿用旧值 */
      }
    }, 500)
  }
```

`updateNodeOutside` 是从 `OutlineTree.tsx` 里 `export` 出来的 `updateNode`（把该函数加 `export`，或复制一份到 `outline.ts` 里作为共享工具——**选后者**，避免页面 import 组件内部实现）：

在 `frontend/src/api/outline.ts` 末尾加入：

```ts
/** 按索引路径不可变地更新节点（与 OutlineTree 内的同名 helper 语义一致） */
export function updateNodeByPath(
  chapters: OutlineChapter[],
  path: number[],
  updater: (n: OutlineChapter) => OutlineChapter,
): OutlineChapter[] {
  if (path.length === 0) return chapters
  const [head, ...rest] = path
  return chapters.map((ch, idx) => {
    if (idx !== head) return ch
    if (rest.length === 0) return updater(ch)
    return { ...ch, children: updateNodeByPath(ch.children ?? [], rest, updater) }
  })
}
```

并把上面 `requestMatch` 里的 `updateNodeOutside` 换成 `updateNodeByPath`。

抽屉与树接线：

```tsx
      <MatchDrawer
        open={matchOpen}
        loading={matchLoading}
        title={matchPath ? getNodeOutside(chapters, matchPath)?.title ?? '' : ''}
        result={matchResult}
        onPick={(c) => {
          if (!matchPath) return
          const next = updateNodeByPath(chapters, matchPath, (n) => ({
            ...n,
            match: {
              ...(n.match as OutlineMatchResult),
              status: 'matched',
              best: c,
              candidates: [],
              picked: 'manual',
            },
          }))
          applyTree(next)
          setMatchResult(getNodeByPath(next, matchPath)?.match ?? null)
          setMatchOpen(false)
        }}
        onClose={() => setMatchOpen(false)}
      />
```

`OutlineTree` 的用法改为：

```tsx
              <OutlineTree
                chapters={chapters}
                onChange={applyTree}
                onMatchRequest={requestMatch}
                onOpenMatch={(path) => {
                  setMatchPath(path)
                  setMatchResult(getNodeOutside(chapters, path)?.match ?? null)
                  setMatchOpen(true)
                }}
              />
```

`OutlineConfirm.tsx` 顶部 import 相应扩展（注意 `updateNodeByPath` / `getNodeByPath` 都从 `outline.ts` 来，不是从组件内部）：

```tsx
import {
  outlineApi,
  updateNodeByPath,
  getNodeByPath,
  type ChapterType,
  type OutlineChapter,
  type OutlineMatchResult,
} from '../../api/outline'
```

`getNodeByPath` 同样放到 `outline.ts`：

```ts
export function getNodeByPath(
  chapters: OutlineChapter[],
  path: number[],
): OutlineChapter | null {
  let arr: OutlineChapter[] | undefined = chapters
  let node: OutlineChapter | null = null
  for (const idx of path) {
    if (!arr) return null
    node = arr[idx]
    arr = node?.children
  }
  return node
}
```

并在 `handleConfirm` 里把整棵树带上去：

```tsx
        const res = await outlineApi.confirm(id, chapters)
```

- [ ] **Step 4: Type-check, build, manual smoke**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 无 TS 错误，构建成功

手工冒烟（用 `结果/招标文件-红云红河…签章.pdf` 建立的项目）：

1. 把某章类型改成「固定格式」→ 约半秒后徽标由 ⚪ 变绿「已匹配」，点开抽屉能看到招标原文片段与页码
2. 把标题改成招标文件里真实存在的另一个小节名 → 徽标与预览跟着变
3. 把标题改成一个不存在的名字 → 徽标变红「未匹配」，抽屉里显示明确说明
4. 构造一个同名小节出现两次的场景（或改标题命中招标里的高频词）→ 徽标变黄「多候选」，点候选能切换，关掉抽屉再打开仍是新选的那条
5. 把类型改回「AI 生成」→ 徽标变 ⚪，不再发起匹配请求（Network 面板确认）
6. 刷新页面 → 类型与匹配结果都还在

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/outline.ts frontend/src/components/OutlineEditor/OutlineTree.tsx frontend/src/components/OutlineEditor/MatchDrawer.tsx frontend/src/pages/project/OutlineConfirm.tsx
git commit -m "feat: 章节类型下拉 + 匹配徽标与原文预览抽屉"
```

---

### Task 15: 前端 — 附件挂载

**Files:**
- Modify: `frontend/src/components/OutlineEditor/MatchDrawer.tsx`（附件区）
- Test: 手工冒烟（见 Step 3）

**Interfaces:**
- Consumes: Task 13 的 `outlineApi.uploadAttachment` / `OutlineAttachment`、Task 14 的 `MatchDrawer`
- Produces:
  - `MatchDrawer` props 新增 `{ projectId: string, attachments: OutlineAttachment[], onAttachmentsChange: (next: OutlineAttachment[]) => void }`

- [ ] **Step 1: Add the attachment editor**

在 `MatchDrawer.tsx` 里加附件区（放在抽屉内容最后）：

```tsx
import { Upload, Button, List, Modal, Select } from 'antd'
import { UploadOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import client from '../../api/client'
import type { OutlineAttachment } from '../../api/outline'
```

props 扩展：

```tsx
  attachments: OutlineAttachment[]
  onAttachmentsChange: (next: OutlineAttachment[]) => void
```

抽屉里渲染：

```tsx
          <div>
            <Space style={{ marginBottom: 8 }}>
              <Typography.Text strong>附件清单</Typography.Text>
              <Upload
                showUploadList={false}
                beforeUpload={(file) => {
                  onUpload(file)
                  return false // 自己发请求，不让 antd 代传
                }}
              >
                <Button size="small" icon={<UploadOutlined />} loading={uploading}>
                  上传本项目文件
                </Button>
              </Upload>
              <Button size="small" icon={<PlusOutlined />} onClick={() => setPickerOpen(true)}>
                从资源库选
              </Button>
            </Space>
            <List
              size="small"
              locale={{ emptyText: '尚未挂载任何附件' }}
              dataSource={attachments}
              renderItem={(a, i) => (
                <List.Item
                  actions={[
                    <Button
                      key="del"
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => onAttachmentsChange(attachments.filter((_, k) => k !== i))}
                    />,
                  ]}
                >
                  <Space>
                    <Tag>{KIND_LABEL[a.kind] ?? a.kind}</Tag>
                    <span>{a.label}</span>
                  </Space>
                </List.Item>
              )}
            />
          </div>
```

`KIND_LABEL` 常量：

```tsx
const KIND_LABEL: Record<string, string> = {
  qualification: '资质',
  personnel_cert: '人员证书',
  contract: '合同',
  upload: '本项目上传',
}
```

上传处理（在 `MatchDrawer` 内）：

```tsx
  const [uploading, setUploading] = useState(false)

  const onUpload = async (file: File) => {
    setUploading(true)
    try {
      const att = await outlineApi.uploadAttachment(projectId, file, file.name)
      onAttachmentsChange([...attachments, att])
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '附件上传失败')
    } finally {
      setUploading(false)
    }
  }
```

> `MatchDrawer` 因此需要新增 `projectId: string` prop。

资源库挑选弹窗：复用已有的资源库列表接口。三个接口的响应形状已实测确认（见 `app/schemas/qualification.py` / `personnel.py` / `contract.py`），字段如下，**不要写 `??` 兜底链**：

| kind | 端点 | 取值 |
|---|---|---|
| `qualification` | `GET /qualifications` → `[{id, name, attachment_path}]` | `id`、`name`、`attachment_path` |
| `personnel_cert` | `GET /personnel` → `[{id, name, certificates: [{id, cert_name, attachment_path}]}]` | 展开每人的每个证书：`cert.id`、`${person.name} · ${cert.cert_name}`、`cert.attachment_path` |
| `contract` | `GET /contracts` → `[{id, project_name, image_paths_json}]` | `id`、`project_name`、`JSON.parse(image_paths_json)[0]` |

```tsx
  type PickerOption = { label: string; value: string; path: string }

  const [pickerKind, setPickerKind] = useState<OutlineAttachment['kind']>('qualification')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [options, setOptions] = useState<PickerOption[]>([])
  const [picked, setPicked] = useState<string[]>([])

  const buildOptions = (kind: OutlineAttachment['kind'], rows: any[]): PickerOption[] => {
    if (kind === 'qualification') {
      return rows.map((r) => ({ value: r.id, label: r.name, path: r.attachment_path }))
    }
    if (kind === 'personnel_cert') {
      return rows.flatMap((p) =>
        (p.certificates ?? []).map((c: any) => ({
          value: c.id,
          label: `${p.name} · ${c.cert_name}`,
          path: c.attachment_path,
        })),
      )
    }
    return rows.map((r) => {
      let first = ''
      try {
        const arr = JSON.parse(r.image_paths_json || '[]')
        first = Array.isArray(arr) && arr.length > 0 ? String(arr[0]) : ''
      } catch {
        first = ''
      }
      return { value: r.id, label: r.project_name, path: first }
    })
  }

  const KIND_ENDPOINT: Record<string, string> = {
    qualification: '/qualifications',
    personnel_cert: '/personnel',
    contract: '/contracts',
  }

  const openPicker = async (kind: OutlineAttachment['kind']) => {
    setPickerKind(kind)
    setPicked([])
    try {
      const res = await client.get(KIND_ENDPOINT[kind])
      setOptions(buildOptions(kind, Array.isArray(res.data) ? res.data : []))
      setPickerOpen(true)
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '资源库加载失败')
    }
  }

  const confirmPick = () => {
    const chosen = options.filter((o) => picked.includes(o.value) && o.path)
    const skipped = options.filter((o) => picked.includes(o.value) && !o.path)
    if (skipped.length > 0) {
      message.warning(`${skipped.length} 项没有扫描件/图片，已跳过`)
    }
    onAttachmentsChange([
      ...attachments,
      ...chosen.map((o) => ({
        kind: pickerKind,
        id: o.value,
        label: o.label,
        path: o.path,
      })),
    ])
    setPickerOpen(false)
  }
```

弹窗本体：

```tsx
      <Modal
        title="从资源库选附件"
        open={pickerOpen}
        onOk={confirmPick}
        onCancel={() => setPickerOpen(false)}
        okText={`添加 ${picked.length} 项`}
        okButtonProps={{ disabled: picked.length === 0 }}
      >
        <Select
          mode="multiple"
          style={{ width: '100%' }}
          placeholder="选择要挂到本章的资质 / 证书 / 合同扫描件"
          value={picked}
          onChange={setPicked}
          options={options.map((o) => ({
            value: o.value,
            label: o.path ? o.label : `${o.label}（无扫描件）`,
            disabled: !o.path,
          }))}
        />
      </Modal>
```

抽屉里三个入口按钮各带 kind：

```tsx
              <Button size="small" icon={<PlusOutlined />} onClick={() => openPicker('qualification')}>
                选资质
              </Button>
              <Button size="small" icon={<PlusOutlined />} onClick={() => openPicker('personnel_cert')}>
                选人员证书
              </Button>
              <Button size="small" icon={<PlusOutlined />} onClick={() => openPicker('contract')}>
                选合同
              </Button>
```

- [ ] **Step 2: 确认时不拦截（有意为之）**

**不要在 `handleConfirm` 里加「未匹配章节」的拦停弹框。** 用户在结构页已经能看到每章的红叉/黄叹号徽标，确认时再弹一次是重复打扰（2026-09-27 用户明确选择静默放过）。

`handleConfirm` 保持 Task 14 Step 3 的样子即可——只把整棵树随 `confirm` 一起提交：

```tsx
        const res = await outlineApi.confirm(id, chapters)
```

代价是：用户如果没看徽标就点确认，那些章节会安静地由 AI 撰写。这是可接受的产品取舍，**但因此 Task 15 的冒烟里必须确认徽标本身足够显眼**（见下）。

- [ ] **Step 3: Type-check, build, manual smoke**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: 无 TS 错误，构建成功

手工冒烟：

1. 把某章类型设为「附件」→ 抽屉里能「上传本项目文件」，选中后清单出现该条并显示「本项目上传」标签
2. 「从资源库选」能列出候选、多选确定后清单新增对应条
3. 删除某条附件 → 清单移除，刷新后仍是移除状态
4. 同一张图片通过两个不同路径挂到**两个章节** → 保存后第二个章节的该条被静默剔除（后端 `_prune_attachments` 按内容 md5 去重）
5. 制造一个 `missing` 的固定格式章节 → 左树该行的红色「未匹配」徽标**在整屏浏览时一眼可见**（这是唯一的告知渠道，不能太弱）
6. 点「确认并继续」→ **直接进入信息搜集，不弹任何拦截框**
7. 生成一次，确认附件章节导出后真的有图片（不是「（待补充…）」占位文本）

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/OutlineEditor/MatchDrawer.tsx frontend/src/pages/project/OutlineConfirm.tsx
git commit -m "feat: 附件挂载（资源库 + 本项目上传）"
```

---

## 完成后的收尾（不占独立任务）

- [ ] 全量后端测试：`cd backend && venv/Scripts/python.exe -m pytest tests/ -q`，确认无回归
- [ ] E2E：`docker exec -w /app -e PYTHONPATH=/app hongxi-backend python scripts/e2e_smoke.py` 全绿
- [ ] **真实标书复测**：用 `结果/招标文件-红云红河…签章.pdf` 走完整链路，**逐字核对固定格式章节是否等于招标原文**、表格列定义是否与招标一致、附件图片是否都出
- [ ] 核对 `format_verification_json` 里的 warnings 是否如实反映了本次的降级情况
- [ ] 部署：`git push server master` → 服务器 `cd /hongxi/hongxi-bid && git pull && docker compose up -d --build`
- [ ] 交付后确认：**已确认/已生成的老项目不会自动获得本次精度改善**（它们没有 `match` 字段），需在结构页重新过一遍

