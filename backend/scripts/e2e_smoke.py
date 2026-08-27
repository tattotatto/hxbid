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
    {"title": "商务部分", "order_index": 1, "chapter_type": "ai_generated", "children": []},
    {"title": "技术部分", "order_index": 2, "chapter_type": "ai_generated", "children": []},
    {"title": "资格审查部分", "order_index": 3, "chapter_type": "ai_generated", "children": []},
    {"title": "投标人认为需要提供的其他内容", "order_index": 4, "chapter_type": "ai_generated", "children": []},
]

# 注意：与真实解析结果一致 —— service_requirements 是字符串数组，
# evaluation_criteria 是字符串。之前用对象数组会触发 join(dict) 崩溃。
REQUIREMENTS = {
    "project_name": "物业管理服务采购项目（E2E冒烟测试）",
    "procurement_type": "service",
    "project_location": "昆明市",
    "project_duration": "一年",
    "tender_number": "E2E-2026-TEST-001",
    "service_requirements": [
        "保安服务：门岗值守与巡逻服务",
        "保洁服务：公共区域保洁服务",
        "综合管理：物业管理及综合协调",
    ],
    "special_requirements": ["不得转包分包", "服务响应时间不超过30分钟"],
    "evaluation_criteria": "服务方案40%、技术方案30%、报价30%，综合评分法评标",
    "service_location": "昆明市官渡区",
    "vat_rate": "6%",
    "invoice_type": "增值税专用发票",
    "bid_deposit_amount": "50000",
    "bid_deposit_method": "电汇",
    "quantity_months": "11",
    "unit_price_excluding_tax": "487300.00",
    "total_price_excluding_tax": "5360300.00",
}

FORMAT_TEMPLATE = {
    "document_structure": [
        {
            "number": "一", "title": "商务部分", "type": "ai_generated", "required": True,
            "children": [
                {"number": "（一）", "title": "开标一览表", "type": "table", "required": True},
                {"number": "（二）", "title": "投标函", "type": "fixed_form", "required": True},
                {"number": "（三）", "title": "法定代表人身份证明书", "type": "fixed_form", "required": True},
                {"number": "（四）", "title": "法定代表人授权委托书", "type": "fixed_form", "required": True},
                {"number": "（五）", "title": "投标保证金及基本户凭证", "type": "fixed_form", "required": True},
                {"number": "（六）", "title": "廉洁诚信承诺书", "type": "fixed_form", "required": True},
                {"number": "（七）", "title": "与招标人干部职工不存在关联关系的承诺书", "type": "fixed_form", "required": True},
            ],
        },
        {
            "number": "二", "title": "技术部分", "type": "ai_generated", "required": True,
            "children": [
                {"number": "（一）", "title": "本项目投入服务人员", "type": "table", "required": True},
                {"number": "（二）", "title": "项目服务方案", "type": "ai_generated", "required": True},
                {"number": "（三）", "title": "服务承诺", "type": "ai_generated", "required": True},
                {"number": "（四）", "title": "队伍管理制度、器材车辆保养方案及应急预案", "type": "ai_generated", "required": True},
                {"number": "（五）", "title": "其他材料", "type": "ai_generated", "required": True},
            ],
        },
        {
            "number": "三", "title": "资格审查部分", "type": "ai_generated", "required": True,
            "children": [
                {"number": "（一）", "title": "投标人基本情况表", "type": "table", "required": True},
                {"number": "（二）", "title": "企业信誉情况", "type": "fixed_form", "required": True},
                {"number": "（三）", "title": "项目人员承诺书", "type": "fixed_form", "required": True},
            ],
        },
        {
            "number": "四", "title": "投标人认为需要提供的其他内容", "type": "ai_generated", "required": True,
            "children": [],
        },
    ],
    "global_format_rules": {
        "numbering_style": "chinese_legal",
        "font": "宋体",
        "font_size": "小四",
        "heading_levels": ["一、", "（一）", "1.", "（1）"],
        "toc_heading_title": "目录",
        "cover_page_required": True,
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


# ---------------------------------------------------------------------------
# 评分指标预置（确定性，绕过 AI 提取）：仅供生成阶段发出 scoring_report 与
# confirm 自动补场景复用。tech-x 为内容型（章节标题不含「售后服务」→ gap_detect
# 判为缺失 → 自动补节点）；tech-q 为 quality 型（不参与目录补全，只喂自我评分）。
# ---------------------------------------------------------------------------
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
            "key_terms": ["售后服务"],  # 技术部分章节标题不含该词 → gap_detect 判为缺失
        },
        {
            "id": "tech-q", "dimension": "技术部分", "name": "方案的针对性",
            "points": 10, "kind": "quality", "criteria": "贴合项目实际",
            "key_terms": ["针对性"],  # quality 不参与目录补全，只喂评分
        },
    ],
}


async def db_seed(project_id: str, *, status: str = "draft", seed_rubric: bool = True):
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
        if seed_rubric:
            proj.scoring_rubric_json = json.dumps(RUBRIC_SEED, ensure_ascii=False)
        proj.status = status

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


async def confirm_gap_smoke(headers: dict) -> list[tuple[str, bool]]:
    """mini 场景：预置评分指标 → outline/confirm 自动补节点 → GET /chapters 断言 source 标记."""
    checks: list[tuple[str, bool]] = []
    pid = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10),
                                     follow_redirects=True) as client:
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
                async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10),
                                             follow_redirects=True) as client:
                    await client.delete(f"{API_BASE}/projects/{pid}", headers=headers)
            except Exception:
                pass
    return checks


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
    for name in ("status", "outline_generated", "section_start", "section_done", "progress", "format_verification", "scoring_report", "done"):
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

    scoring = next((d for ev, d in events if ev == "scoring_report"), None)
    if scoring:
        checks.append(("scoring_report 总分字段完整", isinstance(scoring.get("total"), (int, float))))
        checks.append(("scoring_report 逐项字段完整", bool(scoring.get("items"))))
        print(f"  自我评分: total={scoring.get('total')} scored_total={scoring.get('scored_total')} "
              f"({len(scoring.get('items') or [])} 项)")
    else:
        checks.append(("scoring_report 发出", False))

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
            # AI 偶发会产生紧邻的两个标题（empty_section）或裸标题（bare_heading），
            # 这是内容质量问题不是结构问题，标记为 warning，不阻塞 E2E 通过。
            if e["empty_headings"]:
                print(f"  ⚠️  [{e['title']}] 空标题 {len(e['empty_headings'])} 个: "
                      f"{e['empty_headings'][:2]}")
        else:
            print(f"  - {e['title']}: type={e['type']} status={e['status']}")
    if rep["empty_headings_total"] > 0:
        print(f"  ⚠️  整体空标题: {rep['empty_headings_total']} 个（warning，不阻塞通过）")

    # ---- docx 严格格式校验 ----
    print("\n导出 docx 并校验严格格式...")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
            exp_resp = await client.post(
                f"{API_BASE}/bid/export",
                headers=headers,
                json={"project_id": project_id, "format": "docx"},
            )
            exp_resp.raise_for_status()
            exp_data = exp_resp.json()
            docx_url = exp_data.get("docx_url", "")
            if not docx_url:
                checks.append(("docx 导出返回 URL", False))
            else:
                # 服务端返回的是以 /api/v1 开头的相对路径；
                # 若已是绝对 URL 直接使用，否则按 origin = http://host:port 拼接。
                if docx_url.startswith("http://") or docx_url.startswith("https://"):
                    dl_url = docx_url
                else:
                    origin = API_BASE[: -len("/api/v1")] if API_BASE.endswith("/api/v1") else API_BASE.rstrip("/")
                    dl_url = f"{origin}{docx_url if docx_url.startswith('/') else '/' + docx_url}"
                dl = await client.get(dl_url, headers=headers)
                dl.raise_for_status()
                docx_bytes = dl.content

                # Parse docx with python-docx
                from docx import Document
                from io import BytesIO

                doc = Document(BytesIO(docx_bytes))
                all_para_text = [p.text for p in doc.paragraphs]
                # Collect text including tables
                table_texts = []
                for tbl in doc.tables:
                    for row in tbl.rows:
                        for cell in row.cells:
                            table_texts.append(cell.text)

                # Check 1: contains 开标一览表
                full_doc_text = "\n".join(all_para_text + table_texts)
                has_opening = "开标一览表" in full_doc_text
                checks.append(("docx 含「开标一览表」", has_opening))
                print(f"  - 含「开标一览表」: {has_opening}")

                # Check 2: has Word TOC field (look for fldChar in document.xml)
                # We need to peek at the raw XML for the fldChar element
                from docx.oxml.ns import qn
                body_xml = doc.element.body.xml if hasattr(doc.element.body, 'xml') else ''
                has_toc_field = "TOC" in body_xml and "fldChar" in body_xml
                checks.append(("docx 含 Word TOC 域（fldChar + TOC）", has_toc_field))
                print(f"  - 含 Word TOC 域: {has_toc_field}")

                # Check 3: must have 商务部分, 技术部分, 资格审查部分, 投标人认为需要提供的其他内容
                required_parts = ["商务部分", "技术部分", "资格审查部分", "投标人认为需要提供的其他内容"]
                missing = [p for p in required_parts if p not in full_doc_text]
                checks.append((f"docx 含全部一级章节 {required_parts}", not missing))
                print(f"  - 含全部一级章节: missing={missing}")

                # Check 4: must have sub-sections numbered (一)(二) etc.
                required_subs = ["（一）开标一览表", "（二）投标函", "（三）法定代表人身份证明书",
                                 "（四）法定代表人授权委托书"]
                missing_subs = [s for s in required_subs if s not in full_doc_text]
                checks.append((f"docx 含商务部分子章节 ({len(required_subs) - len(missing_subs)}/{len(required_subs)})", not missing_subs))
                print(f"  - 含商务部分子章节: missing={missing_subs}")

                # Check 5: file size sanity check (must be > 10KB for a real bid)
                size_kb = len(docx_bytes) / 1024
                checks.append((f"docx 大小合理（{size_kb:.1f} KB ≥ 10 KB）", size_kb >= 10))
                print(f"  - docx 大小: {size_kb:.1f} KB")

                # Diagnostic: AI 正文不应残留 markdown 标记（* #）。渲染层已统一清理，
                # 这里若再出现说明有新的泄漏路径，标 warning 不阻塞（与空标题同一策略）。
                md_leaks = [t for t in (all_para_text + table_texts) if ("*" in t or "#" in t)]
                if md_leaks:
                    print(f"  ⚠️  docx 发现 markdown 残留标记 {len(md_leaks)} 处"
                          f"（warning，不阻塞通过）: {md_leaks[:3]}")
    except Exception as exc:
        print(f"  ⚠️  docx 校验异常: {exc}")
        import traceback
        traceback.print_exc()
        checks.append(("docx 导出与校验", False))

    # ---- 历史标书配对学习 ----
    print("\n上传招标+标书配对并等待分析...")
    try:
        from io import BytesIO
        from docx import Document as DocxDocument

        # 生成一对小的 docx 作为测试素材
        def _make_docx(text: str) -> bytes:
            doc = DocxDocument()
            for line in text.split("\n"):
                doc.add_paragraph(line)
            buf = BytesIO()
            doc.save(buf)
            return buf.getvalue()

        tender_doc = _make_docx(
            "招标文件\n第一章 投标邀请\n项目名称：示例监控项目\n第二章 投标人须知\n"
            "资质要求：须持有保安服务许可证。\n技术需求：须具备 7×24 小时响应能力。\n"
            "投标文件组成：（一）开标一览表（二）投标函（三）法定代表人身份证明书\n"
            "评标办法：综合评分法\n"
        )
        bid_doc = _make_docx(
            "投标文件\n（一）开标一览表\n项目名称：示例监控项目\n（二）投标函\n我方承诺满足全部要求。\n"
            "（三）法定代表人身份证明书\n我方持有保安服务许可证。\n技术方案\n本方案提供 7×24 小时响应。\n"
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as client:
            up = await client.post(
                f"{API_BASE}/bid-lessons/upload",
                headers=headers,
                files={
                    "tender_file": ("tender.docx", tender_doc, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                    "bid_file": ("bid.docx", bid_doc, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                },
                data={"name": "E2E-配对学习测试"},
            )
            up.raise_for_status()
            pair_id = up.json()["id"]
            checks.append(("配对上传成功", True))
            print(f"  - 配对 id: {pair_id}")

            # 轮询等待分析完成(最多 5 分钟)
            lesson_ok = False
            for _ in range(60):
                await asyncio.sleep(5)
                detail = await client.get(f"{API_BASE}/bid-lessons/{pair_id}", headers=headers)
                detail.raise_for_status()
                data = detail.json()
                if data["status"] == "ready":
                    lesson = data.get("lesson") or {}
                    lesson_ok = bool(lesson.get("requirements_coverage")) and bool(lesson.get("lessons"))
                    break
                if data["status"] == "failed":
                    print(f"  - 配对分析失败: {data.get('error')}")
                    break
            checks.append(("配对学习报告 ready 且含需求覆盖/要点", lesson_ok))
            print(f"  - 配对分析状态: {'ready' if lesson_ok else '未完成'}")
    except Exception as exc:
        print(f"  ⚠️  配对学习校验异常: {exc}")
        checks.append(("历史标书配对学习", False))

    # ---- mini 场景：评标办法驱动目录补全（confirm 自动补，独立项目）----
    checks += await confirm_gap_smoke(headers)

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
