import { useEffect, useState } from 'react'
import { Table, Tag, message, Upload, Button, Modal, Input } from 'antd'
import { UploadOutlined, InboxOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { UploadProps } from 'antd'
import dayjs from 'dayjs'
import client from '../../api/client'

interface Project {
  id: string
  name: string
  status: string
  created_at: string
}

const RELEVANT_STATUSES = ['exported', 'archived', 'won', 'lost']

const statusLabelMap: Record<string, { text: string; color: string }> = {
  won: { text: '已中标', color: 'success' },
  lost: { text: '未中标', color: 'error' },
  exported: { text: '已完成', color: 'blue' },
  archived: { text: '已归档', color: 'default' },
}

export default function HistoryBids() {
  const [data, setData] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [bidName, setBidName] = useState('')

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

  useEffect(() => { fetchProjects() }, [])

  const handleUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options as any
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('project_name', bidName || file.name)
      await client.post('/bid/upload-history', formData)
      message.success(`「${bidName || file.name}」已上传到历史标书库`)
      setModalOpen(false)
      setBidName('')
      fetchProjects()
      onSuccess?.('ok')
    } catch (err: any) {
      message.error(err.response?.data?.detail || '上传失败')
      onError?.(err)
    } finally {
      setUploading(false)
    }
  }

  const columns: ColumnsType<Project> = [
    { title: '项目名称', dataIndex: 'name', key: 'name' },
    {
      title: '状态',
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
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>历史标书</h2>
        <Button type="primary" icon={<UploadOutlined />} onClick={() => setModalOpen(true)}>
          上传历史标书
        </Button>
      </div>

      <Table<Project>
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
      />

      <Modal
        title="上传历史标书"
        open={modalOpen}
        onCancel={() => { setModalOpen(false); setBidName('') }}
        footer={null}
      >
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8, fontWeight: 500 }}>标书名称（可选）</div>
          <Input
            placeholder="如：XX项目2023年度投标文件"
            value={bidName}
            onChange={(e) => setBidName(e.target.value)}
          />
        </div>
        <Upload.Dragger
          accept=".docx,.doc,.pdf,.wps"
          customRequest={handleUpload}
          showUploadList={false}
          disabled={uploading}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽历史标书文件</p>
          <p className="ant-upload-hint">支持 .docx / .doc / .pdf / .wps，文件将自动解析归档</p>
        </Upload.Dragger>
      </Modal>
    </div>
  )
}
