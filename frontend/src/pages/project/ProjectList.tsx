import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Table, Button, Tag, Popconfirm, message, Space } from 'antd'
import { PlusOutlined, DeleteOutlined, FolderOpenOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../../api/client'

interface Project {
  id: number
  name: string
  status: string
  bid_deadline: string | null
  created_at: string
}

const statusColorMap: Record<string, string> = {
  draft: 'default',
  parsed: 'blue',
  generating: 'processing',
  review: 'warning',
  exported: 'success',
}

export default function ProjectList() {
  const navigate = useNavigate()
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)

  const fetchProjects = async () => {
    setLoading(true)
    try {
      const res = await client.get('/projects/')
      setProjects(res.data)
    } catch {
      message.error('获取项目列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchProjects()
  }, [])

  const handleDelete = async (id: number) => {
    try {
      await client.delete(`/projects/${id}`)
      message.success('项目已删除')
      fetchProjects()
    } catch {
      message.error('删除项目失败')
    }
  }

  const columns: ColumnsType<Project> = [
    {
      title: '项目名称',
      dataIndex: 'name',
      key: 'name',
      render: (text: string, record: Project) => (
        <a onClick={() => navigate(`/projects/${record.id}`)}>{text}</a>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={statusColorMap[status] || 'default'}>{status}</Tag>
      ),
    },
    {
      title: '投标截止',
      dataIndex: 'bid_deadline',
      key: 'bid_deadline',
      render: (val: string | null) => val || '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (val: string) => new Date(val).toLocaleDateString('zh-CN'),
    },
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: Project) => (
        <Space>
          <Button
            type="link"
            icon={<FolderOpenOutlined />}
            onClick={() => navigate(`/projects/${record.id}`)}
          >
            打开
          </Button>
          <Popconfirm
            title="确认删除"
            description="确定要删除这个项目吗？"
            onConfirm={() => handleDelete(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button type="link" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 16,
        }}
      >
        <h2 style={{ margin: 0 }}>标书项目</h2>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => navigate('/projects/new')}
        >
          新建标书
        </Button>
      </div>
      <Table<Project>
        columns={columns}
        dataSource={projects}
        rowKey="id"
        loading={loading}
      />
    </div>
  )
}
