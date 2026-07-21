"""Quick deploy one file to server — no full rebuild needed."""
import paramiko
import os
import sys

HOST = "192.168.50.38"
USER = "hxbid"
PWD = "hx123456"
PROJ = "/hxbid/hongxi-bid"
LOCAL = os.path.dirname(os.path.abspath(__file__))

# Only the file we changed
FILES = [
    "backend/app/services/ai_pipeline.py",
]

print("=" * 60)
print("宏曦标书 — 快速部署 (文件替换)")
print(f"服务器: {HOST}  用户: {USER}")
print("=" * 60)

# Connect
print("\n>>> 连接服务器...")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=15)
print("已连接!")

# Upload files
print("\n>>> 上传文件...")
sftp = c.open_sftp()
for f in FILES:
    local = os.path.join(LOCAL, f)
    if not os.path.exists(local):
        print(f"  SKIP (本地不存在): {f}")
        continue
    remote = f"{PROJ}/{f}".replace("\\", "/")
    remote_dir = os.path.dirname(remote).replace("\\", "/")
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

# Copy into running container and restart
print("\n>>> 更新运行中的容器...")

def sudo_cmd(cmd, timeout=60):
    """Run a command with sudo."""
    safe = cmd.replace('"', '\\"')
    full = f"echo '{PWD}' | sudo -S bash -c \"{safe}\""
    stdin, stdout, stderr = c.exec_command(full, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out.strip():
        for line in out.strip().split("\n")[-10:]:
            print(f"    {line}")
    if err.strip():
        clean = [l for l in err.strip().split("\n")
                 if "sudo" not in l.lower() and "password" not in l.lower()]
        if clean:
            print(f"    ERR: {clean[0][:200]}")

# Copy ai_pipeline.py into the container
for f in FILES:
    remote_path = f"{PROJ}/{f}"
    container_path = f"/app/services/{os.path.basename(f)}"
    sudo_cmd(f"docker cp {remote_path} hongxi-backend:{container_path}")
    print(f"  Copied {f} -> hongxi-backend:{container_path}")

# Restart backend
print("\n>>> 重启后端服务...")
sudo_cmd(f"cd {PROJ} && docker compose restart backend", timeout=60)

# Wait and check status
import time
print("\n>>> 等待服务就绪...")
time.sleep(5)

sudo_cmd("docker compose -f /hxbid/hongxi-bid/docker-compose.yml ps")

# Quick health check
stdin, stdout, stderr = c.exec_command(
    "curl -s -o /dev/null -w '%{http_code}' http://localhost:8888/api/"
)
code = stdout.read().decode(errors="replace").strip()
print(f"\n>>> 健康检查: HTTP {code}")

c.close()
print("\n" + "=" * 60)
print("部署完成! 访问: http://192.168.50.38:8888")
print("=" * 60)
