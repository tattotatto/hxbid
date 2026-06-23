import { useEffect, useState, useRef } from 'react'
import { Table, Tag, message, Upload, Button, Modal, Input, Progress } from 'antd'
import { UploadOutlined, InboxOutlined, DeploymentUnitOutlined } from '@ant-design/icons'
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

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export default function HistoryBids() {
  const [data, setData] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadPercent, setUploadPercent] = useState(0)
  const [uploadFileName, setUploadFileName] = useState('')
  const [uploadFileSize, setUploadFileSize] = useState(0)
  const [bidName, setBidName] = useState('')
  const [vectorizing, setVectorizing] = useState<Record<string, boolean>>({})
  const xhrRef = useRef<XMLHttpRequest | null>(null)

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

  const handleUpload: UploadProps['customRequest'] = (options) => {
    const { file, onSuccess, onError } = options as any
    const token = localStorage.getItem('token')

    setUploading(true)
    setUploadPercent(0)
    setUploadFileName(file.name)
    setUploadFileSize(file.size)

    const formData = new FormData()
    formData.append('file', file)
    formData.append('project_name', bidName || file.name)

    const xhr = new XMLHttpRequest()
    xhrRef.current = xhr

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        setUploadPercent(Math.round((e.loaded / e.total) * 100))
      }
    })

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        message.success(`「${bidName || file.name}」已上传到历史标书库`)
        setModalOpen(false)
        setBidName('')
        setUploadPercent(0)
        fetchProjects()
        onSuccess?.(JSON.parse(xhr.responseText))
      } else {
        let detail = '上传失败'
        try { detail = JSON.parse(xhr.responseText)?.detail || detail } catch {}
        message.error(detail)
        onError?.(new Error(detail))
      }
      setUploading(false)
      xhrRef.current = null
    })

    xhr.addEventListener('error', () => {
      message.error('网络错误，请检查连接后重试')
      setUploading(false)
      xhrRef.current = null
      onError?.(new Error('Network error'))
    })

    xhr.open('POST', '/api/v1/bid/upload-history')
    xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.send(formData)
  }

  const handleVectorize = async (id: string) => {
    setVectorizing((p) => ({ ...p, [id]: true }))
    try {
      const res = await client.post(`/bid/index-history/${id}`)
      message.success(`向量化完成：${res.data.sections_indexed} 个章节片段已入库`)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '向量化失败')
    } finally {
      setVectorizing((p) => ({ ...p, [id]: false }))
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
    {
      title: '操作',
      key: 'actions',
      render: (_: unknown, record: Project) => (
        <Button
          size="small"
          icon={<DeploymentUnitOutlined />}
          loading={vectorizing[record.id]}
          onClick={() => handleVectorize(record.id)}
        >
          向量化
        </Button>
      ),
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
        onCancel={() => {
          if (!uploading) { setModalOpen(false); setBidName('') }
        }}
        footer={null}
        maskClosable={!uploading}
        closable={!uploading}
      >
        {!uploading ? (
          <>
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
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽历史标书文件</p>
              <p className="ant-upload-hint">支持 .docx / .doc / .pdf / .wps，最大 500MB</p>
            </Upload.Dragger>
          </>
        ) : (
          <div style={{ textAlign: 'center', padding: '24px 0' }}>
            <div style={{ fontSize: 16, marginBottom: 16, fontWeight: 500 }}>
              正在上传：{uploadFileName}
            </div>
            <div style={{ fontSize: 13, color: '#888', marginBottom: 20 }}>
              文件大小：{formatFileSize(uploadFileSize)}
            </div>
            <Progress
              type="circle"
              percent={uploadPercent}
              size={120}
              status={uploadPercent < 100 ? 'active' : 'success'}
            />
            <div style={{ marginTop: 16, color: '#888', fontSize: 13 }}>
              {uploadPercent < 100
                ? `上传中 ${uploadPercent}% ... 大文件请耐心等待`
                : '上传完成，正在解析归档...'}
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
