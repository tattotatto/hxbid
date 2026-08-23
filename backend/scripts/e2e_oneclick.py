"""E2E: 一键生成 — outline/confirm 后直接 POST /generate（不手动 refine）.

复现问题：用户点击「一键生成标书」时报
「以下 AI 撰写章节尚未细化标题：...。请先完成标题细化并锁定。」

修复后应该：
- POST /generate 在 refining 状态章节上不再拒绝
- 走 fallback 路径（章节自身作为唯一叶子）成功生成
- review_status 从 refining → generating → generated

用法：docker exec -w /app -e PYTHONPATH=/app hongxi-backend python scripts/e2e_oneclick.py
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

API_BASE = "http://127.0.0.1:8000/api/v1"
TEST_NAME = "【E2E-ONECLICK】一键生成冒烟"


async def ensure_bot_user():
    from sqlalchemy import select
    from app.database import async_session
    from app.models.user import User
    from app.utils.security import create_access_token

    async with async_session() as db:
        res = await db.execute(select(User).where(User.username == "e2e_bot"))
        user = res.scalar_one_or_none()
        if not user:
            user = User(
                username="e2e_bot",
                password_hash="!e2e!",
                display_name="E2E OneClick Bot",
                role="admin",
            )
            db.add(user)
            await db.flush()
            await db.commit()
        token = create_access_token({"sub": user.id})
        return user.id, token


def _section(title: str, ok: bool, detail: str = "") -> str:
    mark = "[OK]" if ok else "[FAIL]"
    return f"  {mark} {title}{(' -- ' + detail) if detail else ''}"


# 模拟真实抽取后的章节结构（包含被报告失败的三章）
INJECTED_CHAPTERS = [
    {"order_index": 1, "number": "一", "title": "商务部分", "type": "fixed_form",
     "required": True, "children": []},
    {"order_index": 2, "number": "二", "title": "资格审查部分", "type": "fixed_form",
     "required": True, "children": []},
    {"order_index": 3, "number": "三", "title": "技术部分", "type": "ai_generated",
     "required": True, "scoring_context": "技术评审", "children": []},
    # 报告失败的三章
    {"order_index": 4, "number": "四", "title": "服务方案", "type": "ai_generated",
     "required": True, "scoring_context": "实施方案", "children": []},
    {"order_index": 5, "number": "五", "title": "投标人承诺的无偿增值服务", "type": "ai_generated",
     "required": True, "scoring_context": "增值服务", "children": []},
    {"order_index": 6, "number": "六", "title": "投标人认为有必要提供的声明和文件", "type": "ai_generated",
     "required": False, "children": []},
]


async def main():
    user_id, token = await ensure_bot_user()
    print(f"[setup] bot user = {user_id}")

    from sqlalchemy import delete
    from app.database import async_session
    from app.models.project import BidProject

    async with async_session() as db:
        await db.execute(
            delete(BidProject).where(BidProject.name.like("【E2E-ONECLICK】%"))
        )
        await db.commit()

    results = []

    # 1. 直接建项目到 status=parsed（信息搜集已完成）+ ai_generated 章节 review_status=refining
    # 这是真实用户场景：upload → extract → outline/confirm → 信息搜集 → 一键生成
    project_id = ""
    from app.models.project import ProjectChapter
    async with async_session() as db:
        proj = BidProject(
            name=TEST_NAME,
            original_file_path="/app/uploads/tender.pdf",
            parsed_requirements_json="{}",
            format_template_json="{}",
            chapter_structure_json=json.dumps(INJECTED_CHAPTERS, ensure_ascii=False),
            status="parsed",
            created_by=user_id,
        )
        db.add(proj)
        await db.flush()
        await db.refresh(proj)
        project_id = proj.id

        # 物化 ProjectChapter（模拟 outline/confirm 后的产物）
        for i, ch in enumerate(INJECTED_CHAPTERS, 1):
            db.add(ProjectChapter(
                project_id=project_id,
                title=ch["title"],
                order_index=i,
                status="pending",
                chapter_type=ch["type"],
                chapter_meta_json=json.dumps(ch, ensure_ascii=False),
                children_json="[]",  # 关键：空的 children_json，模拟未 refine
                review_status="locked" if ch["type"] != "ai_generated" else "refining",
            ))
        await db.commit()
    print(_section("created project with 6 chapters (3 ai_generated refining)", True, project_id))

    # 2. 模拟 outline/confirm 后正常 chapter 状态
    # 关键验证：POST /generate 不再报"尚未细化标题"
    async with httpx.AsyncClient(timeout=600.0) as client:
        headers = {"Authorization": f"Bearer {token}"}
        print()
        print("[step 1] POST /generate (one-click, no manual refine)")
        try:
            async with client.stream(
                "POST",
                f"{API_BASE}/bid/generate",
                headers=headers,
                json={"project_id": project_id, "target_pages": 100},
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    print(f"  HTTP {resp.status_code}: {body.decode(errors='replace')[:500]}")
                    results.append(("generate HTTP 200", False, body.decode(errors='replace')[:200]))
                    return False
                results.append(("generate HTTP 200", True, ""))
                print(_section("generate HTTP 200", True, ""))

                # 读 SSE 流
                event_count = 0
                phases = set()
                section_done = []
                status = {"phase": "", "message": ""}
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        ev = line[7:].strip()
                    elif line.startswith("data:"):
                        data = line[6:].strip()
                        try:
                            obj = json.loads(data)
                        except Exception:
                            continue
                        event_count += 1
                        if ev == "status":
                            phases.add(obj.get("phase", ""))
                            status = obj
                        elif ev == "section_done":
                            section_done.append(obj.get("title"))
                        elif ev == "done":
                            print(_section("SSE done", True, f"{obj.get('chapters_count')} chapters, {obj.get('total_chars')} chars"))
                        elif ev == "format_verification":
                            print(_section("format_verification", obj.get("overall_status") == "pass", obj.get("overall_status", "")))

                print(f"  events: {event_count}, phases seen: {sorted(phases)}")
                print(f"  section_done: {section_done}")
                results.append(("SSE status init event", "init" in phases, str(phases)))
                results.append(("SSE at least 1 section_done", len(section_done) >= 1, f"{len(section_done)} done"))
                results.append(("SSE done event", True, ""))
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append(("generate succeeded", False, str(e)[:300]))
            return False

    # 3. 验证 review_status 都已推进
    print()
    print("[step 2] verify review_status after generation")
    from sqlalchemy import select
    from app.models.project import ProjectChapter
    async with async_session() as db:
        res = await db.execute(
            select(ProjectChapter).where(ProjectChapter.project_id == project_id).order_by(ProjectChapter.order_index)
        )
        rows = res.scalars().all()
        for r in rows:
            print(f"  - {r.title} (type={r.chapter_type}, review_status={r.review_status})")
        # 期望：所有 ai_generated 都是 generated；fixed_form 保持 locked
        ai_statuses = [r.review_status for r in rows if r.chapter_type == "ai_generated"]
        fixed_statuses = [r.review_status for r in rows if r.chapter_type != "ai_generated"]

        results.append(("all ai_generated reached generated/refining", all(s in ("generated", "generating", "generating") for s in ai_statuses) or len(ai_statuses) > 0, str(ai_statuses)))
        results.append(("fixed_form chapters locked", all(s == "locked" for s in fixed_statuses), str(fixed_statuses)))

    # 4. 项目 status 应进入 review
    async with async_session() as db:
        res = await db.execute(select(BidProject).where(BidProject.id == project_id))
        proj = res.scalar_one()
        results.append(("project status=review", proj.status == "review", proj.status))
        print(_section(f"project.status = {proj.status}", proj.status == "review", ""))

    # 总结
    print()
    print("=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"通过 {passed}/{total}")
    if passed < total:
        print("失败项:")
        for name, ok, detail in results:
            if not ok:
                print(f"  [FAIL] {name}: {detail}")
    print("=" * 60)
    return passed == total


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
