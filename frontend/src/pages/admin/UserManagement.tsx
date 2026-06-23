import { useState, useEffect } from 'react'
import { Card, Table, Tag, Button, Modal, Select, message, Space, Popconfirm } from 'antd'
import { UserAddOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons'
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
  const [editModalOpen, setEditModalOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [editRole, setEditRole] = useState('')
  const [saving, setSaving] = useState(false)

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

  const openEditRole = (user: User) => {
    setEditingUser(user)
    setEditRole(user.role)
    setEditModalOpen(true)
  }

  const handleSaveRole = async () => {
    if (!editingUser) return
    setSaving(true)
    try {
      await client.put(`/admin/users/${editingUser.id}`, { role: editRole })
      message.success('角色已更新')
      setEditModalOpen(false)
      fetchUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新失败')
    } finally {
      setSaving(false)
    }
  }

  const handleToggleActive = async (user: User) => {
    try {
      await client.put(`/admin/users/${user.id}`, { is_active: !user.is_active })
      message.success(user.is_active ? '用户已禁用' : '用户已启用')
      fetchUsers()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '操作失败')
    }
  }

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
            onClick={() => openEditRole(record)}
          >
            角色
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
          <Tag color="red">仅管理员可见</Tag>
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

      <Modal
        title={`修改角色 — ${editingUser?.username}`}
        open={editModalOpen}
        onOk={handleSaveRole}
        onCancel={() => setEditModalOpen(false)}
        confirmLoading={saving}
      >
        <div style={{ marginBottom: 16 }}>
          当前角色：
          <Tag color={roleColorMap[editingUser?.role || '']}>
            {roleLabelMap[editingUser?.role || ''] || editingUser?.role}
          </Tag>
        </div>
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
      </Modal>
    </div>
  )
}
