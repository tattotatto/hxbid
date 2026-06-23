"""Fix CompanyInfo.tsx image URL on server and rebuild frontend."""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.50.157', username='johnwoo', password='admin4wwj', timeout=10)

sftp = ssh.open_sftp()
with sftp.open('/opt/hxbid/frontend/src/pages/resources/CompanyInfo.tsx', 'r') as f:
    content = f.read().decode('utf-8')

# The old wrong pattern uses /api/v1/uploads/ with .split('/').pop()
# The new correct pattern uses /uploads/ with .replace(...)
old = "api/v1/uploads/${value.split('/').pop()}"
new = "uploads/${value.replace(/^uploads[\\/]/, '')}"

print("Old found:", old in content)
content = content.replace(old, new)
print("Old remaining:", old in content)

with sftp.open('/opt/hxbid/frontend/src/pages/resources/CompanyInfo.tsx', 'w') as f:
    f.write(content.encode('utf-8'))
sftp.close()

# Rebuild
print("Building frontend (no-cache)...")
stdin, stdout, stderr = ssh.exec_command(
    'cd /opt/hxbid && docker compose build --no-cache frontend 2>&1'
)
time.sleep(90)
out = stdout.read().decode()
for line in out.strip().split('\n')[-5:]:
    print(line)

# Restart
ssh.exec_command('cd /opt/hxbid && docker compose up -d --force-recreate frontend nginx 2>&1')
time.sleep(5)

# Verify
stdin, stdout, stderr = ssh.exec_command(
    'docker exec hxbid-frontend-1 grep -c "api/v1/uploads" /usr/share/nginx/html/assets/*.js 2>&1'
)
print("Old URL in build:", stdout.read().decode().strip())

stdin, stdout, stderr = ssh.exec_command(
    'docker exec hxbid-frontend-1 grep -c "value.replace" /usr/share/nginx/html/assets/*.js 2>&1'
)
print("New URL in build:", stdout.read().decode().strip())

ssh.close()
print("Done")
