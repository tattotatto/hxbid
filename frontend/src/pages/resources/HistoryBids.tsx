import { useEffect, useState } from 'react'
import { Table, Tag, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs from 'dayjs'
import client from '../../api/client'

interface Project {
  id: number
  name: string
  status: string
  created_at: string
}

const RELEVANT_STATUSES = ['exported', 'archived', 'won', 'lost']

const statusLabelMap: Record<string, { text: string; color: string }> = {
  won: { text: '已中标', color: 'success' },
  lost: { text: '未中标', color: 'error' },
  exported: { text: '待定', color: 'default' },
  archived: { text: '待定', color: 'default' },
}

export default function HistoryBids() {
  const [data, setData] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const fetchProjects = async () => {
      setLoading(true)
      try {
        const res = await client.get('/projects/')
        const filtered = (res.data as Project[]).filter((p) =>
          RELEVANT_STATUSES.includes(p.status),
        )
        setData(filtered)
      } catch {
        message.error('获取历史标书失败')
      } finally {
        setLoading(false)
      }
    }
    fetchProjects()
  }, [])

  const columns: ColumnsType<Project> = [
    { title: '项目名称', dataIndex: 'name', key: 'name' },
    {
      title: '中标结果',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => {
        const info = statusLabelMap[status] || { text: '待定', color: 'default' }
        return <Tag color={info.color}>{info.text}</Tag>
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (val: string) => dayjs(val).format('YYYY-MM-DD'),
    },
  ]

  return (
    <div>
      <h2 style={{ marginBottom: 16 }}>历史标书</h2>
      <Table<Project>
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
      />
    </div>
  )
}
