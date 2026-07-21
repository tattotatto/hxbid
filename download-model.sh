#!/bin/bash
# ============================================================
# 宏曦标书 — Embedding Model Download Script
# 下载 sentence-transformers 模型到本地目录，供 Docker 挂载使用
#
# 用法:
#   chmod +x download-model.sh
#   ./download-model.sh                    # 下载 bge-base-zh-v1.5 (推荐)
#   ./download-model.sh bge-small-zh-v1.5  # 下载小模型 (轻量)
# ============================================================
set -e

MODEL_NAME="${1:-BAAI/bge-base-zh-v1.5}"
# 本地目录名：把 / 替换为 -
LOCAL_DIR="${MODEL_NAME//\//-}"

echo "=== 宏曦标书 — 下载 Embedding 模型 ==="
echo "模型: ${MODEL_NAME}"
echo "本地目录: ./${LOCAL_DIR}"
echo ""

# 检查是否已存在
if [ -d "./${LOCAL_DIR}" ] && [ -f "./${LOCAL_DIR}/config.json" ]; then
    echo "[OK] 模型已存在: ./${LOCAL_DIR}"
    echo "如需重新下载，请先删除该目录: rm -rf ./${LOCAL_DIR}"
    exit 0
fi

# 检查 Python 环境
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

echo "安装 sentence-transformers..."
pip3 install sentence-transformers --quiet

echo "下载模型文件..."
python3 -c "
from sentence_transformers import SentenceTransformer
print('正在下载 ${MODEL_NAME} ...')
model = SentenceTransformer('${MODEL_NAME}')
model.save('./${LOCAL_DIR}')
print('下载完成: ./${LOCAL_DIR}')
"

echo ""
echo "=== 下载完成 ==="
echo "模型路径: ./${LOCAL_DIR}"
echo ""
echo "请确保 docker-compose.yml 中的 EMBEDDING_MODEL 设置为:"
echo "  EMBEDDING_MODEL=/app/${LOCAL_DIR}"
echo "且 volumes 中包含:"
echo "  - ./${LOCAL_DIR}:/app/${LOCAL_DIR}:ro"
