import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Row, Col, Card, Statistic, Table, Tag, Button, Spin, List, Collapse, Empty } from 'antd'
import {
  FileTextOutlined,
  ClockCircleOutlined,
  CheckCircleOutlined,
  PlusOutlined,
  DatabaseOutlined,
  TrophyOutlined,
  BulbOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import client from '../api/client'

interface VectorStats {
  enabled: boolean
  total_chunks: number
  unique_projects: number
  unique_chapters: number
}

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
  const [vectorStats, setVectorStats] = useState<VectorStats | null>(null)
  const [analytics, setAnalytics] = useState<any>(null)
  const [winFactors, setWinFactors] = useState<any>(null)
  const navigate = useNavigate()

  useEffect(() => {
    client
      .get('/projects/')
      .then((res) => {
        setProjects(res.data.items ?? res.data ?? [])
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    client.get('/bid/vector-stats').then((res) => setVectorStats(res.data)).catch(() => {})
    client.get('/analytics/stats').then((res) => setAnalytics(res.data)).catch(() => {})
  }, [])

  const fetchWinFactors = () => {
    client.get('/analytics/win-factors').then((res) => setWinFactors(res.data)).catch(() => {})
  }

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
    { title: '状态', dataIndex: 'status', key: 'status',
      render: (s: string) => <Tag color={statusMap[s] || 'default'}>{statusLabelMap[s] || s}</Tag>,
    },
    { title: '中标结果', dataIndex: 'bid_result', key: 'bid_result',
      render: (r: string | null) => r ? <Tag color={bidResultColorMap[r] || 'default'}>{r}</Tag> : <Tag>--</Tag>,
    },
  ]

  const recentProjects = [...projects].sort((a, b) => b.id - a.id).slice(0, 5)

  if (loading) {
    return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
  }

  const winRate = analytics?.win_rate ?? 0

  return (
    <>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic title="标书总数" value={totalProjects} prefix={<FileTextOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="进行中" value={activeProjects} prefix={<ClockCircleOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={`胜率 (${analytics?.won || 0}胜/${analytics?.lost || 0}负)`}
              value={winRate}
              suffix="%"
              prefix={<TrophyOutlined />}
              valueStyle={{ color: winRate >= 50 ? '#52c41a' : winRate > 0 ? '#faad14' : '#999' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title={vectorStats?.enabled ? '知识库条目' : '知识库 (未启用)'}
              value={vectorStats === null ? '—' : (vectorStats.enabled ? vectorStats.total_chunks : 0)}
              valueStyle={vectorStats === null || !vectorStats.enabled ? { color: '#999', fontSize: 18 } : undefined}
              suffix={vectorStats?.enabled ? '条' : ''}
              prefix={<DatabaseOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={16}>
          <Card title="最近项目" extra={
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/projects/new')}>
              新建标书
            </Button>
          }>
            <Table<Project> columns={columns} dataSource={recentProjects} rowKey="id" pagination={false} />
          </Card>
        </Col>
        <Col span={8}>
          <Card title={<span><BulbOutlined /> 分析洞察</span>} style={{ height: '100%' }}>
            {analytics && analytics.total > 0 ? (
              <div>
                <Row gutter={8} style={{ marginBottom: 16 }}>
                  <Col span={8}><Statistic title="中标" value={analytics.won} valueStyle={{ color: '#52c41a', fontSize: 20 }} /></Col>
                  <Col span={8}><Statistic title="未中标" value={analytics.lost} valueStyle={{ color: '#ff4d4f', fontSize: 20 }} /></Col>
                  <Col span={8}><Statistic title="活跃规则" value={analytics.active_rules} valueStyle={{ fontSize: 20 }} /></Col>
                </Row>
                <Button type="link" onClick={fetchWinFactors} icon={<BulbOutlined />}>
                  {winFactors ? '刷新AI分析' : 'AI分析成败因素'}
                </Button>
                {winFactors?.analyzed && (
                  <div style={{ marginTop: 8, fontSize: 12, color: '#666' }}>
                    <div style={{ fontWeight: 'bold', color: '#52c41a' }}>成功要素：</div>
                    <List size="small" dataSource={winFactors.success_factors?.slice(0, 3) || []}
                      renderItem={(item: string) => <List.Item style={{ padding: '2px 0', fontSize: 12 }}>✅ {item}</List.Item>} />
                    {winFactors.improvement_areas?.length > 0 && (
                      <>
                        <div style={{ fontWeight: 'bold', color: '#ff4d4f', marginTop: 8 }}>改进方向：</div>
                        <List size="small" dataSource={winFactors.improvement_areas?.slice(0, 3) || []}
                          renderItem={(item: string) => <List.Item style={{ padding: '2px 0', fontSize: 12 }}>⚠️ {item}</List.Item>} />
                      </>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ color: '#999', textAlign: 'center', padding: 20 }}>
                暂无分析数据。在项目页标记中标/未中标结果后，反馈闭环将自动积累分析数据。
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </>
  )
}
