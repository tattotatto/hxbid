"""E2E: 已 confirm 项目回到 /outline 页面能正常显示章节.

复现问题：user outline/confirm 后,再次打开 /projects/{pid}/outline
左树显示「暂无章节」,但 ProjectChapter 行已存在。

修复：get_chapters 在 locked 分支同时返回 type 和 chapter_type，
前端 OutlineTree 兼容两种字段名。

用法：docker exec -w /app -e PYTHONPATH=/app hongxi-backend python scripts/e2e_outline_revisit.py
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

API_BASE = "http://127.0.0.1:8000/api/v1"
TEST_NAME = "【E2E-REVISIT】重新进入 outline 页面冒烟"


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
                display_name="E2E Revisit Bot",
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


INJECTED_CHAPTERS = [
    {"order_index": 1, "number": "一", "title": "商务部分", "type": "fixed_form",
     "required": True, "children": []},
    {"order_index": 2, "number": "二", "title": "服务方案", "type": "ai_generated",
     "required": True, "scoring_context": "实施", "children": []},
    {"order_index": 3, "number": "三", "title": "报价一览表", "type": "table",
     "required": True, "table_columns": ["序号", "金额"], "children": []},
    {"order_index": 4, "number": "四", "title": "投标人承诺的无偿增值服务", "type": "ai_generated",
     "required": True, "scoring_context": "增值", "children": []},
]


async def main():
    user_id, token = await ensure_bot_user()
    print(f"[setup] bot user = {user_id}")

    from sqlalchemy import delete
    from app.database import async_session
    from app.models.project import BidProject, ProjectChapter

    async with async_session() as db:
        await db.execute(
            delete(BidProject).where(BidProject.name.like("【E2E-REVISIT】%"))
        )
        await db.commit()

    # 直接构造已 confirm 状态的项目
    project_id = ""
    async with async_session() as db:
        proj = BidProject(
            name=TEST_NAME,
            original_file_path="/app/uploads/tender.pdf",
            parsed_requirements_json="{}",
            format_template_json="{}",
            chapter_structure_json=json.dumps(INJECTED_CHAPTERS, ensure_ascii=False),
            status="collecting",  # 已 outline/confirm
            created_by=user_id,
        )
        db.add(proj)
        await db.flush()
        await db.refresh(proj)
        project_id = proj.id
        for i, ch in enumerate(INJECTED_CHAPTERS, 1):
            db.add(ProjectChapter(
                project_id=project_id,
                title=ch["title"],
                order_index=i,
                status="pending",
                chapter_type=ch["type"],
                chapter_meta_json=json.dumps(ch, ensure_ascii=False),
                children_json="[]",
                review_status="refining",
            ))
        await db.commit()
    print(_section("created project (collecting, 4 chapters)", True, project_id))

    results = []
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        # 模拟前端 OutlineConfirm 页面打开: GET /bid/{pid}/chapters
        r = await client.get(f"{API_BASE}/bid/{project_id}/chapters", headers=headers)
        body = r.json()
        chapters = body.get("chapters", [])

        results.append(("GET /chapters 200", r.status_code == 200, str(r.status_code)))
        results.append(("locked=true", body.get("locked") is True, str(body.get("locked"))))
        results.append(("returned 4 chapters", len(chapters) == 4, f"{len(chapters)} chapters"))

        if chapters:
            ch = chapters[0]
            keys = list(ch.keys())
            print(f"  first chapter keys: {keys}")
            print(f"    title={ch.get('title')} type={ch.get('type')} chapter_type={ch.get('chapter_type')}")
            results.append(("type field present (new)", "type" in ch, ",".join(keys)))
            results.append(("chapter_type field present (backward compat)", "chapter_type" in ch, ",".join(keys)))
            results.append(("title correct", ch.get("title") == "商务部分", ch.get("title")))

        # 全部章节必须有 type 字段
        for ch in chapters:
            assert "type" in ch, f"chapter missing type: {ch}"
        results.append(("all chapters have type field", True, ""))

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
