# 宏曦标书 — Docker 部署指南

> 适用于 Ubuntu 20.04+ / Debian 11+ 内网服务器部署

---

## 一、环境要求

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| CPU | 4 核 | 8 核 |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 20 GB | 50 GB+ (含模型和 Docker 镜像) |
| 系统 | Ubuntu 20.04 | Ubuntu 22.04 / 24.04 |
| Docker | 24.0+ | 最新稳定版 |

---

## 二、安装 Docker (首次部署)

### 2.1 外网环境 — 在线安装

```bash
# 安装 Docker Engine
curl -fsSL https://get.docker.com | sudo bash

# 将当前用户加入 docker 组 (免 sudo)
sudo usermod -aG docker $USER
newgrp docker

# 验证
docker --version
docker compose version
```

### 2.2 内网环境 — 离线安装

在外网机器上下载安装包：

```bash
# 在外网 Ubuntu 机器上
# 1. 下载 Docker 安装包
curl -fsSL https://download.docker.com/linux/ubuntu/dists/$(lsb_release -cs)/pool/stable/amd64/ -o docker-packages.html

# 2. 建议使用 Docker 官方离线包
wget https://download.docker.com/linux/static/stable/x86_64/docker-26.0.0.tgz

# 3. 下载 Docker Compose 二进制
wget https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64
```

将下载的文件拷贝到内网服务器后：

```bash
# 解压 Docker
tar xzvf docker-26.0.0.tgz
sudo cp docker/* /usr/bin/

# 安装 systemd 服务
sudo cat > /etc/systemd/system/docker.service << 'EOF'
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now docker

# 安装 Docker Compose
sudo cp docker-compose-linux-x86_64 /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

---

## 三、准备 Docker 镜像 (内网必做)

如果内网服务器无法访问 Docker Hub，需要在有网的机器上导出镜像：

```bash
# === 在有外网的机器上执行 ===

# 拉取基础镜像
docker pull postgres:15-alpine
docker pull nginx:alpine
docker pull python:3.12-slim
docker pull node:20-alpine

# 导出镜像
docker save postgres:15-alpine nginx:alpine python:3.12-slim node:20-alpine \
  -o hongxi-base-images.tar

# 压缩 (可选，减小体积)
gzip hongxi-base-images.tar
```

将 `hongxi-base-images.tar.gz` 拷贝到内网服务器后：

```bash
# === 在内网服务器上执行 ===

# 解压并导入
gunzip hongxi-base-images.tar.gz   # 如果压缩过
docker load -i hongxi-base-images.tar

# 验证
docker images
```

---

## 四、准备 Embedding 模型 (内网必做)

向量搜索需要 sentence-transformers 模型，内网无法从 HuggingFace 下载，需要提前准备。

### 4.1 在外网机器下载模型

```bash
# 在有外网的机器上执行
pip3 install sentence-transformers

# 下载推荐模型 (bge-base-zh-v1.5, 约 400MB)
python3 -c "
from sentence_transformers import SentenceTransformer
model = SentenceTransformer('BAAI/bge-base-zh-v1.5')
model.save('./bge-base-zh-v1.5')
"

# 打包
tar czf bge-base-zh-v1.5.tar.gz bge-base-zh-v1.5/
```

### 4.2 在内网服务器解压

```bash
# 将 bge-base-zh-v1.5.tar.gz 拷贝到项目根目录
tar xzf bge-base-zh-v1.5.tar.gz
ls bge-base-zh-v1.5/config.json   # 确认有配置文件
```

---

## 五、部署步骤

### 5.1 上传项目

将整个项目目录拷贝到服务器：

```bash
# 在外网机器上打包 (排除 node_modules 等)
cd /path/to/项目
tar czf hongxi-bid.tar.gz \
  --exclude='node_modules' \
  --exclude='.git' \
  --exclude='frontend/dist' \
  --exclude='backend/chroma_data' \
  --exclude='outputs' \
  --exclude='__pycache__' \
  .

scp hongxi-bid.tar.gz user@server-ip:/opt/
```

在服务器上解压：

```bash
cd /opt
tar xzf hongxi-bid.tar.gz -C hongxi-bid
cd hongxi-bid
```

### 5.2 配置环境变量

```bash
# 创建配置文件
cp .env.example .env

# 编辑配置
vim .env
```

**必填项：**

```ini
# 安全密钥 — 务必修改!
SECRET_KEY=<运行: openssl rand -hex 32>

# AI API 密钥 (根据实际使用的 AI 服务填写)
DEEPSEEK_API_KEY=sk-your-deepseek-key
# 或
OPENAI_API_KEY=sk-your-openai-key
# 或
TONGYI_API_KEY=sk-your-tongyi-key

# 访问地址 (替换为服务器实际 IP)
CORS_ORIGINS=["http://192.168.1.100:8888"]

# 文件存储路径 (Docker 内部路径, 无需修改)
UPLOAD_DIR=/app/uploads
OUTPUT_DIR=/app/outputs
TEMPLATE_DIR=/app/templates
```

### 5.3 一键部署

```bash
chmod +x deploy.sh download-model.sh
./deploy.sh
```

部署脚本会自动：
1. 检查系统环境 (CPU/内存/磁盘)
2. 检查 Docker 环境
3. 创建 .env 配置 (如不存在)
4. 检查/下载 Embedding 模型
5. 构建镜像并启动服务
6. 等待服务就绪

### 5.4 验证部署

```bash
# 检查服务状态
docker compose ps

# 应该看到 4 个服务都是 Up 状态:
#   hongxi-db-1        postgres:15-alpine    Up (healthy)
#   hongxi-backend-1   hongxi-bid-backend    Up
#   hongxi-frontend-1  hongxi-bid-frontend   Up
#   hongxi-nginx-1     nginx:alpine          Up

# 测试 API
curl http://localhost:8888/api/

# 在浏览器访问
# http://<服务器IP>:8888
```

---

## 六、服务架构

```
┌──────────────────────────────────────────────────┐
│                   Nginx (:8888)                   │
│                  反向代理 + 静态文件               │
└──────┬───────────────────────┬───────────────────┘
       │                       │
       │ /api/*  /uploads/*    │ /*
       ▼                       ▼
┌──────────────┐       ┌──────────────────┐
│   Backend    │       │    Frontend       │
│  FastAPI     │       │  React (build)    │
│  :8000       │       │  nginx :80        │
└──────┬───────┘       └──────────────────┘
       │
       ▼
┌──────────────┐
│  PostgreSQL  │
│  :5432       │
└──────────────┘
```

**四个容器：**
- `nginx` — 入口反向代理，端口 8888
- `frontend` — React 前端静态文件
- `backend` — FastAPI 后端 API
- `db` — PostgreSQL 15 数据库

---

## 七、常用运维命令

```bash
cd /opt/hongxi-bid

# 查看运行状态
docker compose ps

# 查看实时日志
docker compose logs -f

# 查看特定服务的日志
docker compose logs -f backend
docker compose logs -f db

# 重启所有服务
docker compose restart

# 重启单个服务
docker compose restart backend

# 停止所有服务
docker compose down

# 完全删除 (包括数据卷 — 慎用!)
docker compose down -v

# 更新代码后重新构建
git pull
docker compose build backend frontend
docker compose up -d

# 数据库备份
docker exec hongxi-db-1 pg_dump -U hongxi hongxi_bid > backup_$(date +%Y%m%d).sql

# 数据库恢复
docker exec -i hongxi-db-1 psql -U hongxi hongxi_bid < backup_20260101.sql
```

---

## 八、数据备份

### 数据库

```bash
# 每日备份脚本 (添加到 crontab)
# 0 3 * * * cd /opt/hongxi-bid && docker exec hongxi-db-1 pg_dump -U hongxi hongxi_bid | gzip > backups/db_$(date +\%Y\%m\%d).sql.gz

mkdir -p /opt/hongxi-bid/backups
```

### 文件数据

Docker 数据卷位置：
- `pgdata` — PostgreSQL 数据库文件
- `uploads` — 用户上传文件
- `outputs` — 生成的标书文件

```bash
# 查看数据卷
docker volume ls | grep hongxi

# 备份数据卷
docker run --rm -v hongxi-bid_pgdata:/data -v $(pwd)/backups:/backup alpine tar czf /backup/pgdata_$(date +%Y%m%d).tar.gz -C /data .
```

---

## 九、升级更新

```bash
cd /opt/hongxi-bid

# 拉取最新代码
git pull

# 重新构建并部署
docker compose build backend frontend
docker compose up -d

# 清理旧镜像
docker image prune -f
```

---

## 十、故障排查

### 服务无法启动

```bash
# 查看所有容器状态
docker compose ps -a

# 查看详细日志
docker compose logs --tail=100

# 检查端口占用
ss -tlnp | grep 8888
```

### 数据库连接失败

```bash
# 检查数据库是否健康
docker exec hongxi-db-1 pg_isready -U hongxi -d hongxi_bid

# 进入数据库容器排查
docker exec -it hongxi-db-1 psql -U hongxi -d hongxi_bid
```

### 向量搜索不工作

```bash
# 检查 Embedding 模型是否正确挂载
docker exec hongxi-backend-1 ls -la /app/bge-base-zh-v1.5/

# 查看后端日志中的模型加载信息
docker compose logs backend | grep -i "embedding\|chroma\|model"
```

### 内网 pip/npm 安装失败

如果构建镜像时 pip install 失败，需要配置国内镜像源。

编辑 `backend/Dockerfile`，在 `RUN pip install` 前添加：

```dockerfile
# 使用阿里云 PyPI 镜像
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
```

编辑 `frontend/Dockerfile`，在 `RUN npm ci` 前添加：

```dockerfile
# 使用淘宝 npm 镜像
RUN npm config set registry https://registry.npmmirror.com
```

### 完全重置

```bash
# 停止并删除所有容器和数据
docker compose down -v

# 删除镜像
docker rmi hongxi-bid-backend hongxi-bid-frontend

# 重新部署
./deploy.sh
```

---

## 十一、端口与防火墙

默认使用端口 **8888**。如需要修改：

1. 编辑 `docker-compose.yml` 中的 `ports` 映射
2. 编辑 `nginx.conf` 中的 `listen` 端口
3. 更新 `.env` 中的 `CORS_ORIGINS`

```bash
# 开放防火墙端口
sudo ufw allow 8888/tcp
```

---

## 十二、内网部署清单

| 步骤 | 文件/操作 | 大小 |
|------|----------|------|
| 1. 安装 Docker | docker-26.0.0.tgz | ~70MB |
| 2. Docker Compose | docker-compose-linux-x86_64 | ~60MB |
| 3. 基础镜像 | postgres:15-alpine, nginx:alpine, python:3.12-slim, node:20-alpine | ~1.2GB |
| 4. Embedding 模型 | bge-base-zh-v1.5.tar.gz | ~400MB |
| 5. 项目代码 | hongxi-bid.tar.gz | ~5MB |
| **总计** | | **约 1.8GB** |
