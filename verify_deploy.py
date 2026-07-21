"""Verify deployment and check backend logs."""
import paramiko

HOST = "192.168.50.38"
USER = "hxbid"
PWD = "hx123456"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=15)

def sudo(cmd, timeout=30):
    safe = cmd.replace('"', '\\"')
    full = f"echo '{PWD}' | sudo -S bash -c \"{safe}\""
    stdin, stdout, stderr = c.exec_command(full, timeout=timeout)
    return stdout.read().decode(errors="replace"), stderr.read().decode(errors="replace")

# 1. Check imports in container
print("=" * 50)
print("1. Test imports in container")
out, err = sudo("docker exec hongxi-backend python3 -c 'import queue, concurrent.futures; print(\"OK\")'")
print(out.strip())
if err.strip():
    print(err.strip()[:200])

# 2. Check recent logs
print("\n" + "=" * 50)
print("2. Backend logs (last 15 lines)")
out, _ = sudo("docker logs hongxi-backend --tail 15 2>&1")
for line in out.strip().split('\n'):
    print(f"  {line.strip()}")

# 3. Verify the file was updated
print("\n" + "=" * 50)
print("3. Verify ThreadPoolExecutor exists in file")
out, _ = sudo("grep -c 'ThreadPoolExecutor' /hxbid/hongxi-bid/backend/app/services/ai_pipeline.py")
print(f"  ThreadPoolExecutor count: {out.strip()}")

out, _ = sudo("grep -c 'queue.Queue' /hxbid/hongxi-bid/backend/app/services/ai_pipeline.py")
print(f"  queue.Queue count: {out.strip()}")

c.close()
