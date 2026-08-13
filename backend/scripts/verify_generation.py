"""宏曦标书 - 生成结果只读校验报告.

对指定项目检查：
  (a) 实际篇幅估计是否落在 target_pages 的 ±20%；
  (b) 扫描"空标题"（裸标题紧跟下一标题 / 标题无正文）；
  (c) verify_format 结果（format_verification_json）；
  (d) required 必需章节齐全且顺序正确（validate_chapter_structure 对账）；
  (e) 目录树健康度：叶子是否都回填 content、容器是否都有 lead_in（新管线）。

用法：
  cd backend
  python scripts/verify_generation.py <project_id>            # 按 id
  python scripts/verify_generation.py --name <项目名>         # 按名称模糊匹配
  python scripts/verify_generation.py --latest               # 最近更新的项目

只读脚本，不做任何写操作。退出码：0 = 全部通过；1 = 存在告警/失败项。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import async_session
from app.models.project import BidProject, ProjectChapter

# 判断是否为"空标题"行：标题行后（允许空行）紧跟另一标题行
HEADING_RE = re.compile(r"^(#{1,4})\s*(.*)$")
BARE_HEADING_RE = re.compile(r"^#{1,4}\s*$")


def _fuzzy_match(a: str, b: str) -> bool:
    """标题模糊匹配：任一包含另一即视为命中."""
    return a in b or b in a


def _count_leaves(nodes: list) -> int:
    count = 0
    for n in nodes:
        kids = n.get("children") or []
        if kids:
            count += _count_leaves(kids)
        else:
            count += 1
    return count


def _walk_containers(nodes: list, out: list):
    for n in nodes:
        kids = n.get("children") or []
        if kids:
            out.append(n)
            _walk_containers(kids, out)


def _walk_leaves(nodes: list, out: list):
    for n in nodes:
        kids = n.get("children") or []
        if kids:
            _walk_leaves(kids, out)
        else:
            out.append(n)


def _scan_empty_headings(content: str) -> list[dict]:
    """扫描空标题：标题无标题文字，或标题后紧跟另一标题（无正文）."""
    if not content:
        return []
    lines = content.split("\n")
    issues: list[dict] = []
    n = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = HEADING_RE.match(stripped)
        if not m:
            continue
        title = m.group(2).strip()
        line_no = i + 1
        if not title:
            issues.append({"line": line_no, "type": "bare_heading", "preview": stripped[:60]})
            continue
        # 检查该标题下是否紧跟（允许空行）另一个标题
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and HEADING_RE.match(lines[j].strip()):
            issues.append({
                "line": line_no,
                "type": "empty_section",
                "preview": f"{stripped[:50]}  →  下一个标题: {lines[j].strip()[:50]}",
            })
    return issues


async def _load_project(proj_ref: str, by_name: bool, latest: bool) -> BidProject | None:
    async with async_session() as db:
        if latest:
            result = await db.execute(
                select(BidProject)
                .options(selectinload(BidProject.chapters))
                .order_by(BidProject.updated_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()
        if by_name:
            result = await db.execute(
                select(BidProject)
                .options(selectinload(BidProject.chapters))
                .where(BidProject.name.ilike(f"%{proj_ref}%"))
                .order_by(BidProject.updated_at.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()
        result = await db.execute(
            select(BidProject)
            .options(selectinload(BidProject.chapters))
            .where(BidProject.id == proj_ref)
        )
        return result.scalar_one_or_none()


def _check_pages(project: BidProject, chapters: list[ProjectChapter], chars_per_page: int) -> dict:
    target = project.target_pages or settings.GENERATION_TARGET_PAGES_DEFAULT
    ai_chapters = [c for c in chapters if c.chapter_type in ("ai_generated", "text", "mixed")]
    total_chars = sum(
        len((c.final_content or c.ai_generated_content or ""))
        for c in ai_chapters
    )
    actual_pages = max(1, int(total_chars / chars_per_page)) if total_chars else 0
    ok = abs(actual_pages - target) / max(target, 1) <= 0.20
    return {
        "target_pages": target,
        "total_chars": total_chars,
        "estimated_pages": actual_pages,
        "within_20pct": ok,
        "deviation_pct": round(abs(actual_pages - target) / max(target, 1) * 100, 1) if target else 0,
    }


def _check_tree_health(chapters: list[ProjectChapter]) -> dict:
    """检查嵌套树：叶子 content 回填率、容器 lead_in 覆盖率."""
    total_leaves = 0
    leaves_with_content = 0
    total_containers = 0
    containers_with_leadin = 0
    tree_chapters = 0

    for c in chapters:
        if c.chapter_type not in ("ai_generated",):
            continue
        try:
            nodes = json.loads(c.children_json) if c.children_json else []
        except json.JSONDecodeError:
            nodes = []
        if not isinstance(nodes, list) or not nodes:
            continue
        # 扁平旧格式跳过（不走树健康检查）
        if isinstance(nodes[0], dict) and "path" in nodes[0]:
            continue
        tree_chapters += 1
        leaves: list = []
        containers: list = []
        _walk_leaves(nodes, leaves)
        _walk_containers(nodes, containers)
        for leaf in leaves:
            total_leaves += 1
            if (leaf.get("content") or "").strip():
                leaves_with_content += 1
        for node in containers:
            total_containers += 1
            if (node.get("lead_in") or "").strip():
                containers_with_leadin += 1

    return {
        "tree_chapters": tree_chapters,
        "total_leaves": total_leaves,
        "leaves_with_content": leaves_with_content,
        "leaf_content_rate": round(leaves_with_content / max(total_leaves, 1) * 100, 1),
        "total_containers": total_containers,
        "containers_with_leadin": containers_with_leadin,
        "container_leadin_rate": round(containers_with_leadin / max(total_containers, 1) * 100, 1),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="标书生成结果只读校验")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("project_id", nargs="?", help="项目 ID")
    group.add_argument("--name", dest="by_name", help="按项目名称模糊匹配")
    group.add_argument("--latest", action="store_true", help="最近更新的项目")
    args = parser.parse_args()

    project = await _load_project(
        args.project_id or "",
        by_name=bool(args.by_name),
        latest=args.latest,
    )
    if not project:
        print(f"❌ 未找到项目（ref={args.project_id or args.by_name or 'latest'}）", file=sys.stderr)
        return 1

    chapters = sorted(project.chapters, key=lambda c: c.order_index)
    print("=" * 70)
    print(f"项目: {project.name}")
    print(f"  id: {project.id}    状态: {project.status}    章节数: {len(chapters)}")
    print("=" * 70)

    checks_pass = True

    # ── (a) 页数校验 ──
    pages = _check_pages(project, chapters, settings.GENERATION_CHARS_PER_PAGE)
    print("\n(a) 篇幅校验（目标页数 vs 实际字符估算）")
    print(f"  目标页数: {pages['target_pages']}")
    print(f"  实际字符数: {pages['total_chars']}")
    print(f"  估算页数(≈{settings.GENERATION_CHARS_PER_PAGE}字/页): {pages['estimated_pages']}")
    if pages["within_20pct"]:
        print(f"  ✅ 偏差 {pages['deviation_pct']}% ≤ 20%")
    else:
        print(f"  ⚠️ 偏差 {pages['deviation_pct']}% > 20%（未落在 ±20% 内）")
        checks_pass = False

    # ── (b) 空标题扫描 ──
    print("\n(b) 空标题扫描")
    total_empty = 0
    empty_examples: list[tuple[str, dict]] = []
    for c in chapters:
        content = c.final_content or c.ai_generated_content or ""
        issues = _scan_empty_headings(content)
        if issues:
            total_empty += len(issues)
            empty_examples.extend((c.title, i) for i in issues[:3])
    if total_empty == 0:
        print("  ✅ 未发现空标题（裸标题 / 标题无正文）")
    else:
        print(f"  ⚠️ 发现 {total_empty} 处空标题：")
        for ch_title, issue in empty_examples[:10]:
            print(f"    - [{ch_title}] L{issue['line']} {issue['type']}: {issue['preview']}")
        checks_pass = False

    # ── (c) 格式校验结果 ──
    print("\n(c) verify_format 结果（format_verification_json）")
    try:
        vf = json.loads(project.format_verification_json or "{}")
    except json.JSONDecodeError:
        vf = {}
    status = vf.get("overall_status")
    if not status:
        print("  ℹ️ 尚无校验记录（未跑过新管线生成，或旧管线生成）")
    else:
        mark = "✅" if status == "pass" else ("⚠️" if status == "pass_with_warnings" else "❌")
        print(f"  {mark} overall_status: {status}")
        if vf.get("message"):
            print(f"    message: {vf['message']}")
        missing = vf.get("missing_required") or []
        if missing:
            print(f"    缺失必需章节: {[m.get('title') if isinstance(m, dict) else m for m in missing]}")
            checks_pass = checks_pass and status == "pass"

    # ── (d) 必需章节对账 ──
    print("\n(d) 必需章节对账（format_template vs 实际章节）")
    try:
        fmt = json.loads(project.format_template_json or "{}")
        reqs = json.loads(project.parsed_requirements_json or "{}")
    except json.JSONDecodeError:
        fmt, reqs = {}, {}
    from app.services.format_verifier import validate_chapter_structure
    vs = validate_chapter_structure(chapters, fmt or None, reqs or None)
    if vs.get("overall_status") == "fail":
        print("  ❌ " + vs.get("message", "必需章节缺失或顺序异常"))
        for m in vs.get("missing_required", []):
            print(f"    - 缺失: {m.get('title') if isinstance(m, dict) else m}")
        for oi in vs.get("order_issues", []):
            print(f"    - 顺序: {oi}")
        checks_pass = False
    elif vs.get("overall_status") == "pass_with_warnings":
        print("  ⚠️ " + vs.get("message", "必需章节齐全，但有提示"))
        for note in (vs.get("coverage_notes") or [])[:5]:
            print(f"    - 提示: {note.get('detail', note)}")
    else:
        print("  ✅ " + vs.get("message", "必需章节齐全且顺序正确"))

    # ── (e) 目录树健康度 ──
    print("\n(e) 目录树健康度（新管线 children_json 嵌套树）")
    tree = _check_tree_health(chapters)
    print(f"  嵌套树章节数: {tree['tree_chapters']}")
    print(f"  叶子总数: {tree['total_leaves']}    已回填 content: {tree['leaves_with_content']} "
          f"({tree['leaf_content_rate']}%)")
    print(f"  容器总数: {tree['total_containers']}    有 lead_in: {tree['containers_with_leadin']} "
          f"({tree['container_leadin_rate']}%)")
    if tree["total_leaves"] > 0 and tree["leaf_content_rate"] < 100:
        print("  ⚠️ 存在未回填内容的叶子（可能生成失败）")
        checks_pass = False
    if tree["total_containers"] > 0 and tree["container_leadin_rate"] < 100:
        print("  ⚠️ 存在无引导段的容器（可能有空标题风险）")
        checks_pass = False

    print("\n" + "=" * 70)
    if checks_pass:
        print("✅ 校验通过")
        return 0
    print("⚠️ 存在需要关注的问题（见上方）")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main()))
