"""用真实招标文件验证「按标题在招标原文中精确匹配」.

这是唯一能抓住「匹配到错的小节」的验证 —— 单测用的都是人造语料，而真实
招标文件的标题编号体系、目录条目、同名小节千奇百怪。曾靠它发现：用户写
「投标保证金」而原文小节叫「（五）投标保证金及基本户凭证」时，子串得分
低于命中阈值 → 状态 missing 且候选为空，用户无路可走。

用法（在 backend/ 下）：
    venv/Scripts/python.exe scripts/verify_real_tender_matching.py [输出文件]

默认扫 结果/*.pdf 与 素材/*.pdf；没有则提示并把路径作为参数传进来。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 直接 `python scripts/xxx.py` 时 sys.path[0] 是 scripts/ 而不是 backend/，
# 补上父目录，脚本才能不依赖外部 PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pdf_extractor import extract_format_section  # noqa: E402
from app.services.tender_section_matcher import match_tender_section  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

# 真实标书里最常见的固定格式小节（含两类典型失败：原文标题比它长得多、
# 以及 AI 提取的规范名与招标原文的冗长标题对不上）
PROBE_TITLES = [
    "投标函",
    "开标一览表",
    "法定代表人身份证明书",
    "法定代表人授权委托书",
    "投标保证金",
    "廉洁诚信承诺书",
    "关联关系承诺书",
]


def _default_pdfs() -> list[Path]:
    found: list[Path] = []
    for sub in ("结果", "素材"):
        d = ROOT / sub
        if d.is_dir():
            found.extend(sorted(d.glob("*.pdf")))
    return found


def _fmt_candidates(r) -> str:
    return " / ".join(
        f"{c.raw}(p{c.page},{c.score:.2f})" for c in r.candidates[:3]
    )


def check(pdf_path: Path, lines: list[str]) -> None:
    lines.append(f"### {pdf_path.name}")
    try:
        fs = extract_format_section(str(pdf_path))
    except Exception as exc:  # noqa: BLE001 - 报告里如实写出失败原因即可
        lines.append(f"  [提取失败：{type(exc).__name__}: {exc}]\n")
        return

    corpus = fs["full_text"]
    pmap = fs.get("page_map") or []
    lines.append(
        f"  格式章节：{len(corpus)} 字 | {len(fs['tables'])} 张表 | "
        f"第 {fs['start_page']}-{fs['end_page']} 页 | page_map {len(pmap)} 项"
    )

    for title in PROBE_TITLES:
        r = match_tender_section(
            title, chapter_type="fixed_form",
            format_section_text=corpus, format_page_map=pmap,
        )
        if r.status == "matched" and r.best is not None:
            sliced = corpus[r.best.start:r.best.end]
            first_line = sliced.splitlines()[0].strip() if sliced.strip() else "(空)"
            # 硬判据：切出来那段的首行必须就是命中的标题行，否则位置切错了
            ok = "OK " if first_line == r.best.raw else "!! "
            lines.append(
                f"    {ok}{title:14s} -> 「{r.best.raw}」 p{r.best.page} "
                f"score={r.best.score:.2f} 切片={len(sliced)}字 首行=「{first_line}」"
            )
        elif r.candidates:
            lines.append(
                f"    -- {title:14s} -> {r.status}，候选：{_fmt_candidates(r)}"
            )
        else:
            lines.append(f"    -- {title:14s} -> {r.status}（无候选）")
    lines.append("")


def main() -> int:
    args = sys.argv[1:]
    out_path = Path(args[0]) if args and args[0].endswith(".txt") else None
    pdfs = [Path(a) for a in args if a.endswith(".pdf")] or _default_pdfs()

    if not pdfs:
        print("没找到招标文件 PDF；把路径作为参数传进来，或在 结果/ 素材/ 下放一份。")
        return 2

    lines: list[str] = []
    for pdf in pdfs:
        if pdf.exists():
            check(pdf, lines)
        else:
            lines.append(f"### {pdf.name}\n  [跳过：文件不存在]\n")

    report = "\n".join(lines)
    if out_path:
        out_path.write_text(report, encoding="utf-8")
        print(f"written {out_path}")
    else:
        # 直接打屏时明确提醒：Windows 控制台是 GBK，中文会花
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
