"""E2E: 目录确认门 — outline/confirm + structure_ready guard.

流程（先 upload 真实 PDF，再直接注入 chapter_structure_json + structure_ready，
    跳过 AI 提取的不稳定性；其余全部走真实 API）：
1. ensure_bot_user() — 复用 e2e_bot
2. upload-and-parse → 取 project_id
3. 注入 8 个章节 + status="structure_ready"（模拟 extract_chapters 成功后状态）
4. chapters/chat（structure_ready） → 期望 200
5. outline/confirm → 期望 200，status=collecting，ProjectChapter 行数 > 0
6. chapters/chat（collecting） → 期望 400 + gate 提示
7. outline/confirm（collecting） → 期望 400 + gate 提示
8. chapters/lock（collecting） → 期望 400（同样 guard）

不依赖 AI 调用，专门验证 gate 行为。

用法：
  docker exec -w /app -e PYTHONPATH=/app hongxi-backend python scripts/e2e_outline.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

API_BASE = "http://127.0.0.1:8000/api/v1"
TEST_NAME = "【E2E-OUTLINE】目录确认门冒烟"

# 注入用的章节树（与真实 AI 输出一致形状）
INJECTED_CHAPTERS = [
    {"order_index": 1, "number": "一", "title": "投标函", "type": "fixed_form",
     "required": True, "children": []},
    {"order_index": 2, "number": "二", "title": "投标保证金", "type": "fixed_form",
     "required": True, "children": []},
    {"order_index": 3, "number": "三", "title": "商务响应文件", "type": "ai_generated",
     "required": True, "scoring_context": "商务评审", "children": []},
    {"order_index": 4, "number": "四", "title": "技术响应文件", "type": "ai_generated",
     "required": True, "scoring_context": "技术评审", "children": []},
    {"order_index": 5, "number": "五", "title": "报价一览表", "type": "table",
     "required": True, "table_columns": ["序号", "服务内容", "单价", "总价"],
     "children": []},
    {"order_index": 6, "number": "六", "title": "服务方案", "type": "ai_generated",
     "required": True, "scoring_context": "实施方案", "children": []},
    {"order_index": 7, "number": "七", "title": "应急预案", "type": "ai_generated",
     "required": True, "scoring_context": "应急能力", "children": []},
    {"order_index": 8, "number": "八", "title": "资格声明与证明材料", "type": "attachment",
     "required": True, "children": []},
]


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
                display_name="E2E Outline Bot",
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


async def main():
    user_id, token = await ensure_bot_user()
    print(f"[setup] bot user = {user_id}")

    from sqlalchemy import delete
    from app.database import async_session
    from app.models.project import BidProject

    async with async_session() as db:
        await db.execute(
            delete(BidProject).where(BidProject.name.like("【E2E-OUTLINE】%"))
        )
        await db.commit()

    headers = {"Authorization": f"Bearer {token}"}

    # 不依赖 upload-and-parse（其 AI 调用慢且不稳），直接构造 BidProject。
    # 找一个任意 PDF 当 original_file_path 占位（extract_chapters 不会调用）。
    import glob
    candidates = []
    for base in ["/素材", "/hxbid/hongxi-bid/素材", "/app/uploads"]:
        candidates.extend(glob.glob(f"{base}/*.pdf"))
        candidates.extend(glob.glob(f"{base}/*.PDF"))
    candidates = [p for p in candidates if os.path.getsize(p) > 1024]
    pdf_path = candidates[0] if candidates else "/app/uploads/tender.pdf"
    print(f"[setup] pdf placeholder = {pdf_path}")

    results = []

    # ── 1. 直接创建 BidProject，注入 structure_ready + 8 章节 ──
    project_id = ""
    async with async_session() as db:
        proj = BidProject(
            name=TEST_NAME,
            original_file_path=pdf_path,
            parsed_requirements_json="{}",
            format_template_json="{}",
            chapter_structure_json=json.dumps(INJECTED_CHAPTERS, ensure_ascii=False),
            status="structure_ready",
            created_by=user_id,
        )
        db.add(proj)
        await db.flush()
        await db.refresh(proj)
        project_id = proj.id
        await db.commit()
    print(_section("created project structure_ready", bool(project_id), project_id))
    results.append(("project created", bool(project_id), project_id))

    async with httpx.AsyncClient(timeout=60.0) as client:
        # ── 2. GET chapters API 应返回注入的树 ──
        r = await client.get(f"{API_BASE}/bid/{project_id}/chapters", headers=headers)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        chapters_out = body.get("chapters") if isinstance(body, dict) else body
        n = len(chapters_out or [])
        results.append(("GET chapters returns >=8", n >= 8, f"{n} chapters"))
        print(_section("GET chapters API", n >= 8, f"{n} chapters"))

        # ── 3. chapters/chat on structure_ready → 200（耗时较久，单独超时）──
        try:
            r = await client.post(
                f"{API_BASE}/bid/{project_id}/chapters/chat",
                headers=headers,
                json={"message": "把第三章标题改成「商务与报价响应」", "conversation_id": None},
                timeout=300.0,
            )
            ok = r.status_code == 200
            results.append(("chat on structure_ready 200", ok, r.text[:200]))
            print(_section("chat(structure_ready) 200", ok, r.text[:200]))
        except Exception as e:
            results.append(("chat on structure_ready 200", False, str(e)[:200]))
            print(_section("chat(structure_ready) 200", False, str(e)[:200]))

        # ── 5. outline/confirm → 200, status=collecting, ProjectChapter > 0 ──
        r = await client.post(
            f"{API_BASE}/bid/{project_id}/outline/confirm", headers=headers
        )
        ok = r.status_code == 200
        body = r.json() if ok else {}
        results.append(("confirm 200", ok, r.text[:200]))
        results.append(("confirm status=collecting", body.get("status") == "collecting", str(body)))
        results.append(("confirm chapters_count>0", body.get("chapters_count", 0) > 0, str(body)))
        print(_section("outline/confirm 200", ok, str(body)))

        # Verify ProjectChapter count
        from app.models.project import ProjectChapter
        from sqlalchemy import select as _sel
        async with async_session() as db:
            res = await db.execute(
                _sel(ProjectChapter).where(ProjectChapter.project_id == project_id)
            )
            rows = res.scalars().all()
            results.append(("ProjectChapter rows>0", len(rows) > 0, f"{len(rows)} rows"))
            print(_section("ProjectChapter materialised", len(rows) > 0, f"{len(rows)} rows"))

        # ── 6. chat on collecting → 400 ──
        r = await client.post(
            f"{API_BASE}/bid/{project_id}/chapters/chat",
            headers=headers,
            json={"message": "再改一下", "conversation_id": None},
        )
        ok = r.status_code == 400
        detail = r.json().get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        results.append(("chat on collecting rejected 400", ok, detail[:200]))
        print(_section("chat(collecting) 拒绝", ok, detail[:200]))

        # ── 7. outline/confirm on collecting → 400 ──
        r = await client.post(
            f"{API_BASE}/bid/{project_id}/outline/confirm", headers=headers
        )
        ok = r.status_code == 400
        detail = r.json().get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        results.append(("confirm on collecting rejected 400", ok, detail[:200]))
        print(_section("confirm(collecting) 拒绝", ok, detail[:200]))

        # ── 8. lock on collecting → 400 ──
        r = await client.post(
            f"{API_BASE}/bid/{project_id}/chapters/lock", headers=headers
        )
        ok = r.status_code == 400
        detail = r.json().get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        results.append(("lock on collecting rejected 400", ok, detail[:200]))
        print(_section("lock(collecting) 拒绝", ok, detail[:200]))

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

