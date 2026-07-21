"""Upload BGE model to 192.168.50.38 via SFTP."""
import paramiko, os, sys

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.50.38', username='hxbid', password='hx123456', timeout=15)

sftp = ssh.open_sftp()
local_dir = r'D:\bge-base-zh-v1.5'
remote_dir = '/hxbid/bge-base-zh-v1.5'

# Create remote dir
stdin, stdout, stderr = ssh.exec_command(f'echo hx123456 | sudo -S mkdir -p {remote_dir} && sudo chown -R hxbid:hxbid {remote_dir} && sudo chmod -R 755 {remote_dir}')
stdout.read()

uploaded = 0
for root, dirs, files in os.walk(local_dir):
    for name in files:
        local = os.path.join(root, name)
        rel = os.path.relpath(local, local_dir)
        remote = remote_dir + '/' + rel.replace('\\', '/')
        parent = os.path.dirname(remote).replace('\\', '/')
        try:
            sftp.mkdir(parent)
        except:
            pass
        try:
            sftp.put(local, remote)
            uploaded += 1
            size = os.path.getsize(local)
            print(f'  OK [{size/(1024*1024):.1f}MB] {rel}')
        except Exception as e:
            print(f'  FAIL: {rel} - {e}')

sftp.close()
print(f'\nUploaded {uploaded} files')

# Verify
stdin, stdout, stderr = ssh.exec_command('ls -lh /hxbid/bge-base-zh-v1.5/ && echo --- && du -sh /hxbid/bge-base-zh-v1.5/')
print(stdout.read().decode(errors='replace').strip())
ssh.close()
