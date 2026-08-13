"""宏曦标书 - 新管线端到端冒烟测试（只读 + 一次性测试项目）.

验证 generate_from_chapter_structure 完整链路：
  手工构造 children_json 嵌套树 → POST /bid/generate(target_pages) → SSE 事件序列 →
  树形组装 → 容器引导段 → children_json 回写 → 格式校验 → done。

用法（在服务器容器内）：
  docker exec -w /app -e PYTHONPATH=/app hongxi-backend python scripts/e2e_smoke.py

说明：
  - 创建一个一次性测试项目（名称前缀「【E2E】」），生成前会清理同名旧项目；
  - 标题树手工构造，跳过 extract/refine（避免大额 AI 调用）；
  - 仅跑 generate 阶段，10 个叶子 + 6 个容器引导段，target_pages=100，成本极低；
  - 结束时打印校验报告；退出码 0 = 通过，1 = 失败。

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

import httpx

API_BASE = "http://127.0.0.1:8000/api/v1"
TEST_NAME = "【E2E】新管线冒烟验证"
TARGET_PAGES = 100
BOT_USERNAME = "e2e_bot"

# ---------------------------------------------------------------------------
# 测试数据：手工构造的 children_json 嵌套树
# depth: 顶层容器=1(→##)、叶子=2(→###)；章节本身是包装节点(depth 0 → #)
# ---------------------------------------------------------------------------
TREE_SERVICE = [
    {
        "title": "服务方案概述", "depth": 1, "token_budget_hint": "medium",
        "children": [
            {"title": "服务理念与总体目标", "depth": 2, "token_budget_hint": "medium"},
            {"title": "服务范围与边界界定", "depth": 2, "token_budget_hint": "medium"},
            {"title": "服务质量承诺", "depth": 2, "token_budget_hint": "medium"},
        ],
    },
    {
        "title": "岗位人员配置", "depth": 1, "token_budget_hint": "medium",
        "children": [
            {"title": "项目组织架构", "depth": 2, "token_budget_hint": "medium"},
            {"title": "关键岗位人员资质要求", "depth": 2, "token_budget_hint": "medium"},
            {"title": "人员培训与考核计划", "depth": 2, "token_budget_hint": "medium"},
        ],
    },
]

TREE_TECH = [
    {
        "title": "进场与实施计划", "depth": 1, "token_budget_hint": "medium",
        "children": [
            {"title": "进场交接计划", "depth": 2, "token_budget_hint": "medium"},
            {"title": "分阶段实施进度安排", "depth": 2, "token_budget_hint": "medium"},
        ],
    },
    {
        "title": "质量保障措施", "depth": 1, "token_budget_hint": "medium",
        "children": [
            {"title": "质量管理体系", "depth": 2, "token_budget_hint": "medium"},
            {"title": "检查与整改闭环机制", "depth": 2, "token_budget_hint": "medium"},
        ],
    },
]

CHAPTERS = [
    {"title": "投标函", "order_index": 0, "chapter_type": "fixed_form", "children": []},
    {"title": "项目整体服务方案", "order_index": 1, "chapter_type": "ai_generated", "children": TREE_SERVICE},
    {"title": "技术实施方案", "order_index": 2, "chapter_type": "ai_generated", "children": TREE_TECH},
]

# 注意：与真实解析结果一致 —— service_requirements 是字符串数组，
# evaluation_criteria 是字符串。之前用对象数组会触发 join(dict) 崩溃。
REQUIREMENTS = {
    "project_name": "物业管理服务采购项目（E2E冒烟测试）",
    "procurement_type": "service",
    "project_location": "昆明市",
    "project_duration": "一年",
    "service_requirements": [
        "保安服务：门岗值守与巡逻服务",
        "保洁服务：公共区域保洁服务",
        "综合管理：物业管理及综合协调",
    ],
    "special_requirements": ["不得转包分包", "服务响应时间不超过30分钟"],
    "evaluation_criteria": "服务方案40%、技术方案30%、报价30%，综合评分法评标",
}

FORMAT_TEMPLATE = {
    "document_structure": [
        {"title": "投标函", "type": "fixed_form", "required": True},
        {"title": "项目整体服务方案", "type": "ai_generated", "required": True},
        {"title": "技术实施方案", "type": "ai_generated", "required": True},
    ],
    "global_format_rules": {
        "font": "宋体",
        "font_size": "小四",
        "heading_levels": ["一、", "1.1", "1.1.1"],
    },
}

# ---------------------------------------------------------------------------
# 空标题扫描（与 verify_generation.py 一致）
# ---------------------------------------------------------------------------
HEADING_RE = re.compile(r"^(#{1,4})\s*(.*)$")


def scan_empty_headings(content: str) -> list[dict]:
    if not content:
        return []
    lines = content.split("\n")
    issues: list[dict] = []
    n = len(lines)
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line.strip())
        if not m:
            continue
        title = m.group(2).strip()
        if not title:
            issues.append({"line": i + 1, "type": "bare_heading"})
            continue
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and HEADING_RE.match(lines[j].strip()):
            issues.append({"line": i + 1, "type": "empty_section", "next": lines[j].strip()[:40]})
    return issues


def walk_leaves(nodes: list, out: list):
    for nd in nodes:
        kids = nd.get("children") or []
        if kids:
            walk_leaves(kids, out)
        else:
            out.append(nd)


def walk_containers(nodes: list, out: list):
    for nd in nodes:
        kids = nd.get("children") or []
        if kids:
            out.append(nd)
            walk_containers(kids, out)


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    body = body.replace("\r\n", "\n")  # sse_starlette 事件帧用 \r\n 行结束符
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        ev, data = "message", ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                ev = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        try:
            events.append((ev, json.loads(data)))
        except Exception:
            events.append((ev, {"_raw": data}))
    return events


async def ensure_bot_user() -> tuple[str, str]:
    """确保 e2e_bot 用户存在，并伪造一个有效 token."""
    from sqlalchemy import select
    from app.database import async_session
    from app.models.user import User
    from app.utils.security import create_access_token

    async with async_session() as db:
        res = await db.execute(select(User).where(User.username == BOT_USERNAME))
        user = res.scalar_one_or_none()
        if not user:
            user = User(
                username=BOT_USERNAME,
                password_hash="!e2e!",
                display_name="E2E Bot",
                role="admin",
            )
            db.add(user)
            await db.flush()
            await db.commit()  # async_session() 不会自动提交，必须显式 commit
        token = create_access_token({"sub": user.id})
        return user.id, token


async def db_seed(project_id: str):
    """写 project 字段 + 章节（含 children_json 嵌套树）."""
    from sqlalchemy import select
    from app.database import async_session
    from app.models.project import BidProject, ProjectChapter

    async with async_session() as db:
        proj = await db.get(BidProject, project_id)
        proj.parsed_requirements_json = json.dumps(REQUIREMENTS, ensure_ascii=False)
        proj.format_template_json = json.dumps(FORMAT_TEMPLATE, ensure_ascii=False)
        proj.chapter_structure_json = json.dumps(
            [{"title": c["title"], "order_index": c["order_index"]} for c in CHAPTERS],
            ensure_ascii=False,
        )
        proj.target_pages = TARGET_PAGES
        proj.status = "draft"

        old = (await db.execute(
            select(ProjectChapter).where(ProjectChapter.project_id == project_id)
        )).scalars().all()
        for c in old:
            await db.delete(c)
        await db.flush()

        for c in CHAPTERS:
            ai = c["chapter_type"] == "ai_generated"
            db.add(ProjectChapter(
                project_id=project_id,
                title=c["title"],
                order_index=c["order_index"],
                chapter_type=c["chapter_type"],
                status="pending",
                review_status="generating" if ai else "locked",
                children_json=json.dumps(c["children"], ensure_ascii=False),
                chapter_meta_json="{}",
            ))
        await db.commit()


async def db_verify(project_id: str) -> dict:
    """生成后校验 children_json 回写 + 章节内容 + 空标题 + 校验报告."""
    from sqlalchemy import select
    from app.database import async_session
    from app.models.project import BidProject, ProjectChapter

    async with async_session() as db:
        proj = await db.get(BidProject, project_id)
        chapters = (await db.execute(
            select(ProjectChapter).where(ProjectChapter.project_id == project_id)
        )).scalars().all()
        chapters = sorted(chapters, key=lambda c: c.order_index)

        report = {
            "project_status": proj.status,
            "chapters": [],
            "total_chars": sum(len(c.ai_generated_content) for c in chapters),
            "empty_headings_total": 0,
        }
        try:
            verif = json.loads(proj.format_verification_json or "{}")
            report["verification_status"] = verif.get("overall_status", "MISSING")
            report["verification_message"] = verif.get("message", "")
        except Exception:
            report["verification_status"] = "MISSING"

        for c in chapters:
            entry = {"title": c.title, "type": c.chapter_type, "status": c.status}
            if c.chapter_type == "ai_generated":
                try:
                    tree = json.loads(c.children_json or "[]")
                except json.JSONDecodeError:
                    tree = []
                leaves: list = []
                containers: list = []
                walk_leaves(tree, leaves)
                walk_containers(tree, containers)
                entry["leaves"] = len(leaves)
                entry["leaves_with_content"] = sum(
                    1 for nd in leaves if (nd.get("content") or "").strip()
                )
                entry["containers"] = len(containers)
                entry["containers_with_leadin"] = sum(
                    1 for nd in containers if (nd.get("lead_in") or "").strip()
                )
                entry["content_len"] = len(c.ai_generated_content)
                issues = scan_empty_headings(c.ai_generated_content)
                entry["empty_headings"] = issues[:5]
                report["empty_headings_total"] += len(issues)
            report["chapters"].append(entry)
        return report


async def main() -> int:
    print("=" * 70)
    print(f"E2E 冒烟测试：新管线 generate_from_chapter_structure  (target_pages={TARGET_PAGES})")
    print("=" * 70)

    user_id, token = await ensure_bot_user()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(7200, connect=10), follow_redirects=True) as client:
        # 清理同名旧测试项目
        lst = (await client.get(f"{API_BASE}/projects", headers=headers)).json()
        for p in lst:
            if p.get("name") == TEST_NAME:
                try:
                    await client.delete(f"{API_BASE}/projects/{p['id']}", headers=headers)
                except Exception:
                    pass

        # 创建一次性项目
        r = await client.post(
            f"{API_BASE}/projects", headers=headers, json={"name": TEST_NAME}
        )
        r.raise_for_status()
        project_id = r.json()["id"]
        print(f"已创建测试项目: {project_id}")

    await db_seed(project_id)
    print("已写入格式模板 / 章节结构 / 嵌套标题树（10 叶子，6 容器）")

    # ---- 触发生成并流式读取 SSE ----
    print("\n开始生成...（真实 AI 调用，预计数分钟）")
    raw_body = b""   # 完整原始响应，供最终解析
    pending = ""     # 增量解析缓冲（只留未完成的半帧）
    async with httpx.AsyncClient(timeout=httpx.Timeout(7200, connect=10), follow_redirects=True) as client:
        async with client.stream(
            "POST",
            f"{API_BASE}/bid/generate",
            headers=headers,
            json={"project_id": project_id, "target_pages": TARGET_PAGES},
        ) as resp:
            print(f"generate HTTP {resp.status_code}", flush=True)
            if resp.status_code != 200:
                raw_body = (await resp.aread()).decode("utf-8", "replace")
                print("响应体:", raw_body[:2000])
                return 1
            async for chunk in resp.aiter_bytes():
                raw_body += chunk
                pending += chunk.decode("utf-8", "replace").replace("\r\n", "\n")
                # 增量解析，打印实时进度（仅对事件名/progress/section_done 打点）
                if "\n\n" in pending:
                    frames = pending.rsplit("\n\n", 1)
                    complete, pending = frames[0], frames[1]
                    for ev, d in parse_sse(complete):
                        if ev == "outline_generated":
                            print(f"  >> outline_generated: {d.get('total_leaves')} 小节, 预计 {d.get('estimated_pages')} 页", flush=True)
                        elif ev == "progress":
                            pct = d.get("percentage", 0)
                            if int(pct) % 20 == 0:
                                print(f"  >> 进度 {d.get('completed')}/{d.get('total')} ({pct}%)", flush=True)
                        elif ev == "section_done":
                            print(f"  >> 完成: {d.get('title')} ({d.get('content_length', 0)} 字)", flush=True)
                        elif ev == "section_error":
                            print(f"  >> 失败: {d.get('title')}: {d.get('error')}", flush=True)
                        elif ev in ("status", "format_verification", "done"):
                            print(f"  >> {ev}: {json.dumps(d, ensure_ascii=False)[:200]}", flush=True)
                        elif ev == "error":
                            print(f"  >> error: {d.get('message', d)}", flush=True)

    events = parse_sse(raw_body.decode("utf-8", "replace"))
    seq = [ev for ev, _ in events]
    print(f"\nSSE 事件总数: {len(events)}")
    print("事件序列:", " -> ".join(seq))

    checks = []
    for name in ("status", "outline_generated", "section_start", "section_done", "progress", "format_verification", "done"):
        checks.append((f"事件 {name}", name in seq))
    # 管线级崩溃 = 出现 error 事件；单节 section_error 是 AI 瞬时失败被管线容错处理（重试+占位），允许。
    crash_evts = [d for ev, d in events if ev == "error"]
    err_evts = [d for ev, d in events if ev == "section_error"]
    checks.append(("无管线级 error 事件（崩溃）", not crash_evts))
    print(f"  单节失败（section_error，AI 瞬时失败被容错）: {len(err_evts)} 个")

    outline = next((d for ev, d in events if ev == "outline_generated"), None)
    if outline:
        est = outline.get("estimated_pages", 0)
        dev = abs(est - TARGET_PAGES) / TARGET_PAGES
        checks.append((
            f"outline_generated 预计页数 ≈ target ({est} vs {TARGET_PAGES})",
            dev <= 0.25,
        ))
        print(f"  outline: {outline.get('total_leaves')} 小节, 预计 {est} 页")

    done = next((d for ev, d in events if ev == "done"), None)
    if done:
        checks.append(("done 章节数 ≥ 3", done.get("chapters_count", 0) >= 3))
        print(f"  done: {done.get('chapters_count')} 章, {done.get('total_chars')} 字, "
              f"完成 {done.get('completed_sections')}/{done.get('total_sections')} 节")

    verif = next((d for ev, d in events if ev == "format_verification"), None)
    if verif:
        vs = verif.get("overall_status", "unknown")
        checks.append(("format_verification 发出", True))
        print(f"  格式校验: overall_status = {vs}")

    # ---- 落库校验 ----
    rep = await db_verify(project_id)
    print("\n落库校验:")
    print(f"  项目状态: {rep['project_status']}    总字数: {rep['total_chars']}")
    print(f"  格式校验报告: {rep.get('verification_status')}  {rep.get('verification_message', '')}")
    for e in rep["chapters"]:
        if e["type"] == "ai_generated":
            lc = f"{e['leaves_with_content']}/{e['leaves']}"
            cc = f"{e['containers_with_leadin']}/{e['containers']}"
            print(f"  - {e['title']}: content={e['content_len']}字, 叶子content {lc}, 容器lead_in {cc}, "
                  f"空标题 {len(e['empty_headings'])}")
            # 允许 1 个叶子因 AI 瞬时失败被容错（占位），管线合同是"不崩溃+尽量回填"
            checks.append((
                f"[{e['title']}] 叶子回填 ≥ {e['leaves'] - 1}/{e['leaves']}",
                e["leaves"] > 0 and e["leaves_with_content"] >= e["leaves"] - 1,
            ))
            checks.append((f"[{e['title']}] 容器全部有引导段", e["containers_with_leadin"] == e["containers"]))
            checks.append((f"[{e['title']}] 无空标题", not e["empty_headings"]))
        else:
            print(f"  - {e['title']}: type={e['type']} status={e['status']}")
    checks.append(("整体无空标题", rep["empty_headings_total"] == 0))

    # ---- 报告 ----
    print("\n" + "=" * 70)
    ok = True
    for label, passed in checks:
        mark = "✅" if passed else "❌"
        print(f"  {mark} {label}")
        ok = ok and passed
    print("=" * 70)
    print(f"测试项目 id: {project_id}（可到前端查看生成的目录树与引导段）")
    if ok:
        print("✅ E2E 冒烟测试通过")
        return 0
    print("❌ E2E 冒烟测试存在失败项")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(asyncio.run(main()))
