#!/bin/bash
# ============================================================
# 宏曦标书 — 离线更新包制作脚本
# 用法: bash make_update.sh
# 输出: /tmp/hxbid-update-YYYYMMDD-HHMMSS.tar.gz
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TIMESTAMP=$(date '+%Y%m%d-%H%M%S')
PACKAGE_NAME="hxbid-update-${TIMESTAMP}"
BUILD_DIR="/tmp/${PACKAGE_NAME}"
PACKAGE_FILE="/tmp/${PACKAGE_NAME}.tar.gz"

echo "========================================"
echo "  宏曦标书 — 离线更新包制作"
echo "  ${TIMESTAMP}"
echo "========================================"

# ── 清理旧构建 ──
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{backend,frontend,migration}

# ── 1. 复制后端 Python 文件 ──
echo ""
echo ">>> 收集后端文件..."
cd "$SCRIPT_DIR"

# 核心服务（有变更的）
BACKEND_FILES=(
    "backend/app/api/bid.py"
    "backend/app/api/chapters.py"
    "backend/app/api/router.py"
    "backend/app/models/project.py"
    "backend/app/schemas/project.py"
    "backend/app/services/ai_pipeline.py"
    "backend/app/services/chapter_chat.py"
    "backend/app/services/chapter_extractor.py"
    "backend/app/services/content_assembler.py"
    "backend/app/services/outline_engine.py"
    "backend/app/services/render_engine.py"
    "backend/app/services/section_editor.py"
    "backend/app/services/template_filler.py"
    "backend/app/services/title_refiner.py"
)

for f in "${BACKEND_FILES[@]}"; do
    if [ -f "$f" ]; then
        mkdir -p "$BUILD_DIR/backend/$(dirname "$f")"
        cp "$f" "$BUILD_DIR/backend/$f"
        echo "  OK: $f"
    else
        echo "  SKIP (not found): $f"
    fi
done

# ── 2. 复制迁移文件 ──
echo ""
echo ">>> 收集迁移文件..."
if [ -d "backend/alembic/versions" ]; then
    cp backend/alembic/versions/20260802_0004_add_chapter_structure_columns.py \
       "$BUILD_DIR/migration/" 2>/dev/null && echo "  OK: migration file" || echo "  SKIP: migration file"
fi

# ── 3. 复制前端构建所需文件 ──
echo ""
echo ">>> 收集前端文件..."
FRONTEND_FILES=(
    "frontend/src/pages/project/ProjectWorkflow.tsx"
    "frontend/src/components/TreeEditor/TreeEditor.tsx"
    "frontend/src/components/TreeEditor/TreePanel.tsx"
    "frontend/src/components/TreeEditor/EditPanel.tsx"
)

mkdir -p "$BUILD_DIR/frontend/src"
for f in "${FRONTEND_FILES[@]}"; do
    if [ -f "$f" ]; then
        mkdir -p "$BUILD_DIR/frontend/$(dirname "$f")"
        cp "$f" "$BUILD_DIR/frontend/$f"
        echo "  OK: $f"
    else
        echo "  SKIP (not found): $f"
    fi
done

# ── 4. 写入服务器端更新脚本 ──
echo ""
echo ">>> 生成服务器更新脚本..."
cat > "$BUILD_DIR/do_update.sh" << 'UPDATE_SCRIPT'
#!/bin/bash
# ============================================================
# 宏曦标书 — 服务器端离线更新脚本
# 用法: bash do_update.sh
# 前提: 在更新包解压后的目录中执行
# ============================================================
set -e

APP_DIR="/hxbid/hongxi-bid"
BACKEND_CONTAINER="hongxi-backend"
FRONTEND_CONTAINER="hongxi-frontend"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "\n${CYAN}>>> $*${NC}"; }

UPDATE_DIR="$(pwd)"

# ── 0. 检查环境 ──
step "检查环境"
if [ ! -d "$APP_DIR" ]; then
    err "项目目录 $APP_DIR 不存在"
    exit 1
fi
if ! docker ps | grep -q "$BACKEND_CONTAINER"; then
    err "后端容器 $BACKEND_CONTAINER 未运行"
    exit 1
fi
info "环境检查通过"

# ── 1. 备份当前代码 ──
step "1/7  备份当前代码"
BACKUP_NAME="backup-$(date '+%Y%m%d-%H%M%S').tar.gz"
tar czf "/tmp/$BACKUP_NAME" "$APP_DIR/backend/app/" --absolute-names 2>/dev/null || true
info "备份保存到 /tmp/$BACKUP_NAME"

# ── 2. 更新后端代码（宿主机） ──
step "2/7  更新宿主机后端代码"
if [ -d "$UPDATE_DIR/backend" ]; then
    cp -r "$UPDATE_DIR/backend/"* "$APP_DIR/" 2>/dev/null || true
    info "后端文件已复制到 $APP_DIR"
else
    warn "未找到后端文件，跳过"
fi

# ── 3. 复制后端文件到容器 ──
step "3/7  更新容器内后端代码"
BACKEND_FILES=(
    "backend/app/api/bid.py"
    "backend/app/api/chapters.py"
    "backend/app/api/router.py"
    "backend/app/models/project.py"
    "backend/app/schemas/project.py"
    "backend/app/services/ai_pipeline.py"
    "backend/app/services/chapter_chat.py"
    "backend/app/services/chapter_extractor.py"
    "backend/app/services/content_assembler.py"
    "backend/app/services/outline_engine.py"
    "backend/app/services/render_engine.py"
    "backend/app/services/section_editor.py"
    "backend/app/services/template_filler.py"
    "backend/app/services/title_refiner.py"
)

for f in "${BACKEND_FILES[@]}"; do
    src="$APP_DIR/$f"
    dst="/app/$f"
    if [ -f "$src" ]; then
        docker exec "$BACKEND_CONTAINER" mkdir -p "$(dirname "$dst")" 2>/dev/null || true
        docker cp "$src" "$BACKEND_CONTAINER:$dst" 2>/dev/null && \
            echo "  OK: $f" || \
            echo "  FAIL: $f"
    fi
done
info "后端文件已同步到容器"

# ── 4. 运行数据库迁移 ──
step "4/7  运行数据库迁移"
# 先将迁移文件复制到容器
if [ -f "$APP_DIR/backend/alembic/versions/20260802_0004_add_chapter_structure_columns.py" ]; then
    docker cp "$APP_DIR/backend/alembic/versions/20260802_0004_add_chapter_structure_columns.py" \
        "$BACKEND_CONTAINER:/app/alembic/versions/" 2>/dev/null || true
fi
docker exec "$BACKEND_CONTAINER" alembic -c /app/alembic.ini upgrade head 2>&1 | tail -3
info "数据库迁移完成"

# ── 5. 重启后端 ──
step "5/7  重启后端服务"
cd "$APP_DIR"
docker compose restart backend 2>/dev/null || docker compose restart backend
sleep 4
info "后端已重启"

# ── 6. 更新前端 ──
step "6/7  更新前端代码并重建"
if [ -d "$UPDATE_DIR/frontend" ]; then
    cp -r "$UPDATE_DIR/frontend/"* "$APP_DIR/" 2>/dev/null || true
    info "前端文件已复制"
    # Rebuild frontend image
    docker compose build frontend 2>/dev/null || docker compose build frontend
    docker compose up -d --no-deps frontend 2>/dev/null || docker compose up -d --no-deps frontend
    info "前端已重建"
else
    warn "未找到前端文件，跳过"
fi

# ── 7. 验证 ──
step "7/7  验证"
sleep 4
echo ""
echo ">>> 容器状态:"
cd "$APP_DIR"
docker compose ps 2>/dev/null || docker compose ps

echo ""
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 10 http://localhost:8888/docs 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    info "✅ HTTP $HTTP_CODE — 服务正常"
else
    warn "⚠ HTTP $HTTP_CODE — 等待几秒后重试..."
    sleep 5
    HTTP_CODE2=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 10 http://localhost:8888/docs 2>/dev/null || echo "000")
    if [ "$HTTP_CODE2" = "200" ]; then
        info "✅ HTTP 200 — 服务正常"
    else
        err "❌ HTTP $HTTP_CODE2 — 请检查日志"
        echo ""
        echo ">>> 后端最近日志:"
        docker logs "$BACKEND_CONTAINER" --tail 20 2>/dev/null || true
    fi
fi

echo ""
echo "========================================"
echo "  更新完成  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"
UPDATE_SCRIPT

chmod +x "$BUILD_DIR/do_update.sh"

# ── 5. 打包 ──
echo ""
echo ">>> 打包..."
cd /tmp
tar czf "$PACKAGE_FILE" "$PACKAGE_NAME/"
rm -rf "$BUILD_DIR"

# ── 输出 ──
SIZE=$(du -h "$PACKAGE_FILE" | cut -f1)
echo ""
echo "========================================"
echo "  更新包已生成"
echo "  文件: ${PACKAGE_FILE}"
echo "  大小: ${SIZE}"
echo "========================================"
echo ""
echo "服务器更新步骤:"
echo "  1. 将 $PACKAGE_FILE 拷贝到服务器 (U盘 / scp)"
echo "  2. 在服务器上执行:"
echo "     tar xzf ${PACKAGE_NAME}.tar.gz"
echo "     cd ${PACKAGE_NAME}"
echo "     bash do_update.sh"
echo ""
