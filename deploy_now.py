"""Deploy latest code to server — double-click or python deploy_now.py"""
import paramiko, os, sys

HOST = "192.168.50.38"
USER = "hxbid"
PWD = "hx123456"
PROJ = "/hxbid/hongxi-bid"
LOCAL = os.path.dirname(os.path.abspath(__file__))

# All files changed since server's current commit (c330342 → HEAD + unstaged changes)
FILES = [
    # Personnel (latest feature)
    "frontend/src/pages/resources/Personnel.tsx",
    "backend/app/api/personnel.py",
    "backend/app/models/personnel.py",
    "backend/app/schemas/personnel.py",
    # User management
    "backend/app/api/admin.py",
    "backend/app/schemas/user.py",
    # Contracts (history contracts)
    "backend/app/models/contract.py",
    "backend/app/schemas/contract.py",
    "backend/app/api/contracts.py",
    "frontend/src/pages/resources/Contracts.tsx",
    # Company info
    "backend/app/api/company.py",
    "backend/app/models/company.py",
    "backend/app/schemas/company.py",
    # AI / collection
    "backend/app/services/ai_pipeline.py",
    "backend/app/services/collection.py",
    "backend/app/api/collection.py",
    "backend/app/schemas/collection.py",
    "backend/app/models/project.py",
    "backend/app/models/project_resource.py",
    "backend/app/config.py",
    "backend/app/services/ai_adapter.py",
    # Frontend pages
    "frontend/src/pages/resources/HistoryBids.tsx",
    "frontend/src/pages/admin/UserManagement.tsx",
    "frontend/src/pages/project/CollectionStep.tsx",
    "frontend/src/pages/project/ProjectWorkflow.tsx",
    "frontend/src/pages/project/QualificationPickerModal.tsx",
    "frontend/src/pages/project/PersonnelPickerModal.tsx",
    "frontend/src/pages/project/QuickPersonnelForm.tsx",
    "frontend/src/pages/project/QuickQualificationUpload.tsx",
    "frontend/src/App.tsx",
    "frontend/src/components/Layout.tsx",
    "frontend/src/pages/settings/Settings.tsx",
    # Docker / build config
    "docker-compose.yml",
    "backend/Dockerfile",
    "frontend/Dockerfile",
    "backend/entrypoint.sh",
    # Other
    ".env.example",
    "backend/app/models/__init__.py",
    "backend/app/api/router.py",
    "backend/requirements.txt",
    "upload_model.py",
]

print("=" * 60)
print("宏曦标书 — 部署到服务器")
print("Connecting to", HOST)
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=15)
print("Connected!")

# Upload all changed files
print("\n--- Uploading files ---")
sftp = c.open_sftp()
ok_count = 0
fail_count = 0
for f in FILES:
    local = os.path.join(LOCAL, f)
    if not os.path.exists(local):
        print(f"  SKIP (本地不存在): {f}")
        continue
    remote = f"{PROJ}/{f}".replace("\\", "/")
    # Ensure parent dir exists
    remote_dir = os.path.dirname(remote).replace("\\", "/")
    try:
        sftp.stat(remote_dir)
    except FileNotFoundError:
        parts = remote_dir.split("/")
        for i in range(1, len(parts)+1):
            partial = "/".join(parts[:i]) or "/"
            try: sftp.stat(partial)
            except FileNotFoundError: sftp.mkdir(partial)
    try:
        sftp.put(local, remote)
        ok_count += 1
    except Exception as e:
        print(f"  FAIL: {f} - {e}")
        fail_count += 1
sftp.close()
print(f"Upload done! {ok_count} OK, {fail_count} FAIL")

# Rebuild and restart
print("\n--- Rebuilding backend (this takes a few minutes) ---")
cmd = f"cd {PROJ} && docker compose build --no-cache backend 2>&1"
full = f"echo '{PWD}' | sudo -S bash -c \"{cmd.replace(chr(34), chr(92)+chr(34))}\""
stdin, stdout, stderr = c.exec_command(full, timeout=600)
out = stdout.read().decode(errors="replace")
err = stderr.read().decode(errors="replace")
for l in out.strip().split("\n")[-15:]:
    print("  " + l.strip())
if err.strip():
    for l in err.strip().split("\n")[-5:]:
        if l.strip():
            print("  ERR: " + l.strip()[:200])

print("\n--- Rebuilding frontend ---")
cmd = f"cd {PROJ} && docker compose build --no-cache frontend 2>&1"
full = f"echo '{PWD}' | sudo -S bash -c \"{cmd.replace(chr(34), chr(92)+chr(34))}\""
stdin, stdout, stderr = c.exec_command(full, timeout=300)
out = stdout.read().decode(errors="replace")
for l in out.strip().split("\n")[-10:]:
    print("  " + l.strip())

print("\n--- Restarting all services ---")
cmd = f"cd {PROJ} && docker compose up -d && docker compose ps"
full = f"echo '{PWD}' | sudo -S bash -c \"{cmd.replace(chr(34), chr(92)+chr(34))}\""
stdin, stdout, stderr = c.exec_command(full, timeout=60)
out = stdout.read().decode(errors="replace")
print(out)

c.close()
print("\n" + "=" * 60)
print("Deploy complete! Visit http://192.168.50.38:8888")
print("=" * 60)
