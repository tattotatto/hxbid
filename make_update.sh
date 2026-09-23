#!/bin/bash
# ============================================================
# 宏曦标书 — 离线更新包制作（git bundle）
# 用法: bash make_update.sh
# 输出: /tmp/hxbid-update-<commit>-<时间>.bundle
#
# 【为什么改成 bundle】
# 旧版本按**硬编码的文件清单**挑文件打包（14 个后端 + 4 个前端），再在服务器上
# `docker cp` 进容器。两个问题：
#   1. 改了清单外的文件会**静默漏发**——`collection.py` / `score_engine.py` 都曾
#      不在清单里，而它们承载着当时要上线的修复；
#   2. `docker cp` 会让容器与镜像分叉，正是 2026-09-23 审计出来那堆「容器版本
#      混杂」的根源。
# bundle 是完整的 git 对象包：服务器 `git pull` 之后拿到的就是完整仓库，不可能漏；
# 再 `docker compose up -d --build` 让容器从 git 重建，镜像与容器就不会分叉。
#
# 【服务器端用法】
#   cd /hxbid/hongxi-bid
#   git pull /path/to/hxbid-update-xxxx.bundle master
#   docker compose up -d --build
#   docker exec hongxi-backend alembic -c /app/alembic.ini upgrade head
# 也可以直接用 update.sh（它就是这个流程）。
# ============================================================
set -e

cd "$(dirname "$0")"

echo "========================================"
echo "  宏曦标书 — 离线更新包制作"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# ── 1. 必须干净：有未提交改动就打不出完整包 ──
if [ -n "$(git status --porcelain)" ]; then
    echo ""
    echo "!! 工作树有未提交改动，先提交再打包（否则包里没有这些改动）:"
    git status --short | head -20
    exit 1
fi

# ── 2. 必须与远端一致：避免打出别人没有的提交 ──
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
LOCAL="$(git rev-parse HEAD)"
git fetch origin "$BRANCH" --quiet 2>/dev/null || true
REMOTE="$(git rev-parse "origin/$BRANCH" 2>/dev/null || echo '')"
if [ -n "$REMOTE" ] && [ "$LOCAL" != "$REMOTE" ]; then
    echo ""
    echo "!! 本地 $BRANCH 与 origin/$BRANCH 不一致"
    echo "   本地 $LOCAL"
    echo "   远端 $REMOTE"
    echo "   先 push 或 pull，再打包"
    exit 1
fi

# ── 3. 打 bundle ──
SHORT="$(git rev-parse --short HEAD)"
OUT="/tmp/hxbid-update-${SHORT}-$(date '+%Y%m%d-%H%M%S').bundle"

echo ""
echo ">>> 打包 git bundle（完整对象，含全部提交）..."
git bundle create "$OUT" --all

echo ""
echo "========================================"
echo "  更新包已生成"
echo "  文件: $OUT"
echo "  大小: $(du -h "$OUT" | cut -f1)"
echo "  提交: $(git log -1 --oneline)"
echo "========================================"
echo ""
echo "服务器更新步骤:"
echo "  1. 把 $OUT 拷到服务器 (U盘 / scp)"
echo "  2. 在服务器上执行:"
echo "     cd /hxbid/hongxi-bid"
echo "     git pull $OUT master"
echo "     docker compose up -d --build"
echo "     docker exec hongxi-backend alembic -c /app/alembic.ini upgrade head"
echo "  3. 验收:"
echo "     docker compose ps && curl -s -o /dev/null -w '%{http_code}\\n' http://localhost:8888/docs"
echo ""
