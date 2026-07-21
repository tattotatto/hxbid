#!/bin/bash
# ============================================================
# 宏曦标书 — 内网离线部署准备脚本
# 在外网机器上运行，打包所有需要的文件供内网部署使用
#
# 用法:
#   chmod +x prepare-offline.sh
#   ./prepare-offline.sh              # 打包所有依赖
#   ./prepare-offline.sh --pip-mirror https://mirrors.aliyun.com/pypi/simple/
# ============================================================
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
step()  { echo -e "\n${CYAN}>>> $*${NC}"; }

OUTPUT_DIR="./offline-package"
PIP_MIRROR="${1:-}"

mkdir -p "${OUTPUT_DIR}"

# --------------- 1. 拉取 Docker 基础镜像 ---------------
step "1/4  拉取 Docker 基础镜像"

IMAGES=(
    "postgres:15-alpine"
    "nginx:alpine"
    "python:3.12-slim"
    "node:20-alpine"
)

for img in "${IMAGES[@]}"; do
    info "拉取: ${img}"
    docker pull "${img}"
done

info "导出镜像..."
docker save "${IMAGES[@]}" -o "${OUTPUT_DIR}/hongxi-base-images.tar"

info "压缩镜像包..."
gzip -f "${OUTPUT_DIR}/hongxi-base-images.tar"
IMAGES_SIZE=$(du -h "${OUTPUT_DIR}/hongxi-base-images.tar.gz" | cut -f1)
info "镜像包大小: ${IMAGES_SIZE}"

# --------------- 2. 下载 Embedding 模型 ---------------
step "2/4  下载 Embedding 模型"

MODEL_NAME="BAAI/bge-base-zh-v1.5"
MODEL_DIR="bge-base-zh-v1.5"

if [ ! -d "${MODEL_DIR}" ]; then
    info "下载 ${MODEL_NAME} ..."
    pip3 install sentence-transformers --quiet 2>/dev/null || pip install sentence-transformers --quiet

    python3 -c "
from sentence_transformers import SentenceTransformer
print('Downloading ${MODEL_NAME} ...')
model = SentenceTransformer('${MODEL_NAME}')
model.save('./${MODEL_DIR}')
print('Done.')
"
else
    info "模型已存在: ./${MODEL_DIR}"
fi

info "打包模型..."
tar czf "${OUTPUT_DIR}/${MODEL_DIR}.tar.gz" "${MODEL_DIR}"
MODEL_SIZE=$(du -h "${OUTPUT_DIR}/${MODEL_DIR}.tar.gz" | cut -f1)
info "模型包大小: ${MODEL_SIZE}"

# --------------- 3. 打包项目代码 ---------------
step "3/4  打包项目代码"

info "打包项目..."
tar czf "${OUTPUT_DIR}/hongxi-bid.tar.gz" \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='frontend/dist' \
    --exclude='frontend/node_modules' \
    --exclude='backend/__pycache__' \
    --exclude='backend/chroma_data' \
    --exclude='backend/*.pyc' \
    --exclude='outputs' \
    --exclude='offline-package' \
    --exclude='*.md' \
    --exclude='task-*-report.md' \
    --exclude='素材' \
    --exclude='.claude' \
    --exclude='venv' \
    --exclude='.venv' \
    --exclude='*.log' \
    .

PROJECT_SIZE=$(du -h "${OUTPUT_DIR}/hongxi-bid.tar.gz" | cut -f1)
info "项目包大小: ${PROJECT_SIZE}"

# --------------- 4. 生成安装脚本 ---------------
step "4/4  生成内网安装脚本"

cat > "${OUTPUT_DIR}/install-offline.sh" << 'INSTALL_SCRIPT'
#!/bin/bash
# ============================================================
# 宏曦标书 — 内网离线安装脚本
# 在内网服务器上运行
# ============================================================
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
step()  { echo -e "\n${CYAN}>>> $*${NC}"; }

step "1/5 导入 Docker 镜像"
if [ -f hongxi-base-images.tar.gz ]; then
    info "解压镜像包..."
    gunzip -k hongxi-base-images.tar.gz
    info "导入镜像..."
    docker load -i hongxi-base-images.tar
    rm hongxi-base-images.tar
else
    echo "未找到镜像包，跳过。请确保 Docker 基础镜像已安装。"
fi

step "2/5 解压项目代码"
tar xzf hongxi-bid.tar.gz
cd "$(tar tzf hongxi-bid.tar.gz | head -1 | cut -d/ -f1)"

step "3/5 解压 Embedding 模型"
if [ -f ../bge-base-zh-v1.5.tar.gz ]; then
    tar xzf ../bge-base-zh-v1.5.tar.gz
    info "模型已就绪: ./bge-base-zh-v1.5"
else
    echo "未找到模型包，请手动准备 bge-base-zh-v1.5 目录。"
fi

step "4/5 配置环境"
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "请编辑 .env 文件填写配置:"
    echo "  vim .env"
    echo ""
    echo "必填:"
    echo "  SECRET_KEY       — openssl rand -hex 32"
    echo "  DEEPSEEK_API_KEY — AI API 密钥"
    echo "  CORS_ORIGINS     — [\"http://服务器IP:8888\"]"
    echo ""
    read -rp "按 Enter 继续..."
fi

step "5/5 构建并启动"
docker compose build
docker compose up -d

echo ""
echo "============================================"
echo " 部署完成!"
echo " 访问地址: http://$(hostname -I | awk '{print $1}'):8888"
echo "============================================"
INSTALL_SCRIPT

chmod +x "${OUTPUT_DIR}/install-offline.sh"

# --------------- 完成 ---------------
echo ""
echo "============================================"
echo " 离线部署包准备完成!"
echo "============================================"
echo ""
echo "输出目录: ${OUTPUT_DIR}/"
echo ""
ls -lh "${OUTPUT_DIR}/"
echo ""
echo "将以下文件拷贝到内网服务器:"
echo "  1. ${OUTPUT_DIR}/hongxi-base-images.tar.gz  — Docker 基础镜像"
echo "  2. ${OUTPUT_DIR}/bge-base-zh-v1.5.tar.gz    — Embedding 模型"
echo "  3. ${OUTPUT_DIR}/hongxi-bid.tar.gz           — 项目代码"
echo "  4. ${OUTPUT_DIR}/install-offline.sh          — 安装脚本"
echo ""
echo "内网服务器上执行:"
echo "  mkdir hongxi-bid && cd hongxi-bid"
echo "  cp /path/to/usb/* ."
echo "  bash install-offline.sh"
