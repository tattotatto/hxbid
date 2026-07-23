#!/bin/bash
# 宏曦标书 — 服务器更新脚本
# 用法: bash update.sh
set -e

cd /hxbid/hongxi-bid

echo "========================================"
echo "  宏曦标书 — 服务器更新"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# 1. 拉取最新代码
echo ""
echo ">>> 拉取最新代码..."
git pull

# 2. 重建并启动容器
echo ""
echo ">>> 重建镜像并启动..."
docker compose up -d --build

# 3. 等待服务就绪
echo ""
echo ">>> 等待服务就绪..."
sleep 8

# 4. 容器状态
echo ""
echo ">>> 容器状态:"
docker compose ps

# 5. 健康检查
echo ""
echo ">>> 健康检查:"
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 10 http://localhost:8888/docs 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✅ HTTP $HTTP_CODE — 服务正常"
else
    echo "  ❌ HTTP $HTTP_CODE — 请检查日志"
    echo ""
    echo ">>> 最近日志:"
    docker compose logs --tail=20 backend
fi

echo ""
echo "========================================"
echo "  更新完成"
echo "========================================"
