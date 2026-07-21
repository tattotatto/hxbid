"""Check generation logs and debug the issue."""
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

# Check for any Python errors in container logs
print("=" * 60)
print("1. 查找 ERROR / exception / traceback 日志")
out, _ = sudo("docker logs hongxi-backend 2>&1 | grep -i -E 'error|exception|traceback|fail|subsection|outline|generating' | tail -40")
if out.strip():
    print(out)
else:
    print("  (没有找到相关日志)")

print("\n2. 查找 AI pipeline 日志 (generate/deep/outline)")
out, _ = sudo("docker logs hongxi-backend 2>&1 | grep -i -E 'pipeline|outline|Deep|leaf|section|generating' | tail -30")
if out.strip():
    print(out)
else:
    print("  (没有找到相关日志)")

# Check the actual api pipeline file inside container
print("\n3. 验证容器内文件是否有我们的修改")
out, _ = sudo("docker exec hongxi-backend head -15 /app/services/ai_pipeline.py")
print(out)

out, _ = sudo("docker exec hongxi-backend grep -n 'create_task\|progress_queue\|on_section_progress' /app/services/ai_pipeline.py")
print(out)

c.close()
