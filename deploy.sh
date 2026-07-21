#!/bin/bash
# ============================================================
# 宏曦标书 (HongXi Bidding) — Docker 一键部署脚本
# 适用于 Ubuntu 20.04+ / Debian 11+
#
# 用法:
#   chmod +x deploy.sh
#   ./deploy.sh              # 交互式部署
#   ./deploy.sh --offline    # 离线/内网模式
# ============================================================
set -e

APP_NAME="宏曦标书"
REQUIRED_DISK_GB=20

# --------------- 颜色输出 ---------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "\n${CYAN}>>> $*${NC}"; }

# --------------- 权限检查 ---------------
if [ "$(id -u)" -eq 0 ]; then
    warn "当前为 root 用户，建议使用普通用户运行 (Docker 组权限)"
fi

# --------------- 系统检查 ---------------
step "1/6  检查系统环境"

# OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    info "操作系统: ${NAME} ${VERSION_ID}"
else
    warn "无法识别操作系统类型"
fi

# CPU
CPU_CORES=$(nproc 2>/dev/null || echo 1)
info "CPU 核心: ${CPU_CORES}"

# RAM
MEM_TOTAL=$(free -h 2>/dev/null | awk '/^Mem:/{print $2}' || echo "unknown")
info "内存: ${MEM_TOTAL}"

# Disk
DISK_AVAIL=$(df -BG . 2>/dev/null | awk 'NR==2{print $4}' | sed 's/G//' || echo "0")
info "可用磁盘: ${DISK_AVAIL}G"
if [ "${DISK_AVAIL}" -lt "${REQUIRED_DISK_GB}" ] 2>/dev/null; then
    warn "磁盘空间不足 ${REQUIRED_DISK_GB}G，建议至少保留 20G 空间"
fi

# --------------- Docker 检查 ---------------
step "2/6  检查 Docker 环境"

if command -v docker &>/dev/null; then
    DOCKER_VER=$(docker --version 2>/dev/null | head -1)
    info "Docker: ${DOCKER_VER}"
else
    error "未安装 Docker，请先安装 Docker Engine"
    echo ""
    echo "  Ubuntu/Debian 安装命令:"
    echo "  curl -fsSL https://get.docker.com | sudo bash"
    echo "  sudo usermod -aG docker \$USER"
    echo "  newgrp docker"
    echo ""
    echo "  内网离线安装请参考 DEPLOY.md"
    exit 1
fi

if docker compose version &>/dev/null; then
    COMPOSE_VER=$(docker compose version 2>/dev/null | head -1)
    info "Docker Compose: ${COMPOSE_VER}"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_VER=$(docker-compose --version 2>/dev/null | head -1)
    info "Docker Compose (v1): ${COMPOSE_VER}"
    COMPOSE_CMD="docker-compose"
else
    error "未安装 Docker Compose"
    exit 1
fi

# 使用 Docker Compose v2 命令
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
else
    COMPOSE_CMD="docker-compose"
fi

# --------------- 环境配置 ---------------
step "3/6  配置环境变量"

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        info "从 .env.example 创建 .env 配置文件..."
        cp .env.example .env
        warn "请编辑 .env 文件，填写必要配置后重新运行此脚本"
        echo ""
        echo "  必填项:"
        echo "    SECRET_KEY         — 随机密钥 (建议: openssl rand -hex 32)"
        echo "    DEEPSEEK_API_KEY   — AI API 密钥"
        echo "    CORS_ORIGINS       — 服务器访问地址"
        echo ""
        echo "  编辑后执行: ${COMPOSE_CMD} up -d"
        exit 0
    else
        error "未找到 .env.example 模板文件"
        exit 1
    fi
else
    info "已找到 .env 配置文件"
fi

# 检查必填配置
source_env_safe() {
    # 安全地加载 .env 中的变量（跳过注释和空行）
    while IFS='=' read -r key value; do
        case "$key" in
            ''|\#*) continue ;;
            *) export "$key=$value" 2>/dev/null || true ;;
        esac
    done < .env
}
source_env_safe

if [ "${SECRET_KEY}" = "change-me-in-production" ] || [ -z "${SECRET_KEY}" ]; then
    warn "SECRET_KEY 仍为默认值，正在生成随机密钥..."
    NEW_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || echo "")
    if [ -n "${NEW_KEY}" ]; then
        sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${NEW_KEY}/" .env
        info "已自动生成 SECRET_KEY"
    fi
fi

# --------------- 模型下载 ---------------
step "4/6  检查 Embedding 模型"

MODEL_DIR="bge-base-zh-v1.5"
if [ ! -d "./${MODEL_DIR}" ] || [ ! -f "./${MODEL_DIR}/config.json" ]; then
    warn "Embedding 模型未下载"
    echo ""
    echo "  ${MODEL_DIR} 目录不存在或为空。"
    echo "  ChromaDB 向量搜索需要此模型。"
    echo ""
    echo "  请选择:"
    echo "    1) 现在下载 (需要外网，约 400MB)"
    echo "    2) 跳过 (将使用 ChromaDB 默认模型)"
    echo "    3) 退出，手动准备模型后重新部署"
    read -rp "  [1/2/3] (默认=1): " model_choice
    model_choice="${model_choice:-1}"
    case "$model_choice" in
        1)
            bash download-model.sh
            ;;
        2)
            warn "跳过模型下载，向量搜索可能不可用"
            ;;
        *)
            info "请先下载模型再重新部署"
            info "  bash download-model.sh"
            exit 0
            ;;
    esac
else
    info "Embedding 模型已就绪: ./${MODEL_DIR}"
fi

# --------------- 构建与启动 ---------------
step "5/6  构建镜像并启动服务"

info "拉取基础镜像..."
docker pull postgres:15-alpine 2>/dev/null || warn "无法拉取 postgres 镜像 (内网请提前导入)"
docker pull nginx:alpine 2>/dev/null || warn "无法拉取 nginx 镜像 (内网请提前导入)"

info "构建应用镜像..."
${COMPOSE_CMD} build --pull

info "启动所有服务..."
${COMPOSE_CMD} up -d

# --------------- 等待就绪 ---------------
step "6/6  等待服务就绪"

info "等待数据库就绪..."
ATTEMPTS=0
MAX_ATTEMPTS=30
until docker exec hongxi-db-1 pg_isready -U hongxi -d hongxi_bid 2>/dev/null; do
    sleep 2
    ATTEMPTS=$((ATTEMPTS + 1))
    if [ $ATTEMPTS -ge $MAX_ATTEMPTS ]; then
        warn "数据库启动超时，请手动检查: ${COMPOSE_CMD} logs db"
        break
    fi
done

info "等待后端就绪..."
sleep 5
if curl -s http://localhost:8888/api/ >/dev/null 2>&1; then
    info "后端 API 可访问"
else
    warn "后端可能尚未就绪，请稍后检查: curl http://localhost:8888/api/"
fi

# --------------- 完成 ---------------
echo ""
echo "============================================"
echo -e " ${GREEN}${APP_NAME} — 部署完成!${NC}"
echo "============================================"
echo ""
echo "  访问地址:  http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'SERVER_IP'):8888"
echo "  查看日志:  ${COMPOSE_CMD} logs -f"
echo "  停止服务:  ${COMPOSE_CMD} down"
echo "  重启服务:  ${COMPOSE_CMD} restart"
echo ""
echo "  常用命令:"
echo "    ${COMPOSE_CMD} ps          查看服务状态"
echo "    ${COMPOSE_CMD} logs -f     跟踪日志输出"
echo "    ${COMPOSE_CMD} restart     重启所有服务"
echo "    ${COMPOSE_CMD} down        停止并移除容器"
echo "    ${COMPOSE_CMD} up -d       后台启动"
echo ""
