import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Row, Col, Card, Statistic, Table, Tag, Button, Spin } from 'antd'
import {
  FileTextOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../api/client'

interface Project {
  id: number
  name: string
  status: string
  bid_result: string | null
}

const statusMap: Record<string, string> = {
  draft: 'default',
  parsing: 'processing',
  parsed: 'blue',
  generating: 'processing',
  review: 'warning',
  exported: 'success',
}

const statusLabelMap: Record<string, string> = {
  draft: '草稿',
  parsing: '解析中',
  parsed: '已解析',
  generating: '生成中',
  review: '审核中',
  exported: '已导出',
}

const bidResultColorMap: Record<string, string> = {
  '中标': 'success',
  '未中标': 'error',
  '待定': 'default',
  '未公布': 'default',
}

export default function Workbench() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    client
      .get('/projects/')
      .then((res) => {
        setProjects(res.data.items ?? res.data ?? [])
      })
      .catch(() => {
        // silently handle error on workbench
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  const totalProjects = projects.length
  const activeProjects = projects.filter(
    (p) => !['draft', 'exported'].includes(p.status),
  ).length
  const wonProjects = projects.filter((p) => p.bid_result === '中标').length

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
        <Tag color={statusMap[status] || 'default'}>
          {statusLabelMap[status] || status}
        </Tag>
      ),
    },
    {
      title: '中标结果',
      dataIndex: 'bid_result',
      key: 'bid_result',
      render: (bidResult: string | null) =>
        bidResult ? (
          <Tag color={bidResultColorMap[bidResult] || 'default'}>
            {bidResult}
          </Tag>
        ) : (
          <Tag>--</Tag>
        ),
    },
  ]

  const recentProjects = [...projects]
    .sort((a, b) => b.id - a.id)
    .slice(0, 5)

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    )
  }

  return (
    <>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card>
            <Statistic
              title="标书总数"
              value={totalProjects}
              prefix={<FileTextOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="进行中"
              value={activeProjects}
              prefix={<ClockCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="已中标"
              value={wonProjects}
              prefix={<CheckCircleOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card
        title="最近项目"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate('/projects/new')}
          >
            新建标书
          </Button>
        }
      >
        <Table<Project>
          columns={columns}
          dataSource={recentProjects}
          rowKey="id"
          pagination={false}
        />
      </Card>
    </>
  )
}
