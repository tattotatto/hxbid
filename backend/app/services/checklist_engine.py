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
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

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
    # 自定义行：无预置语义，唯一可用的落库校验源是 label 关键词（即 keywords，
    # merge_items 以 label 作为其唯一关键词）命中章节标题。命中 → OK，
    # 未命中 → WARN；PDF 定位结果由 fill_pages 覆盖，本节仅折半路径。
    return _has(*row.get("keywords", []))


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


def _set_run_font(run, font_name: str, font_size: float, bold: bool = False):
    """Set Western + East-Asian font, size, and bold on a ``Run``."""
    run.font.name = font_name
    run.font.size = Pt(font_size)
    run.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    rFonts.set(qn("w:eastAsia"), font_name)


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
        _set_run_font(run, "黑体", 18, True)

    meta = doc.add_paragraph()
    ts = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M")
    _set_run_font(meta.add_run(f"项目名称：{project_name}    生成时间：{ts}"), "宋体", 10.5)

    tip = doc.add_paragraph()
    _set_run_font(tip.add_run("提示：请对照标书逐项核对，在「确认」栏打勾，必要时在「备注」栏注明。"), "宋体", 10.5)

    table = doc.add_table(rows=1, cols=len(header_order))
    table.style = "Table Grid"
    for j, text in enumerate(header_order):
        cell = table.rows[0].cells[j]
        cell.text = text
        cell.width = widths[j]
        for p in cell.paragraphs:
            for run in p.runs:
                _set_run_font(run, "宋体", 12, True)

    for row in rows:
        cells = table.add_row().cells
        for j in range(len(header_order)):
            cells[j].width = widths[j]
        # 行高 ≥1.5cm 给备注书写空间（spec §4.5；hRule=atLeast 由公开 API 自动设置）
        cells[0].text = row.get("label") or ""
        cells[1].text = str(row.get("page") or "")
        cells[2].text = row.get("status") or ""
        cells[3].text = "☐"
        cells[4].text = ""
        for p in cells[0].paragraphs:
            for run in p.runs:
                _set_run_font(run, "宋体", 12)
        table.rows[-1].height = Cm(1.5)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    logger.info("检查清单已生成: %s（%d 行）", out, len(rows))
    return str(out)