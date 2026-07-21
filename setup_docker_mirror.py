"""配置 Docker 镜像源"""
import paramiko
import json

HOST = "192.168.50.38"
USER = "hxbid"
PASSWORD = "hx123456"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASSWORD, timeout=15)

# Create daemon.json content
daemon_config = {
    "data-root": "/hxbid/docker",
    "storage-driver": "overlay2",
    "log-driver": "json-file",
    "log-opts": {
        "max-size": "100m",
        "max-file": "3"
    },
    "registry-mirrors": [
        "https://docker.1ms.run",
        "https://docker.xuanyuan.me",
        "https://docker.m.daocloud.io"
    ]
}

json_content = json.dumps(daemon_config, indent=2, ensure_ascii=False)
print("New daemon.json:")
print(json_content)

# Upload via SFTP
sftp = c.open_sftp()
# Write to a temp file first, then move with sudo
tmp_path = "/tmp/daemon.json"
with sftp.file(tmp_path, 'w') as f:
    f.write(json_content)
sftp.close()

# Move to /etc/docker/ with sudo and restart
cmd = "echo 'hx123456' | sudo -S bash -c 'cp /tmp/daemon.json /etc/docker/daemon.json && systemctl stop docker docker.socket && systemctl start docker && systemctl enable docker && echo RESTART_OK'"
stdin, stdout, stderr = c.exec_command(cmd, timeout=30)
out = stdout.read().decode(errors="replace")
err = stderr.read().decode(errors="replace")
print("Restart:", out.strip())
if err:
    print("Err:", err.strip())

# Verify
stdin, stdout, stderr = c.exec_command("docker info 2>/dev/null | grep -A5 'Registry Mirrors'")
print("\nRegistry config:")
print(stdout.read().decode(errors="replace"))

print("\nDone!")
c.close()
