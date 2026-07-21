#!/bin/bash
# 宏曦标书 — 服务器端模型下载和构建脚本
# 在服务器上直接执行，避免 SSH 嵌套引号问题

set -e

echo "========================================"
echo "1/3 下载 Embedding 模型"
echo "========================================"

export HF_ENDPOINT=https://hf-mirror.com

# Install sentence-transformers
pip3 install --break-system-packages sentence-transformers

# Download model
python3 << 'PYEOF'
import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
from sentence_transformers import SentenceTransformer
print('Downloading BAAI/bge-base-zh-v1.5 ...')
model = SentenceTransformer('BAAI/bge-base-zh-v1.5')
model.save('/hxbid/bge-base-zh-v1.5')
print('Model saved to /hxbid/bge-base-zh-v1.5')
PYEOF

echo ""
echo "Model files:"
ls -lh /hxbid/bge-base-zh-v1.5/

echo ""
echo "========================================"
echo "2/3 配置 .env"
echo "========================================"

cd /hxbid/hongxi-bid

# Check if .env exists
if [ ! -f .env ]; then
    cp .env.example .env
    # Generate secret key
    NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${NEW_KEY}/" .env
    sed -i 's|CORS_ORIGINS=.*|CORS_ORIGINS=["http://192.168.50.38:8888", "http://localhost:8888"]|' .env
fi

echo "Current .env settings:"
grep -E "SECRET_KEY|CORS_ORIGINS|DEEPSEEK_API_KEY|EMBEDDING_MODEL|VECTOR_STORE" .env

echo ""
echo "========================================"
echo "3/3 构建并启动 Docker 服务"
echo "========================================"

cd /hxbid/hongxi-bid

# Build backend
echo "Building backend..."
sudo docker compose build backend

# Build frontend
echo "Building frontend..."
sudo docker compose build frontend

# Start all services
echo "Starting services..."
sudo docker compose up -d

echo ""
echo "Waiting for services..."
sleep 10

echo ""
echo "Service status:"
sudo docker compose ps

echo ""
echo "========================================"
echo "部署完成!"
echo "访问: http://192.168.50.38:8888"
echo "========================================"
