import paramiko, os

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.50.157', username='johnwoo', password='admin4wwj', timeout=10)

sftp = ssh.open_sftp()

local_dir = r'D:\bge-base-zh-v1.5'
remote_dir = '/opt/hxbid/bge-base-zh-v1.5'

# Get remote files
stdin, stdout, stderr = ssh.exec_command('cd ' + remote_dir + ' && find . -type f | sed s/^..//')
remote_files = set(stdout.read().decode().strip().split('\n'))

# Upload missing
uploaded = 0
for root, dirs, files in os.walk(local_dir):
    for f in files:
        local_path = os.path.join(root, f)
        rel = os.path.relpath(local_path, local_dir)
        rel = rel.replace('\\', '/')
        if rel not in remote_files:
            remote_path = remote_dir + '/' + rel
            remote_parent = os.path.dirname(remote_path).replace('\\', '/')
            try:
                sftp.mkdir(remote_parent)
            except:
                pass
            sftp.put(local_path, remote_path)
            uploaded += 1
            print(f'Uploaded: {rel}')

sftp.close()
print(f'Done: {uploaded} files')

# Verify
stdin, stdout, stderr = ssh.exec_command('cat /opt/hxbid/bge-base-zh-v1.5/1_Pooling/config.json')
print('Pooling config:', stdout.read().decode()[:200])

ssh.close()
