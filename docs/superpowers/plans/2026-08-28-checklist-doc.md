# 标书检查清单文档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 标书导出时同步生成一份独立、可打印的「投标文件检查清单」docx + pdf（说明/页码/状态/确认☐/备注 5 列），页码由导出主标书 PDF 逐页关键词扫描自动回填，并点亮既有休眠缺陷「容器内 export_to_pdf 恒 None → pdf_url 恒空」。

**Architecture:** 在 `POST /bid/{project_id}/export` 主标书 docx 渲染成功后（`render_bid_to_docx` → `export_to_pdf` 之后、响应返回前）追加一个 try/except 包裹的 checklist 段：`checklist_engine`（新建纯函数模块）负责 9 行预置模板合并自定义行 → 落库校验源推导状态 → pdfplumber 页扫回填页码（`locate_page` 与 `fill_pages` 分离，单元测试只测纯函数）→ python-docx 渲染清单。任何失败只清空 checklist url 并 `logger.warning`，绝不影响主标书导出。同时 `backend/Dockerfile` 安装 `libreoffice-writer` + `fonts-noto-cjk`（修复 `export_to_pdf` 在容器内恒 None 的休眠缺陷，也是页扫的 PDF 来源）。

**Tech Stack:** python-docx（既有 1.1.0）、pdfplumber（既有 0.11.0）、LibreOffice headless（宿主有、容器将补装）、FastAPI/Pydantic v2（既有）、React + TS + Ant Design（既有）。**不引入任何新 Python/JS 依赖。**

**Spec:** `docs/superpowers/specs/2026-08-28-checklist-doc-design.md`（已批准，含 §4.0 LibreOffice 前置、§4.2 契约、§4.4 页扫、§5 三级降级、§6 测试项）— 权威依据，冲突以 spec 为准。

## Global Constraints

- 后端任何新文件必须带**中文 docstring + 版权头** `Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.`（§13；测试文件同样带）。前端文件**不**写版权头。
- export 的 checklist 段**整体 try/except**，任何失败不抛给前端、不阻塞主标书导出（spec §13 铁律、§5 降级表）。
- 不引入新 Python 依赖（python-docx、pdfplumber、LibreOffice 均既有，spec §8）。
- pytest `asyncio_mode = STRICT`；checklist 全部为同步纯函数，测试用普通 `def test_*` 类方法，无需 `@pytest.mark.asyncio`。
- 后端全量 pytest 基线：202 passed / 1 known fail（Windows 编码 `test_format_template_to_prompt_text`）——本特性验收须保持「新测试全绿 + 无新增失败」。
- 前端 `npm run build`（tsc -b && vite build）必须绿，TS 严格模式。
- 页码语义（spec §4.4）：主标书 PDF 中 1-indexed 首次命中页；定位失败该行页码留空 + 状态 ⚠。
- 提交信息：中文、imperative 主题；每条 commit 末尾 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 状态列文案固定两个枚举：`✔ 已定位` / `⚠ 未找到，需核对`（spec §3.3，前后端共用）。
- E2E 冒烟是运行时验证（容器内，部署时跑）；本计划 Task 2 的 Dockerfile 正确性**推迟到容器 E2E 验证**（与先前 R3 先例一致：无法本地构建镜像）。

---

### Task 1: checklist_engine 纯函数模块 + 单元测试

**Files:**
- Create: `backend/app/services/checklist_engine.py`
- Create: `backend/tests/test_checklist_engine.py`

**Interfaces:**
- Produces（Task 2 消费，签名不可变）:
  - `CHECKLIST_ITEM_TEMPLATES: list[dict]` — 9 行预置，每行 `{"key", "label", "keywords": list[str]}`
  - `STATUS_OK = "✔ 已定位"`、`STATUS_WARN = "⚠ 未找到，需核对"`
  - `merge_items(checklist_items: list[dict] | None, removed: list[str] | None) -> list[dict]`
  - `derive_statuses(rows: list[dict], *, source_ctx: dict) -> list[dict]` — 落库校验源推导**临时状态**（PDF 命中由 fill_pages 覆盖为 OK）
  - `locate_page(page_texts: list[str], keywords: list[str]) -> int | None` — 1-indexed 首次命中
  - `fill_pages(pdf_path: str, rows: list[dict]) -> list[dict]` — pdfplumber 页扫回填 `row["page"]`；PDF 读取失败原样返回 rows（降级路径）
  - `build_checklist_docx(project_name: str, rows: list[dict], out_path: str, *, generated_at: str | None = None) -> str`
- Consumes: 无（独立模块，纯服务）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_checklist_engine.py`（带版权头 + 中文 docstring）：

```python
"""检查清单引擎单元测试."""
# Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.

from pathlib import Path

from docx import Document  # noqa: F401  (仅用于读回断言)

from app.services.checklist_engine import (
    CHECKLIST_ITEM_TEMPLATES,
    STATUS_OK,
    STATUS_WARN,
    build_checklist_docx,
    derive_statuses,
    fill_pages,
    locate_page,
    merge_items,
)


class TestTemplates:
    def test_nine_preset_rows_with_unique_keys(self):
        assert len(CHECKLIST_ITEM_TEMPLATES) == 9
        keys = [t["key"] for t in CHECKLIST_ITEM_TEMPLATES]
        assert len(set(keys)) == 9
        for t in CHECKLIST_ITEM_TEMPLATES:
            assert t["label"]
            assert isinstance(t["keywords"], list) and t["keywords"]

    def test_preset_keys_cover_required_submission_items(self):
        keys = {t["key"] for t in CHECKLIST_ITEM_TEMPLATES}
        assert {"quotation", "bid_letter", "legal_rep_cert", "authorization",
                "signature_seal", "commitment", "qualification",
                "performance", "personnel"} <= keys


class TestMergeItems:
    def test_default_returns_templates(self):
        assert merge_items(None, None) == CHECKLIST_ITEM_TEMPLATES

    def test_removed_drops_preset(self):
        rows = merge_items(None, ["quotation", "performance"])
        keys = [r["key"] for r in rows]
        assert "quotation" not in keys and "performance" not in keys
        assert len(rows) == 7

    def test_item_with_preset_key_overrides_label(self):
        rows = merge_items([{"key": "quotation", "label": "报价总表"}], None)
        row = next(r for r in rows if r["key"] == "quotation")
        assert row["label"] == "报价总表"
        # 覆盖 label 不丢预置定位关键词
        assert "开标一览表" in row["keywords"]

    def test_custom_key_appends_with_label_as_keyword(self):
        rows = merge_items([{"key": "custom-1", "label": "项目实施方案"}], None)
        custom = [r for r in rows if r["key"] == "custom-1"]
        assert len(custom) == 1
        assert custom[0]["keywords"] == ["项目实施方案"]

    def test_idempotent_preserves_preset_then_custom_order(self):
        merged = merge_items(
            [{"key": "quotation", "label": "改"}, {"key": "custom-9", "label": "追加行"}],
            ["bid_letter"],
        )
        assert [r["key"] for r in merged] == [r["key"] for r in
            merge_items([{"key": "quotation", "label": "改"},
                         {"key": "custom-9", "label": "追加行"}], ["bid_letter"])]


class TestLocatePage:
    def test_first_hit_across_pages(self):
        assert locate_page(["第1页", "报价表在此", "无"], ["报价表"]) == 2

    def test_miss_returns_none(self):
        assert locate_page(["全部无关", "文字"], ["报价表"]) is None

    def test_any_keyword_hits(self):
        assert locate_page(["无", "盖章页"], ["签字", "盖章"]) == 2

    def test_first_page_wins(self):
        assert locate_page(["盖章在此", "盖章又见"], ["盖章"]) == 1

    def test_empty_keywords_returns_none(self):
        assert locate_page(["任意文本"], []) is None


class TestDeriveStatuses:
    def test_quotation_ok_when_opening_table_built(self):
        rows = derive_statuses(
            [{"key": "quotation", "label": "报价", "keywords": ["报价"]}],
            source_ctx={"chapter_titles": [], "bid_opening_ok": True,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert rows[0]["status"] == STATUS_OK

    def test_chapter_title_matches_mark_ok(self):
        rows = derive_statuses(
            [{"key": "bid_letter", "label": "投标函", "keywords": ["投标函"]}],
            source_ctx={"chapter_titles": ["投标函"], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert rows[0]["status"] == STATUS_OK

    def test_empty_source_marks_warn(self):
        rows = derive_statuses(
            [{"key": k, "label": v, "keywords": []} for k, v in [
                ("quotation", "报价"), ("signature_seal", "签字盖章"),
                ("performance", "业绩")]],
            source_ctx={"chapter_titles": [], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert all(r["status"] == STATUS_WARN for r in rows)

    def test_count_based_sources(self):
        rows = derive_statuses(
            [{"key": k, "label": v, "keywords": []} for k, v in [
                ("qualification", "资质"), ("performance", "业绩"),
                ("personnel", "人员")]],
            source_ctx={"chapter_titles": [], "bid_opening_ok": False,
                        "qual_count": 1, "contract_count": 2, "personnel_count": 2},
        )
        assert all(r["status"] == STATUS_OK for r in rows)

    def test_custom_row_status_from_db_fallback_only(self):
        rows = derive_statuses(
            [{"key": "custom-1", "label": "保密承诺附件", "keywords": ["保密承诺附件"]}],
            source_ctx={"chapter_titles": ["保密承诺附件"], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert rows[0]["status"] == STATUS_OK
        rows2 = derive_statuses(
            [{"key": "custom-1", "label": "保密承诺附件", "keywords": ["保密承诺附件"]}],
            source_ctx={"chapter_titles": [], "bid_opening_ok": False,
                        "qual_count": 0, "contract_count": 0, "personnel_count": 0},
        )
        assert rows2[0]["status"] == STATUS_WARN


class TestFillPages:
    def test_missing_pdf_returns_rows_unchanged(self):
        rows = [{"key": "quotation", "label": "报价", "keywords": ["报价"],
                 "status": STATUS_WARN}]
        out = fill_pages("C:/no_such_dir/缺.pdf", rows)
        assert out is rows  # 降级路径：原对象返回，调用方维持校验源状态


class TestBuildChecklistDocx:
    def test_docx_has_headers_and_rows(self, tmp_path):
        rows = [
            {"key": "quotation", "label": "报价（开标一览表 / 报价表）",
             "page": "3", "status": STATUS_OK},
            {"key": "signature_seal", "label": "签字盖章页",
             "page": "", "status": STATUS_WARN},
        ]
        out = tmp_path / "清单.docx"
        build_checklist_docx("测试项目", rows, str(out), generated_at="2026-08-28 10:00")
        assert out.exists()
        doc = Document(str(out))
        assert doc.tables
        table = doc.tables[0]
        headers = [c.text for c in table.rows[0].cells]
        assert all(h in headers for h in ("说明", "页码", "状态", "确认", "备注"))
        assert len(table.rows) == len(rows) + 1
        # 确认列均填空框 ☐，页码回填正确
        for i, row in enumerate(rows, start=1):
            assert table.rows[i].cells[3].text == "☐"
        assert table.rows[1].cells[1].text == "3"
        # 标题与项目名在正文区
        body_text = "\n".join(p.text for p in doc.paragraphs)
        assert "投标文件检查清单" in body_text
        assert "测试项目" in body_text
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_checklist_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.checklist_engine'`

- [ ] **Step 3: 实现 `checklist_engine.py`**

创建 `backend/app/services/checklist_engine.py`：

```python
"""投标文件检查清单生成（标书导出时的同步产物，纯函数模块）.

职责：预置行模板 → 自定义行合并 → 落库校验源推导状态（临时）→
PDF 逐页关键词扫描回填页码 → python-docx 渲染独立核对清单 docx。

与渲染主标书的 render_engine 完全解耦：本模块不含任何 AI 调用、
不含 DB 访问（校验源数据由调用方经 source_ctx 传入），失败由调用方
try/except 降级（spec §5），任何路径不阻塞主标书导出。
"""
# Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)

STATUS_OK = "✔ 已定位"
STATUS_WARN = "⚠ 未找到，需核对"

# spec §3.2：9 行预置标准核对项
# key：唯一标识（自定义行替换/删除用）；label：说明列文案；
# keywords：主标书 PDF 页扫关键词，任一命中即定位（spec §4.4）。
CHECKLIST_ITEM_TEMPLATES: list[dict] = [
    {"key": "quotation", "label": "报价（开标一览表 / 报价表）",
     "keywords": ["开标一览表", "报价表", "报价表列"]},
    {"key": "bid_letter", "label": "投标函（致招标人）",
     "keywords": ["投标函"]},
    {"key": "legal_rep_cert", "label": "法定代表人身份证明",
     "keywords": ["法定代表人身份证明", "法定代表人"]},
    {"key": "authorization", "label": "授权委托书（授权代理人签署）",
     "keywords": ["授权委托书", "委托代理人"]},
    {"key": "signature_seal", "label": "签字盖章页（法定代表人/委托人签字、公章）",
     "keywords": ["签字", "盖章", "签章"]},
    {"key": "commitment", "label": "承诺书（廉洁承诺 / 不串标等）",
     "keywords": ["承诺书"]},
    {"key": "qualification", "label": "资格证明（资质证书）",
     "keywords": ["资质证书", "证书"]},
    {"key": "performance", "label": "业绩证明（类似项目合同 / 中标通知书）",
     "keywords": ["类似项目", "业绩", "中标通知书"]},
    {"key": "personnel", "label": "人员配置及证书",
     "keywords": ["项目服务人员", "人员", "执业证"]},
]


def merge_items(checklist_items: list[dict] | None,
                removed: list[str] | None) -> list[dict]:
    """合并用户自定义行（spec §4.2）。

    removed 中出现的预置 key 不渲染；checklist_items 中带预置 key 的条目
    覆盖该行 label（保留原 keywords）；custom-* 条目追加为自定义行
    （label 本身参与关键词扫描）。空 items 回退预置模板。
    """
    items = [dict(t) for t in CHECKLIST_ITEM_TEMPLATES]
    removed_set = set(removed or [])
    items = [it for it in items if it["key"] not in removed_set]
    by_key = {it["key"]: it for it in items}
    appended: list[dict] = []
    for it in checklist_items or []:
        key = (it.get("key") or "").strip()
        label = (it.get("label") or "").strip()
        if not key or not label:
            continue
        if key in by_key:
            by_key[key]["label"] = label
        else:
            appended.append({"key": key, "label": label,
                             "keywords": [label], "custom": True})
    return items + appended


def _source_ok(row: dict, ctx: dict) -> bool:
    """落库校验源（spec §3.2 第四列）：由章节标题与素材计数推导。
    仅用于 PDF 不可用或未命中时的折半状态，PDF 命中永远覆盖为 OK。"""
    chapter_titles = ctx.get("chapter_titles") or []
    joined = "\n".join(chapter_titles)

    def _has(*kws: str) -> bool:
        return any(k in joined for k in kws)

    key = row["key"]
    if key == "quotation":
        return bool(ctx.get("bid_opening_ok")) or _has("报价")
    if key == "bid_letter":
        return _has("投标函")
    if key == "legal_rep_cert":
        return _has("法定代表人身份证明", "法定代表人")
    if key == "authorization":
        return _has("授权委托")
    if key == "signature_seal":
        # 签名页是渲染引擎固定输出块（投标人：（盖章）行），以商务部分存在为代理
        return _has("商务")
    if key == "commitment":
        return _has("承诺")
    if key == "qualification":
        return (ctx.get("qual_count") or 0) > 0 or _has("资质", "证书")
    if key == "performance":
        return (ctx.get("contract_count") or 0) > 0 or _has("类似项目", "业绩")
    if key == "personnel":
        return (ctx.get("personnel_count") or 0) > 0 or _has("人员")
    # 自定义行：仅 label 关键词参与 PDF 定位；落库校验源无对应数据 → 默认 WARN
    return False


def derive_statuses(rows: list[dict], *, source_ctx: dict) -> list[dict]:
    """按落库校验源推导临时状态（spec §3.3 折半路径）。

    PDF 可用时由 fill_pages 的命中结果覆盖为 STATUS_OK；未命中保留本
    函数结果（校验源通过 → OK，未通过 → WARN）。返回新 rows（不改入参）。
    """
    out = []
    for row in rows:
        r = dict(row)
        r["status"] = STATUS_OK if _source_ok(r, source_ctx) else STATUS_WARN
        out.append(r)
    return out


def locate_page(page_texts: list[str], keywords: list[str]) -> int | None:
    """在所有页文本中扫任一关键词，返回 1-indexed 首次命中页；无命中返回 None."""
    for i, text in enumerate(page_texts):
        if text and any(kw in text for kw in keywords):
            return i + 1
    return None


def fill_pages(pdf_path: str, rows: list[dict]) -> list[dict]:
    """pdfplumber 逐页抽取文本 → locate_page 回填页码（spec §4.4）。

    PDF 读取/扫描任何异常 → logger.warning + 原样返回 rows（调用方维持
    校验源状态，页码列留空，spec §5 降级）。成功时逐行回填 row["page"]
    （int 或空串），命中行状态覆盖为 STATUS_OK。
    """
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            page_texts = [(page.extract_text() or "") for page in pdf.pages]
    except Exception as exc:
        logger.warning("检查清单 PDF 页扫描失败，页码列留空: %s", exc)
        return rows

    for row in rows:
        page = locate_page(page_texts, row.get("keywords") or [])
        row["page"] = page if page is not None else ""
        if page is not None:
            row["status"] = STATUS_OK
    return rows


def _set_run_font(run, name: str = "宋体", size: float = 12, bold: bool = False):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size)
    run.font.bold = bold


def build_checklist_docx(project_name: str, rows: list[dict], out_path: str,
                         *, generated_at: str | None = None) -> str:
    """渲染 5 列核对清单 docx（spec §4.5）：说明/页码/状态/确认☐/备注。

    返回 out_path（绝对路径字符串）。失败抛异常由调用方降级。
    """
    header_order = ("说明", "页码", "状态", "确认", "备注")
    widths = (Cm(6.0), Cm(1.5), Cm(2.8), Cm(1.5), Cm(4.7))

    doc = Document()
    title = doc.add_heading("投标文件检查清单", level=0)
    for run in title.runs:
        _set_run_font(run, name="黑体", size=18, bold=True)
    if (title._element.rPr is None) or (title.runs and title.runs[0].font.name != "黑体"):
        # Heading 样式重建保护：确保 eastAsia 字体显式设置
        for run in title.runs:
            _set_run_font(run, name="黑体", size=18, bold=True)

    meta = doc.add_paragraph()
    ts = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    _set_run_font(meta.add_run(f"项目名称：{project_name}    生成时间：{ts}"), size=10.5)

    tip = doc.add_paragraph()
    _set_run_font(tip.add_run("提示：请对照标书逐项核对，在「确认」栏打勾，必要时在「备注」栏注明。"), size=10.5)

    table = doc.add_table(rows=1, cols=len(header_order))
    table.style = "Table Grid"
    for j, text in enumerate(header_order):
        cell = table.rows[0].cells[j]
        cell.text = text
        cell.width = widths[j]
        for p in cell.paragraphs:
            for run in p.runs:
                _set_run_font(run, bold=True)

    for row in rows:
        cells = table.add_row().cells
        for j, text in enumerate(header_order):
            cells[j].width = widths[j]
        # 行高 ≥1.5cm 给备注书写空间（spec §4.5）
        row_cells = cells
        row_cells[0].text = row.get("label") or ""
        row_cells[1].text = str(row.get("page") or "")
        row_cells[2].text = row.get("status") or ""
        row_cells[3].text = "☐"
        row_cells[4].text = ""
        for p in row_cells[0].paragraphs:
            for run in p.runs:
                _set_run_font(run, size=12)
        try:
            tr = row_cells[0]._tc.getparent()
            tr_pr = tr.get_or_add_trPr()
            from docx.oxml import OxmlElement
            h = OxmlElement("w:trHeight")
            h.set(qn("w:val"), "424")
            h.set(qn("w:hRule"), "atLeast")
            tr_pr.append(h)
        except Exception:
            pass

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    logger.info("检查清单已生成: %s（%d 行）", out, len(rows))
    return str(out)
```

> 注意 Step 3 代码中的 `WD_ROW_HEIGHT_RULE` import 未被使用（行高用 oxml 原生 `trHeight` 实现）——实现时删掉该 import，保持 lint 干净。标题 run 的重复 `_set_run_font` 是防御「Heading 样式暂未建 rPr」的兜底，可简化实现（保留其中一段）。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_checklist_engine.py -q`
Expected: PASS（18 tests：5×Templates≈2 + MergeItems 5 + LocatePage 5 + DeriveStatuses 5 + FillPages 1 + BuildDocx 1，实际计数以文件为准，至少 15）。

- [ ] **Step 5: 全量回归**

Run: `cd backend && python -m pytest tests -q`
Expected: 基线不变（原 202 passed / 1 known fail Windows 编码）+ 本文件全绿。

- [ ] **Step 6: 提交**

```bash
cd "D:/2026/投标软件"
git add backend/app/services/checklist_engine.py backend/tests/test_checklist_engine.py
git commit -m "feat: 检查清单引擎 — 预置行合并/状态推导/PDF页扫/清单docx渲染

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 导出请求/响应契约 + export_bid 接线 + Dockerfile LibreOffice 前置

**Files:**
- Modify: `backend/app/schemas/bid.py:24-41`（ExportRequest/ExportResponse 加字段）
- Modify: `backend/app/api/bid.py` export_bid（约 :1300-1420 区域：pdf 段之后插入 checklist 段 + 修改返回构造）
- Modify: `backend/Dockerfile`（apt 装 libreoffice-writer + fonts-noto-cjk，spec §4.0）
- Create: `backend/tests/test_export_schema_defaults.py`

**Interfaces:**
- Consumes: Task 1 的 `merge_items / derive_statuses / fill_pages / build_checklist_docx`。
- Produces: `ExportRequest.include_checklist: bool = True`、`ExportRequest.checklist_items: Optional[List[dict]] = None`、`ExportRequest.checklist_removed: Optional[List[str]] = None`；`ExportResponse.checklist_docx_url: str = ""`、`ExportResponse.checklist_pdf_url: str = ""`（Task 3 e2e 消费）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_export_schema_defaults.py`：

```python
"""导出请求/响应契约新增字段默认值测试."""
# Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.

from app.schemas.bid import ExportRequest, ExportResponse


class TestChecklistContractDefaults:
    def test_export_request_checklist_defaults(self):
        # 默认 include_checklist=True：旧前端调新后端默认多产出清单，无破坏（spec §8）
        req = ExportRequest(project_id="p1")
        assert req.include_checklist is True
        assert req.checklist_items is None
        assert req.checklist_removed is None

    def test_export_request_accepts_checklist_payload(self):
        req = ExportRequest(
            project_id="p1",
            include_checklist=False,
            checklist_items=[{"key": "custom-1", "label": "项目实施方案"}],
            checklist_removed=["performance"],
        )
        assert req.include_checklist is False
        assert req.checklist_items[0]["label"] == "项目实施方案"
        assert req.checklist_removed == ["performance"]

    def test_export_response_checklist_urls_default_empty(self):
        res = ExportResponse()
        assert res.checklist_docx_url == ""
        assert res.checklist_pdf_url == ""
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_export_schema_defaults.py -q`
Expected: FAIL — `pydantic` ValidationError（未知字段 `include_checklist`）。

- [ ] **Step 3: 改 schemas/bid.py**

在 `backend/app/schemas/bid.py` 的 `ExportRequest`（:24）与 `ExportResponse`（:36）追加（spec §4.2，既有字段一字不动）：

```python
class ExportRequest(BaseModel):
    project_id: str
    format: str = "docx"
    chapter_ids: Optional[List[str]] = None
    template_id: Optional[str] = None
    include_checklist: bool = True          # 新增：默认导出检查清单
    checklist_items: Optional[List[dict]] = None   # 新增：自定义行 [{key,label}...]
    checklist_removed: Optional[List[str]] = None  # 新增：被删除的预置行 key

class ExportResponse(BaseModel):
    docx_url: str = ""
    pdf_url: str = ""
    checklist_docx_url: str = ""            # 新增：清单失败/未启用时为空串
    checklist_pdf_url: str = ""             # 新增
```

- [ ] **Step 4: 改 export_bid 接线（bid.py）**

关键锚点：`pdf_path` 段在 :1296 附近（`if data.format in ("pdf","both"): pdf_path = export_to_pdf(docx_path)`）；`render_bid_to_docx` 返回 `docx_path`（:1350）；投标数据 vars `all_quals`（:1097）、`contracts`（:1220）、`personnel_cert_images`（:1189）、`bid_opening_content`（:1304/1337）；返回构造在 :1420 附近。

在 `render_bid_to_docx(...)` 与 pdf 段**之后**、`return ExportResponse(...)` **之前**插入（整段 try/except，spec §5 铁律）：

```python
    # ──────────────────── 同步生成检查清单（独立可打印核对文档） ────────────────────
    # 任何失败不阻塞主标书导出：仅清空 checklist url + logger.warning（spec §5）。
    checklist_docx_url = ""
    checklist_pdf_url = ""
    try:
        from app.services.checklist_engine import (
            merge_items, derive_statuses, fill_pages, build_checklist_docx,
        )
        rows = merge_items(data.checklist_items, data.checklist_removed)
        source_ctx = {
            "chapter_titles": [ch.get("title", "") for ch in chapters_payload],
            "bid_opening_ok": bool(bid_opening_content),
            "qual_count": len(all_quals),
            "contract_count": len(contracts),
            "personnel_count": len(personnel_cert_images),
        }
        rows = derive_statuses(rows, source_ctx=source_ctx)
        # 页码定位源：优先主标书 pdf；format=docx 时补渲一次仅用于定位（spec §4.3）
        pdf_for_locate = pdf_path
        if not pdf_for_locate:
            pdf_for_locate = export_to_pdf(docx_path)
        if pdf_for_locate:
            fill_pages(pdf_for_locate, rows)
        checklist_docx = Path(docx_path).with_name(Path(docx_path).stem + "-检查清单.docx")
        build_checklist_docx(project.name, rows, str(checklist_docx))
        checklist_docx_url = f"{DL_PREFIX}{checklist_docx.name}"
        checklist_pdf = export_to_pdf(str(checklist_docx))
        if checklist_pdf:
            checklist_pdf_url = f"{DL_PREFIX}{Path(checklist_pdf).name}"
    except Exception as exc:
        logger.warning("检查清单生成失败（不阻塞主标书导出）: %s", exc)
```

并把返回构造改为（`DL_PREFIX` 沿用函数内既有 download 前缀常量，例如既有 `docx_url=f"/api/v1/bid/download/{docx_filename}"` 的写法；若现有代码未抽常量则就地用同样的字符串字面量）：

```python
    return ExportResponse(
        docx_url=f"{DL_PREFIX}{Path(docx_path).name}",
        pdf_url=f"{DL_PREFIX}{Path(pdf_path).name}" if pdf_path else "",
        checklist_docx_url=checklist_docx_url,
        checklist_pdf_url=checklist_pdf_url,
    )
```

实现时注意：`project` 变量在 export_bid 内是 `BidProject` 记录（`project.name`），若实际变量名是 `proj`/`project_data` 等则对齐；`chapters_payload` 是导出用的章节 dict 列表（含 `title`），若变量名不同（如 `chapters_data`）则对齐。`fill_pages` 返回值在成功时与入参同一对象（就地回填），不需要重新赋值。

- [ ] **Step 5: 改 Dockerfile（spec §4.0 前置）**

在 `backend/Dockerfile` 中（既有 apt 段之后、`COPY . .` 之前）新加一段：

```dockerfile
# LibreOffice: export_to_pdf 容器内 PDF 转换 + 检查清单页码定位（spec §4.0）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
```

> 该步无法在 Windows 开发机本地验证（无 docker build）；正确性由 Task 4 的容器 E2E（`pdf_url` 首次真实非空 + 清单 pdf 非空）在部署时验证 —— 与先前特性 R3 先例一致。

- [ ] **Step 6: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_export_schema_defaults.py tests/test_checklist_engine.py -q`
Expected: PASS。

- [ ] **Step 7: 全量回归**

Run: `cd backend && python -m pytest tests -q`
Expected: 基线不变 + 新增全绿。

- [ ] **Step 8: 提交**

```bash
cd "D:/2026/投标软件"
git add backend/app/schemas/bid.py backend/app/api/bid.py backend/Dockerfile backend/tests/test_export_schema_defaults.py
git commit -m "feat: 导出端点同步生成检查清单 + 容器补装 LibreOffice 点亮 pdf 导出

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 前端导出面板 —— 勾选/自定义行编辑 + 清单下载

**Files:**
- Modify: `frontend/src/pages/project/ProjectWorkflow.tsx`（导出面板 :735-770 附近 + 面板 UI 区）

**Interfaces:**
- Consumes: `ExportResponse.checklist_docx_url / checklist_pdf_url`；向 `POST /bid/export` 发送 `include_checklist / checklist_items / checklist_removed`。
- Produces: 无（终端 UI）。

- [ ] **Step 1: 状态与请求扩展**

在 `ProjectWorkflow.tsx` 组件内（`handleExport` 之前）加状态：

```tsx
const [includeChecklist, setIncludeChecklist] = useState<boolean>(true);
const [removedKeys, setRemovedKeys] = useState<string[]>([]);
const [customRows, setCustomRows] = useState<Array<{ key: string; label: string }>>([]);
const [labelOverrides, setLabelOverrides] = useState<Record<string, string>>({});
```

模块级常量（与服务端 `CHECKLIST_ITEM_TEMPLATES` 对应的预置说明，仅供编辑器展示）：

```tsx
const PRESET_CHECKLIST_ITEMS: Array<{ key: string; label: string }> = [
  { key: 'quotation', label: '报价（开标一览表 / 报价表）' },
  { key: 'bid_letter', label: '投标函（致招标人）' },
  { key: 'legal_rep_cert', label: '法定代表人身份证明' },
  { key: 'authorization', label: '授权委托书（授权代理人签署）' },
  { key: 'signature_seal', label: '签字盖章页（法定代表人/委托人签字、公章）' },
  { key: 'commitment', label: '承诺书（廉洁承诺 / 不串标等）' },
  { key: 'qualification', label: '资格证明（资质证书）' },
  { key: 'performance', label: '业绩证明（类似项目合同 / 中标通知书）' },
  { key: 'personnel', label: '人员配置及证书' },
];
```

改 `handleExport`（现 :739-768，`client.post('/bid/export', {...})`）——请求体加 checklist 字段，响应解构加 checklist url，成功后追加触发下载：

```tsx
const { data } = await client.post('/bid/export', {
  project_id: id,
  format: 'both',
  template_id: selectedTemplateId,
  include_checklist: includeChecklist,
  checklist_items: customRows.map(r => ({ key: r.key, label: r.label }))
    .concat(Object.entries(labelOverrides).map(([key, label]) => ({ key, label }))),
  checklist_removed: removedKeys,
});
const { docx_url, pdf_url, checklist_docx_url, checklist_pdf_url } = data ?? {};
// …（既有 docx/pdf 处理不变）…
if (checklist_docx_url) {
  window.open(checklist_docx_url, '_blank');
}
```

> 说明：`labelOverrides` 与 `customRows` 合并进同一个 `checklist_items`——预置 key 的 override 条目（`{key 命中预置, label 新文案}`）与 `custom-*` 新增行（`{key: 'custom-<n>', label}`）都随请求发送；服务端 `merge_items` 按 §4.2 规则分发。下载用既有 triggerDownload 模式亦可，若 handleExport 内已有同款触发逻辑则统一走它。

- [ ] **Step 2: 导出面板 UI（勾选 + 行编辑）**

在导出面板（「导出」按钮所在 modal/card，含格式选择与 `selectedTemplateId` 的区域内）`template` 选择之下追加：

```tsx
<Space direction="vertical" style={{ width: '100%' }} size={8}>
  <Checkbox
    checked={includeChecklist}
    onChange={(e) => setIncludeChecklist(e.target.checked)}
  >
    导出检查清单（默认开启，打印核对用）
  </Checkbox>
  {includeChecklist && (
    <div>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        勾选要保留的核对项，可改写说明；「+ 自定义行」追加清单行。
      </Typography.Text>
      {PRESET_CHECKLIST_ITEMS.filter((it) => !removedKeys.includes(it.key)).map((it) => (
        <div key={it.key} style={{ display: 'flex', gap: 8, marginTop: 4, alignItems: 'center' }}>
          <Input
            size="small"
            defaultValue={it.label}
            style={{ flex: 1 }}
            onChange={(e) =>
              setLabelOverrides((prev) =>
                e.target.value === PRESET_CHECKLIST_ITEMS.find(x => x.key === it.key)!.label
                  ? (() => { const n = { ...prev }; delete n[it.key]; return n; })()
                  : { ...prev, [it.key]: e.target.value }
              )
            }
          />
          <Button size="small" danger onClick={() => setRemovedKeys((p) => [...p, it.key])}>
            删除
          </Button>
        </div>
      ))}
      {customRows.map((row, idx) => (
        <div key={row.key} style={{ display: 'flex', gap: 8, marginTop: 4, alignItems: 'center' }}>
          <Input size="small" value={row.label} style={{ flex: 1 }} disabled />
          <Button size="small" danger onClick={() => setCustomRows((p) => p.filter((_, i) => i !== idx))}>
            删除
          </Button>
        </div>
      ))}
      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
        <Input
          size="small"
          placeholder="自定义项说明，如：项目实施方案"
          id="checklist-custom-label"
          style={{ flex: 1 }}
          onPressEnter={(e) => {
            const v = (e.target as HTMLInputElement).value.trim();
            if (!v) return;
            setCustomRows((p) => [
              ...p,
              { key: `custom-${Date.now()}`, label: v },
            ]);
            (e.target as HTMLInputElement).value = '';
          }}
        />
        <Button size="small" onClick={() => {
          const input = document.getElementById('checklist-custom-label') as HTMLInputElement;
          const v = input?.value.trim();
          if (!v) return;
          setCustomRows((p) => [...p, { key: `custom-${Date.now()}`, label: v }]);
          if (input) input.value = '';
        }}>
          + 自定义行
        </Button>
      </div>
    </div>
  )}
</Space>
```

> 若 `Space`/`Checkbox`/`Typography` 未在文件 import，按文件既有 Ant Design 引入风格补 `import { Checkbox, Space, Typography } from 'antd'`（`Button`/`Input` 已在）。

- [ ] **Step 3: 前端构建验证**

Run: `cd frontend && npm run build`
Expected: 构建成功（tsc 严格模式通过、vite 产物生成）。若有 TS 报错（如 `res.data` 类型缺字段）→ 扩展 `frontend/src/api/` 导出类型或就地 `??` 兜底；本项目走就地类型化（spec §7 文件清单注明）。

- [ ] **Step 4: 提交**

```bash
cd "D:/2026/投标软件"
git add frontend/src/pages/project/ProjectWorkflow.tsx
git commit -m "feat: 导出面板检查清单勾选/行编辑 + 结果区清单下载

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: E2E 冒烟扩展（pdf + 清单断言）+ 全量回归

**Files:**
- Modify: `backend/scripts/e2e_smoke.py`（export 块 :548-575）

**Interfaces:**
- Consumes: Task 2 的 `POST /bid/export` 新响应字段（`pdf_url` 真实可达、`checklist_docx_url`、`checklist_pdf_url`）。
- Produces: 容器 E2E 全绿（`format:"both" + include_checklist` 断言是部署强制项，spec §8）。

- [ ] **Step 1: 改 export 断言块**

在 `e2e_smoke.py` 的 export 块（现 :548-575 附近）——保留既有 docx 下载与 python-docx 校验（Word TOC 域、章节目录、markdown 残留、体积 ≥10KB）——把请求与断言扩展为：

```python
            # 导出：both（pdf 首次真实验证容器 LibreOffice）+ 检查清单
            r = await client.post(
                f"{origin}/api/v1/bid/export",
                json={"project_id": project_id, "format": "both",
                      "include_checklist": True},
                headers={"Authorization": token},
            )
            if r.status_code != 200:
                checks.append(("导出接口 200", False))
                raise RuntimeError(f"export failed: {r.status_code} {r.text[:400]}")
            exp_data = r.json()
            docx_url = exp_data.get("docx_url", "")
            pdf_url = exp_data.get("pdf_url", "")
            ck_docx_url = exp_data.get("checklist_docx_url", "")
            ck_pdf_url = exp_data.get("checklist_pdf_url", "")

            checks.append(("导出 zip: docx_url 返回", bool(docx_url)))
            checks.append(("pdf_url 返回（容器内 LibreOffice 生效）", bool(pdf_url)))
            checks.append(("checklist_docx_url 返回", bool(ck_docx_url)))
            checks.append(("checklist_pdf_url 返回", bool(ck_pdf_url)))

            docx_bytes = (await client.get(docx_url,
                                          headers={"Authorization": token})).content
            # …（既有 docx × python-docx 校验段落原样保留）…

            if pdf_url:
                pdf_resp = await client.get(pdf_url, headers={"Authorization": token})
                checks.append(("主标书 PDF 可下载", pdf_resp.status_code == 200
                               and len(pdf_resp.content) > 10000))

            if ck_docx_url:
                ck_resp = await client.get(ck_docx_url, headers={"Authorization": token})
                ck_ok = ck_resp.status_code == 200 and len(ck_resp.content) > 1000
                checks.append(("检查清单 docx 可下载", ck_ok))
                if ck_ok:
                    import io
                    ck_doc = Document(io.BytesIO(ck_resp.content))
                    ck_table = ck_doc.tables[0]
                    headers = [c.text for c in ck_table.rows[0].cells]
                    checks.append(("检查清单表头含 说明/页码/状态/确认/备注",
                                   all(h in headers for h in
                                       ("说明", "页码", "状态", "确认", "备注"))))
                    checks.append(("检查清单 ≥9 行", len(ck_table.rows) - 1 >= 9))
                    body = "\n".join(p.text for p in ck_doc.paragraphs)
                    checks.append(("检查清单含标题+项目名",
                                   "投标文件检查清单" in body
                                   and project_name in body))

            if ck_pdf_url:
                ck_pdf_resp = await client.get(ck_pdf_url, headers={"Authorization": token})
                checks.append(("检查清单 PDF 可下载", ck_pdf_resp.status_code == 200
                               and len(ck_pdf_resp.content) > 1000))
```

实现时对齐既有块的变量名与检查模式（`checks.append((描述, bool))`、`project_name` 是否已在作用域；若该 export 块用 `dl = await client.get(...)` 模式则保持一致）。原 `json={"project_id": ..., "format": "docx"}` 改为上面的 `format: "both"`。

- [ ] **Step 2: 静态校验脚本可运行**

Run: `cd backend && python -m py_compile scripts/e2e_smoke.py && python -c "import sys; sys.path.insert(0,'.'); import scripts.e2e_smoke"`（仅编译/导入检查——完整运行在容器内，部署时跑）
Expected: 无语法/导入错误。

- [ ] **Step 3: 全量回归（开发机）**

Run: `cd backend && python -m pytest tests -q`
Expected: 基线不变 + 全部新增测试绿。

- [ ] **Step 4: 提交**

```bash
cd "D:/2026/投标软件"
git add backend/scripts/e2e_smoke.py
git commit -m "test: E2E 冒烟加 pdf_url/检查清单断言（format=both + 清单表头与行数）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review（已执行）

**Spec 覆盖核对**（spec 章节 → Task）：

| Spec | Task |
|---|---|
| §4.0 Dockerfile libreoffice-writer + fonts-noto-cjk | T2 Step 5 |
| §4.1 挂接点 export_bid + §13 铁律 try/except | T2 Step 4 |
| §4.2 ExportRequest/ExportResponse 契约 | T2 Step 3 + schema 测试 |
| §4.3 流程（pdf_for_locate、补渲、文件名 `-检查清单`、OUTPUT_DIR、GET /download） | T2 Step 4 |
| §4.4 页扫 locate_page/fill_pages | T1 |
| §4.5 build_checklist_docx（5 列/表头加粗/网格/☐/行高≥1.5cm） | T1 |
| §3.2 9 行模板 + 合并规则 | T1（测试 test_merge_* 覆盖 meta 第 3 行） |
| §3.3 derive_status 两枚举 + 折半 | T1 |
| §5 三级降级（PDF 缺→校验源、全挂→url 空、行挂→行级） | T1 test_fill_pages_missing_pdf + T2 try/except |
| §6.1 单元测试 5 类 | T1 |
| §6.2 E2E 断言（format=both、pdf 非空、清单 url/表头） | T4 |
| §7 前端导出面板 + 下载 | T3 |
| §8 约束（无新依赖、兼容旧前端、无 DB 迁移） | Global Constraints + T2（无 alembic） |

**占位符扫描**：所有步骤含完整代码/命令/预期；无 "TBD/TODO"。

**类型一致性**：`merge_items(items, removed)`、`derive_statuses(rows, *, source_ctx)`、`locate_page(page_texts, keywords)`、`fill_pages(pdf_path, rows)`、`build_checklist_docx(project_name, rows, out_path, *, generated_at)` 在 T1 定义、T2 调用——签名逐字一致。响应字段 `checklist_docx_url/checklist_pdf_url` 在 T2 定义、T3/T4 消费，命名一致。

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-28-checklist-doc.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**