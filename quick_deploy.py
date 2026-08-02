"""Quick deploy changed files to server — copy backend, rebuild frontend."""
import paramiko
import os
import sys
import time

HOST = "192.168.50.38"
USER = "hxbid"
PWD = "hx123456"
PROJ = "/hxbid/hongxi-bid"
LOCAL = os.path.dirname(os.path.abspath(__file__))

# All backend files changed since last full Docker image build
BACKEND_FILES = [
    # Alembic migrations
    "backend/alembic/versions/20260802_0004_add_chapter_structure_columns.py",
    # API
    "backend/app/api/bid.py",
    "backend/app/api/chapters.py",
    "backend/app/api/router.py",
    "backend/app/api/collection.py",
    # Models
    "backend/app/models/project.py",
    # Schemas
    "backend/app/schemas/project.py",
    # Services
    "backend/app/services/ai_pipeline.py",
    "backend/app/services/chapter_chat.py",
    "backend/app/services/chapter_extractor.py",
    "backend/app/services/content_assembler.py",
    "backend/app/services/outline_engine.py",
    "backend/app/services/render_engine.py",
    "backend/app/services/section_editor.py",
    "backend/app/services/template_filler.py",
    "backend/app/services/title_refiner.py",
]

FRONTEND_FILES = [
    "frontend/src/pages/project/ProjectWorkflow.tsx",
    "frontend/src/components/TreeEditor/TreeEditor.tsx",
    "frontend/src/components/TreeEditor/TreePanel.tsx",
    "frontend/src/components/TreeEditor/EditPanel.tsx",
]

print("=" * 60)
print("宏曦标书 — 快速部署 (含业绩合同选择功能)")
print(f"服务器: {HOST}  用户: {USER}")
print(f"后端文件: {len(BACKEND_FILES)}  前端文件: {len(FRONTEND_FILES)}")
print("=" * 60)

# ── Connect ──
print("\n>>> 连接服务器...")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=15)
print("已连接!")


def sudo_cmd(cmd, timeout=120):
    """Run a command with sudo."""
    safe = cmd.replace('"', '\\"')
    full = f"echo '{PWD}' | sudo -S bash -c \"{safe}\""
    stdin, stdout, stderr = c.exec_command(full, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    for line in out.strip().split("\n")[-10:]:
        if line.strip():
            print(f"    {line.strip()}")
    err_clean = [l for l in err.strip().split("\n")
                 if "sudo" not in l.lower() and "password" not in l.lower()]
    if err_clean:
        for line in err_clean[:5]:
            print(f"    ERR: {line[:200]}")


# ── Step 1: Upload all files ──
print("\n>>> 上传文件到服务器...")
sftp = c.open_sftp()
all_files = BACKEND_FILES + FRONTEND_FILES
for f in all_files:
    local = os.path.join(LOCAL, f)
    if not os.path.exists(local):
        print(f"  SKIP (本地不存在): {f}")
        continue
    remote = f"{PROJ}/{f}".replace("\\", "/")
    remote_dir = os.path.dirname(remote).replace("\\", "/")
    # Ensure remote dir exists
    try:
        sftp.stat(remote_dir)
    except FileNotFoundError:
        parts = remote_dir.split("/")
        for i in range(1, len(parts) + 1):
            partial = "/".join(parts[:i]) or "/"
            try:
                sftp.stat(partial)
            except FileNotFoundError:
                sftp.mkdir(partial)
    try:
        sftp.put(local, remote)
        print(f"  OK: {f}")
    except Exception as e:
        print(f"  FAIL: {f} - {e}")
sftp.close()

# ── Step 2: Copy backend files into container ──
print("\n>>> 更新后端容器 (docker cp)...")
CONTAINER_MAP = {
    "backend/app/api/bid.py": "/app/app/api/bid.py",
    "backend/app/api/chapters.py": "/app/app/api/chapters.py",
    "backend/app/api/router.py": "/app/app/api/router.py",
    "backend/app/api/collection.py": "/app/app/api/collection.py",
    "backend/app/models/project.py": "/app/app/models/project.py",
    "backend/app/schemas/project.py": "/app/app/schemas/project.py",
    "backend/app/services/ai_pipeline.py": "/app/app/services/ai_pipeline.py",
    "backend/app/services/chapter_chat.py": "/app/app/services/chapter_chat.py",
    "backend/app/services/chapter_extractor.py": "/app/app/services/chapter_extractor.py",
    "backend/app/services/content_assembler.py": "/app/app/services/content_assembler.py",
    "backend/app/services/outline_engine.py": "/app/app/services/outline_engine.py",
    "backend/app/services/render_engine.py": "/app/app/services/render_engine.py",
    "backend/app/services/section_editor.py": "/app/app/services/section_editor.py",
    "backend/app/services/template_filler.py": "/app/app/services/template_filler.py",
    "backend/app/services/title_refiner.py": "/app/app/services/title_refiner.py",
}
for f in BACKEND_FILES:
    remote_path = f"{PROJ}/{f}"
    if f not in CONTAINER_MAP:
        continue  # skip migration files, handled separately
    container_path = CONTAINER_MAP[f]
    # Ensure parent dir exists in container
    sudo_cmd(f"docker exec hongxi-backend mkdir -p {os.path.dirname(container_path)}")
    sudo_cmd(f"docker cp {remote_path} hongxi-backend:{container_path}")
    print(f"  Copied {f} -> hongxi-backend:{container_path}")

# ── Step 3: Run DB migration ──
print("\n>>> 运行数据库迁移...")
sudo_cmd("docker exec hongxi-backend alembic -c /app/alembic.ini upgrade head", timeout=30)

# ── Step 4: Restart backend ──
print("\n>>> 重启后端服务...")
sudo_cmd(f"cd {PROJ} && docker compose restart backend", timeout=60)

# ── Step 5: Rebuild frontend ──
if FRONTEND_FILES:
    print("\n>>> 重新构建前端镜像 (这需要 1-3 分钟)...")
    sudo_cmd(f"cd {PROJ} && docker compose build frontend", timeout=300)
    print("\n>>> 重新创建前端容器...")
    sudo_cmd(f"cd {PROJ} && docker compose up -d --no-deps frontend", timeout=60)

# ── Step 6: Wait & verify ──
print("\n>>> 等待服务就绪...")
time.sleep(8)

# Check container status
print("\n>>> 容器状态:")
sudo_cmd("docker compose -f /hxbid/hongxi-bid/docker-compose.yml ps")

# Health check
stdin, stdout, stderr = c.exec_command(
    "curl -s -o /dev/null -w '%{http_code}' http://localhost:8888/docs"
)
code = stdout.read().decode(errors="replace").strip()
print(f"\n>>> 健康检查: HTTP {code}")

# Show recent backend logs
print("\n>>> 后端最近日志:")
stdin2, stdout2, stderr2 = c.exec_command(
    f"echo '{PWD}' | sudo -S docker logs hongxi-backend --tail 8 2>&1"
)
log_out = stdout2.read().decode(errors="replace")
for line in log_out.strip().split("\n"):
    print(f"  {line.strip()[:200]}")

c.close()
print("\n" + "=" * 60)
print("部署完成! 访问: http://192.168.50.38:8888")
print("=" * 60)
