import { useState, useEffect } from 'react'
import { Card, Table, Tag, Button, Modal, Select, message, Space, Popconfirm, Input, Form } from 'antd'
import { UserAddOutlined, EditOutlined, DeleteOutlined, KeyOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'

interface User {
  id: string
  username: string
  display_name: string
  role: string
  is_active: boolean
  created_at: string
}

const roleColorMap: Record<string, string> = {
  admin: 'red',
  editor: 'blue',
  viewer: 'default',
}

const roleLabelMap: Record<string, string> = {
  admin: '管理员',
  editor: '编辑者',
  viewer: '观察者',
}

export default function UserManagement() {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)

  // Create user modal
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [createForm] = Form.useForm()
  const [creating, setCreating] = useState(false)

  // Edit user modal
  const [editModalOpen, setEditModalOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [editRole, setEditRole] = useState('')
  const [editDisplayName, setEditDisplayName] = useState('')
  const [saving, setSaving] = useState(false)

  // Reset password modal
  const [pwdModalOpen, setPwdModalOpen] = useState(false)
  const [pwdUser, setPwdUser] = useState<User | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [resetting, setResetting] = useState(false)

  const fetchUsers = () => {
    setLoading(true)
    client
      .get('/admin/users')
      .then((res) => setUsers(res.data))
      .catch(() => message.error('获取用户列表失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchUsers()
  }, [])

  // ── Create user ──

  const handleCreate = async () => {
    const values = await createForm.validateFields()
    setCreating(true)
    try {
      await client.post('/auth/register', {
        username: values.username,
        password: values.password,
        display_name: values.display_name || values.username,
        role: values.role || 'editor',
      })
      message.success(`用户「${values.username}」已创建`)
      setCreateModalOpen(false)
      createForm.resetFields()
      fetchUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '创建失败')
    } finally {
      setCreating(false)
    }
  }

  // ── Edit user ──

  const openEditModal = (user: User) => {
    setEditingUser(user)
    setEditRole(user.role)
    setEditDisplayName(user.display_name || '')
    setEditModalOpen(true)
  }

  const handleSaveEdit = async () => {
    if (!editingUser) return
    setSaving(true)
    try {
      await client.put(`/admin/users/${editingUser.id}`, {
        role: editRole,
        display_name: editDisplayName,
      })
      message.success('用户信息已更新')
      setEditModalOpen(false)
      fetchUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新失败')
    } finally {
      setSaving(false)
    }
  }

  // ── Reset password ──

  const openResetPwd = (user: User) => {
    setPwdUser(user)
    setNewPassword('')
    setPwdModalOpen(true)
  }

  const handleResetPwd = async () => {
    if (!pwdUser || !newPassword) return
    setResetting(true)
    try {
      await client.put(`/admin/users/${pwdUser.id}/password`, { password: newPassword })
      message.success(`「${pwdUser.username}」密码已重置`)
      setPwdModalOpen(false)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '重置失败')
    } finally {
      setResetting(false)
    }
  }

  // ── Toggle active ──

  const handleToggleActive = async (user: User) => {
    try {
      await client.put(`/admin/users/${user.id}`, { is_active: !user.is_active })
      message.success(user.is_active ? '用户已禁用' : '用户已启用')
      fetchUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '操作失败')
    }
  }

  // ── Delete ──

  const handleDelete = async (userId: string) => {
    try {
      await client.delete(`/admin/users/${userId}`)
      message.success('用户已删除')
      fetchUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '删除失败')
    }
  }

  const columns: ColumnsType<User> = [
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
    },
    {
      title: '显示名',
      dataIndex: 'display_name',
      key: 'display_name',
    },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      render: (role: string) => (
        <Tag color={roleColorMap[role] || 'default'}>
          {roleLabelMap[role] || role}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      render: (active: boolean) => (
        <Tag color={active ? 'success' : 'error'}>
          {active ? '启用' : '禁用'}
        </Tag>
      ),
    },
    {
      title: '注册时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (d: string) => new Date(d).toLocaleDateString('zh-CN'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: User) => (
        <Space>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEditModal(record)}
          >
            编辑
          </Button>
          <Button
            size="small"
            icon={<KeyOutlined />}
            onClick={() => openResetPwd(record)}
          >
            密码
          </Button>
          <Button
            size="small"
            onClick={() => handleToggleActive(record)}
          >
            {record.is_active ? '禁用' : '启用'}
          </Button>
          <Popconfirm
            title="确定删除此用户？"
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <Card
        title="用户管理"
        extra={
          <Space>
            <Button type="primary" icon={<UserAddOutlined />} onClick={() => {
              createForm.resetFields()
              setCreateModalOpen(true)
            }}>
              添加用户
            </Button>
            <Tag color="red">仅管理员可见</Tag>
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Table<User>
          columns={columns}
          dataSource={users}
          rowKey="id"
          loading={loading}
          pagination={false}
        />
      </Card>

      {/* Create user modal */}
      <Modal
        title="添加用户"
        open={createModalOpen}
        onOk={handleCreate}
        onCancel={() => setCreateModalOpen(false)}
        confirmLoading={creating}
        okText="创建"
      >
        <Form form={createForm} layout="vertical">
          <Form.Item label="用户名" name="username" rules={[{ required: true, message: '请输入用户名' }, { min: 2, max: 50 }]}>
            <Input placeholder="登录用户名" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: '请输入密码' }, { min: 6, max: 100 }]}>
            <Input.Password placeholder="至少6位" />
          </Form.Item>
          <Form.Item label="显示名" name="display_name">
            <Input placeholder="用户显示名称，留空则使用用户名" />
          </Form.Item>
          <Form.Item label="角色" name="role" initialValue="editor">
            <Select
              options={[
                { value: 'editor', label: '编辑者 (editor) — 可创建/编辑/删除资源' },
                { value: 'viewer', label: '观察者 (viewer) — 仅可查看' },
                { value: 'admin', label: '管理员 (admin) — 全部权限' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* Edit user modal */}
      <Modal
        title={`编辑用户 — ${editingUser?.username}`}
        open={editModalOpen}
        onOk={handleSaveEdit}
        onCancel={() => setEditModalOpen(false)}
        confirmLoading={saving}
        okText="保存"
      >
        <div style={{ marginBottom: 16 }}>
          当前角色：
          <Tag color={roleColorMap[editingUser?.role || '']}>
            {roleLabelMap[editingUser?.role || ''] || editingUser?.role}
          </Tag>
        </div>
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 4 }}>显示名</div>
          <Input
            value={editDisplayName}
            onChange={(e) => setEditDisplayName(e.target.value)}
            placeholder="显示名称"
          />
        </div>
        <div>
          <div style={{ marginBottom: 4 }}>角色</div>
          <Select
            value={editRole}
            onChange={setEditRole}
            style={{ width: '100%' }}
            options={[
              { value: 'admin', label: '管理员 (admin) — 全部权限' },
              { value: 'editor', label: '编辑者 (editor) — 可创建/编辑/删除资源' },
              { value: 'viewer', label: '观察者 (viewer) — 仅可查看' },
            ]}
          />
        </div>
      </Modal>

      {/* Reset password modal */}
      <Modal
        title={`重置密码 — ${pwdUser?.username}`}
        open={pwdModalOpen}
        onOk={handleResetPwd}
        onCancel={() => setPwdModalOpen(false)}
        confirmLoading={resetting}
        okText="重置"
      >
        <div style={{ marginBottom: 8 }}>为新密码：</div>
        <Input.Password
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          placeholder="输入新密码（至少6位）"
          minLength={6}
        />
      </Modal>
    </div>
  )
}
