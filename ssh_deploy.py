"""SSH 远程部署脚本 — 宏曦标书 Ubuntu 服务器一键部署"""
import paramiko
import sys
import os
import glob as glob_module
import json

HOST = "192.168.50.38"
USER = "hxbid"
PASSWORD = "hx123456"
LOCAL_PROJECT = os.path.dirname(os.path.abspath(__file__))
REMOTE_PROJECT = "/hxbid/hongxi-bid"


def make_client():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    return c


def ssh(c, cmd, timeout=60):
    """Run a command ON the remote server (non-sudo)."""
    print(f"  $ {cmd[:120]}")
    stdin, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out.strip():
        for line in out.strip().split('\n')[-10:]:
            print(f"    {line}")
    if err.strip():
        # skip common noise
        clean = [l for l in err.strip().split('\n') if 'sudo' not in l.lower()]
        if clean:
            print(f"  ERR: {clean[0][:120]}")
    return out, err


def sudo(c, cmd, timeout=60):
    """Run a command with sudo (uses password)."""
    safe = cmd.replace('"', '\\"')
    full = f"echo '{PASSWORD}' | sudo -S bash -c \"{safe}\""
    print(f"  # {cmd[:120]}")
    stdin, stdout, stderr = c.exec_command(full, timeout=timeout)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out.strip():
        for line in out.strip().split('\n')[-10:]:
            print(f"    {line}")
    if err.strip():
        clean = [l for l in err.strip().split('\n')
                 if 'sudo' not in l.lower() and 'password' not in l.lower()]
        if clean:
            print(f"  ERR: {clean[0][:120]}")
    return out, err


def upload_files(client):
    """Upload project files to server via SFTP."""
    sftp = client.open_sftp()

    # Files to upload (local_rel_path -> remote_abs_path)
    # We upload the key config files first, then directories
    files_to_upload = [
        "docker-compose.yml",
        "nginx.conf",
        ".env.example",
        "download-model.sh",
        "deploy.sh",
    ]

    print("\n上传项目文件...")
    for f in files_to_upload:
        local = os.path.join(LOCAL_PROJECT, f)
        remote = f"{REMOTE_PROJECT}/{f}"
        if os.path.exists(local):
            try:
                sftp.put(local, remote)
                print(f"  OK: {f}")
            except Exception as e:
                print(f"  FAIL: {f} - {e}")

    # Upload backend (excluding __pycache__, chroma_data)
    print("上传 backend/ ...")
    backend_local = os.path.join(LOCAL_PROJECT, "backend")
    backend_remote = f"{REMOTE_PROJECT}/backend"

    for root, dirs, files in os.walk(backend_local):
        # Skip unwanted dirs
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'chroma_data', 'venv', '.venv')]
        for fname in files:
            if fname.endswith('.pyc') or fname.endswith('.pyo'):
                continue
            local_path = os.path.join(root, fname)
            rel_path = os.path.relpath(local_path, backend_local)
            remote_path = f"{backend_remote}/{rel_path}".replace('\\', '/')

            # Ensure remote dir exists
            remote_dir = os.path.dirname(remote_path).replace('\\', '/')
            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                # Create dir recursively
                parts = remote_dir.split('/')
                for i in range(1, len(parts) + 1):
                    partial = '/'.join(parts[:i]) or '/'
                    try:
                        sftp.stat(partial)
                    except FileNotFoundError:
                        sftp.mkdir(partial)

            try:
                sftp.put(local_path, remote_path)
            except Exception as e:
                print(f"  FAIL: {rel_path} - {e}")

    print("  backend/ 上传完成")

    # Upload frontend (excluding node_modules, dist)
    print("上传 frontend/ ...")
    frontend_local = os.path.join(LOCAL_PROJECT, "frontend")
    frontend_remote = f"{REMOTE_PROJECT}/frontend"

    if os.path.exists(frontend_local):
        for root, dirs, files in os.walk(frontend_local):
            dirs[:] = [d for d in dirs if d not in ('node_modules', 'dist', '.git')]
            for fname in files:
                local_path = os.path.join(root, fname)
                rel_path = os.path.relpath(local_path, frontend_local)
                remote_path = f"{frontend_remote}/{rel_path}".replace('\\', '/')

                remote_dir = os.path.dirname(remote_path).replace('\\', '/')
                try:
                    sftp.stat(remote_dir)
                except FileNotFoundError:
                    parts = remote_dir.split('/')
                    for i in range(1, len(parts) + 1):
                        partial = '/'.join(parts[:i]) or '/'
                        try:
                            sftp.stat(partial)
                        except FileNotFoundError:
                            sftp.mkdir(partial)

                try:
                    sftp.put(local_path, remote_path)
                except Exception as e:
                    print(f"  FAIL: {rel_path} - {e}")
        print("  frontend/ 上传完成")

    sftp.close()


def main():
    print("=" * 60)
    print("宏曦标书 — 远程部署")
    print(f"服务器: {HOST}")
    print(f"项目路径: {REMOTE_PROJECT}")
    print("=" * 60)

    # ====== Connect ======
    print("\n>>> 连接服务器...")
    client = make_client()
    print("已连接!")

    # ====== Step 1: Configure Docker ======
    print("\n" + "=" * 60)
    print("1/6 配置 Docker data-root -> /hxbid/docker")

    sudo(client, "systemctl stop docker docker.socket 2>/dev/null; echo 'stopped'")
    sudo(client, "mkdir -p /hxbid/docker")
    sudo(client, """cat > /etc/docker/daemon.json << 'EOF'
{
    "data-root": "/hxbid/docker",
    "storage-driver": "overlay2",
    "log-driver": "json-file",
    "log-opts": {
        "max-size": "100m",
        "max-file": "3"
    },
    "registry-mirrors": [
        "https://docker.1ms.run",
        "https://docker.xuanyuan.me"
    ]
}
EOF""")
    sudo(client, "cat /etc/docker/daemon.json")
    sudo(client, "systemctl start docker && systemctl enable docker", timeout=60)
    sudo(client, "docker info 2>/dev/null | grep -E 'Docker Root|Server Version'")

    # ====== Step 2: Create directories ======
    print("\n" + "=" * 60)
    print("2/6 创建数据目录 (/hxbid/)")

    sudo(client, "mkdir -p /hxbid/pgdata /hxbid/uploads /hxbid/outputs /hxbid/chroma_data")
    sudo(client, "chown -R hxbid:hxbid /hxbid/pgdata /hxbid/uploads /hxbid/outputs /hxbid/chroma_data")
    ssh(client, "ls -la /hxbid/")

    # ====== Step 3: Upload project ======
    print("\n" + "=" * 60)
    print("3/6 上传项目文件")

    try:
        upload_files(client)
        print("文件上传完成!")
    except Exception as e:
        print(f"上传出错: {e}")
        print("将尝试继续...")

    # ====== Step 4: Setup .env ======
    print("\n" + "=" * 60)
    print("4/6 配置环境变量")
    ssh(client, f"cd {REMOTE_PROJECT} && cp .env.example .env 2>/dev/null; ls -la .env")
    sudo(client, f"chown hxbid:hxbid {REMOTE_PROJECT}/.env")

    # ====== Step 5: Pull images & build ======
    print("\n" + "=" * 60)
    print("5/6 拉取基础镜像并构建")

    for img in ["postgres:15-alpine", "nginx:alpine", "python:3.12-slim", "node:20-alpine"]:
        print(f"\n拉取: {img}")
        sudo(client, f"docker pull {img}", timeout=300)

    sudo(client, "docker images")

    # ====== Step 6: Build and start ======
    print("\n" + "=" * 60)
    print("6/6 构建并启动应用")

    # Need to fix the docker-compose.yml volumes to use /hxbid paths
    # The current docker-compose.yml uses named volumes and relative paths
    # We need to adjust for the /hxbid mount

    print("""
======================================================================
Docker 环境已配置完毕!

请手动执行以下步骤完成部署:
======================================================================

1. 检查上传的文件:
   ls -la /hxbid/hongxi-bid/

2. 编辑 .env 配置文件:
   vim /hxbid/hongxi-bid/.env
   必填:
   - SECRET_KEY (openssl rand -hex 32)
   - DEEPSEEK_API_KEY
   - CORS_ORIGINS=["http://192.168.50.38:8888"]

3. 解压 Embedding 模型 (如果已上传):
   cd /hxbid
   tar xzf bge-base-zh-v1.5.tar.gz

4. 构建并启动:
   cd /hxbid/hongxi-bid
   sudo docker compose build
   sudo docker compose up -d

5. 查看状态:
   sudo docker compose ps
   sudo docker compose logs -f
======================================================================
""")

    client.close()


if __name__ == "__main__":
    main()
