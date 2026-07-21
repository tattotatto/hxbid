"""上传项目文件到服务器"""
import paramiko
import os

HOST = "192.168.50.38"
USER = "hxbid"
PASSWORD = "hx123456"
LOCAL = r"D:\2026\投标软件"
REMOTE = "/hxbid/hongxi-bid"

SKIP_DIRS = {"__pycache__", "chroma_data", ".git", "venv", ".venv", ".mypy_cache", "node_modules", "dist"}
SKIP_EXTS = {".pyc", ".pyo"}


def ensure_remote_dir(sftp, path):
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    cur = ""
    for p in parts:
        cur = cur + "/" + p
        try:
            sftp.stat(cur)
        except:
            try:
                sftp.mkdir(cur)
            except:
                pass


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWORD, timeout=15)
    sftp = c.open_sftp()

    # --- Root config files ---
    root_files = ["docker-compose.yml", "nginx.conf", ".env.example", "download-model.sh"]
    for f in root_files:
        local = os.path.join(LOCAL, f)
        remote = REMOTE + "/" + f
        if os.path.exists(local):
            try:
                sftp.put(local, remote)
                print(f"OK: {f}")
            except Exception as e:
                print(f"FAIL: {f} - {e}")
        else:
            print(f"MISS: {f}")

    # --- backend/ ---
    print("Uploading backend/ ...")
    backend_root = os.path.join(LOCAL, "backend")
    count = 0
    for dirpath, dirnames, filenames in os.walk(backend_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if any(fn.endswith(ext) for ext in SKIP_EXTS):
                continue
            local_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(local_path, backend_root).replace("\\", "/")
            remote_path = REMOTE + "/backend/" + rel
            remote_dir = os.path.dirname(remote_path).replace("\\", "/")
            ensure_remote_dir(sftp, remote_dir)
            try:
                sftp.put(local_path, remote_path)
                count += 1
            except Exception as e:
                print(f"FAIL: {rel} - {e}")
    print(f"  {count} files uploaded")

    # --- frontend/ ---
    print("Uploading frontend/ ...")
    frontend_root = os.path.join(LOCAL, "frontend")
    count = 0
    for dirpath, dirnames, filenames in os.walk(frontend_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            local_path = os.path.join(dirpath, fn)
            rel = os.path.relpath(local_path, frontend_root).replace("\\", "/")
            remote_path = REMOTE + "/frontend/" + rel
            remote_dir = os.path.dirname(remote_path).replace("\\", "/")
            ensure_remote_dir(sftp, remote_dir)
            try:
                sftp.put(local_path, remote_path)
                count += 1
            except Exception as e:
                print(f"FAIL: {rel} - {e}")
    print(f"  {count} files uploaded")

    sftp.close()
    c.close()
    print("DONE!")


if __name__ == "__main__":
    main()
