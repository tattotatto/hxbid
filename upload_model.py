"""Upload BGE model to server via SFTP."""
import paramiko, os, sys

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.50.157', username='johnwoo', password='admin4wwj', timeout=15)

sftp = ssh.open_sftp()
local_dir = r'D:\bge-base-zh-v1.5'
remote_dir = '/opt/hxbid/bge-base-zh-v1.5'

# Create remote dir
stdin, stdout, stderr = ssh.exec_command('mkdir -p ' + remote_dir)
stdout.read()  # consume

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
        sftp.put(local, remote)
        uploaded += 1
        if uploaded % 3 == 0:
            print(f'{uploaded} files...')

sftp.close()
print(f'Done: {uploaded} files')

# Verify
stdin, stdout, stderr = ssh.exec_command('ls /opt/hxbid/bge-base-zh-v1.5/ && echo --- && du -sh /opt/hxbid/bge-base-zh-v1.5/')
print(stdout.read().decode().strip())
ssh.close()
