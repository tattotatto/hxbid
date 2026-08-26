import { useEffect, useState, useRef } from 'react'
import type { ReactNode } from 'react'
import {
  Table, Tag, message, Upload, Button, Modal, Input, Progress, Drawer, Tooltip, Space, Spin,
} from 'antd'
import {
  UploadOutlined, InboxOutlined, LoadingOutlined, ClockCircleOutlined,
  CheckCircleOutlined, CloseCircleOutlined, EyeOutlined, ReloadOutlined, DeleteOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { UploadProps } from 'antd'
import dayjs from 'dayjs'
import client from '../../api/client'

type BidLessonStatus = 'pending' | 'analyzing' | 'ready' | 'failed'

interface RequirementCoverage {
  requirement: string
  category: string
  bid_response: string
  how_addressed: string
  quality: 'good' | 'partial' | 'missing'
  lesson: string
}

interface StructureMapping {
  tender_section: string
  bid_section: string
  format_ok: boolean
  notes: string
}

interface WritingStyle {
  overall: string
  patterns: string[]
  strengths: string[]
}

interface Lesson {
  tender_meta: { summary?: string; [key: string]: unknown }
  requirements_coverage: RequirementCoverage[]
  structure_mapping: StructureMapping[]
  writing_style: WritingStyle
  lessons: string[]
}

interface BidLesson {
  id: string
  name: string
  source_type: 'upload' | 'project'
  status: BidLessonStatus
  error: string | null
  created_at: string
  lesson?: Lesson
}

const statusMeta: Record<BidLessonStatus, { text: string; color: string }> = {
  pending: { text: '等待分析', color: 'default' },
  analyzing: { text: '分析中', color: 'processing' },
  ready: { text: '已学习', color: 'success' },
  failed: { text: '分析失败', color: 'error' },
}

const statusIcon: Record<BidLessonStatus, ReactNode> = {
  pending: <ClockCircleOutlined />,
  analyzing: <LoadingOutlined spin />,
  ready: <CheckCircleOutlined />,
  failed: <CloseCircleOutlined />,
}

const qualityMeta: Record<RequirementCoverage['quality'], { text: string; color: string }> = {
  good: { text: '达标', color: 'success' },
  partial: { text: '部分达标', color: 'warning' },
  missing: { text: '缺失', color: 'error' },
}

function StatusBadge({ status }: { status: BidLessonStatus }) {
  const meta = statusMeta[status]
  return <Tag color={meta.color} icon={statusIcon[status]}>{meta.text}</Tag>
}

const coverageColumns: ColumnsType<RequirementCoverage> = [
  { title: '要求', dataIndex: 'requirement', key: 'requirement' },
  { title: '类别', dataIndex: 'category', key: 'category', width: 100 },
  { title: '标书应答', dataIndex: 'bid_response', key: 'bid_response' },
  {
    title: '质量',
    dataIndex: 'quality',
    key: 'quality',
    width: 100,
    render: (q: RequirementCoverage['quality']) => (
      <Tag color={qualityMeta[q].color}>{qualityMeta[q].text}</Tag>
    ),
  },
  { title: '写法要点', dataIndex: 'lesson', key: 'lesson' },
]

const structureColumns: ColumnsType<StructureMapping> = [
  { title: '招标章节', dataIndex: 'tender_section', key: 'tender_section' },
  { title: '标书章节', dataIndex: 'bid_section', key: 'bid_section' },
  {
    title: '格式是否合规',
    dataIndex: 'format_ok',
    key: 'format_ok',
    width: 120,
    render: (ok: boolean) => (ok ? <Tag color="success">合规</Tag> : <Tag color="warning">需调整</Tag>),
  },
  { title: '说明', dataIndex: 'notes', key: 'notes' },
]

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

export default function HistoryBids() {
  const [list, setList] = useState<BidLesson[]>([])
  const [loading, setLoading] = useState(false)

  // Upload modal
  const [modalOpen, setModalOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadPercent, setUploadPercent] = useState(0)
  const [tenderFile, setTenderFile] = useState<File | null>(null)
  const [bidFile, setBidFile] = useState<File | null>(null)
  const [bidName, setBidName] = useState('')

  // Report drawer
  const [reportOpen, setReportOpen] = useState(false)
  const [reportLoading, setReportLoading] = useState(false)
  const [reportData, setReportData] = useState<BidLesson | null>(null)

  const [relearning, setRelearning] = useState(false)

  const pollTimerRef = useRef<number | null>(null)

  const maybePoll = (items: BidLesson[]) => {
    const hasInProgress = items.some((l) => l.status === 'pending' || l.status === 'analyzing')
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
    if (hasInProgress) {
      pollTimerRef.current = window.setInterval(() => {
        fetchList({ silent: true })
      }, 5000)
    }
  }

  const fetchList = async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true)
    try {
      const res = await client.get('/bid-lessons')
      const items: BidLesson[] = res.data.items || []
      setList(items)
      maybePoll(items)
    } catch {
      if (!opts?.silent) message.error('获取历史标书失败')
    } finally {
      if (!opts?.silent) setLoading(false)
    }
  }

  useEffect(() => {
    fetchList()
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Upload ──

  const beforeTenderUpload: UploadProps['beforeUpload'] = (file) => {
    setTenderFile(file)
    return false
  }

  const beforeBidUpload: UploadProps['beforeUpload'] = (file) => {
    setBidFile(file)
    return false
  }

  const closeModal = () => {
    if (uploading) return
    setModalOpen(false)
    setTenderFile(null)
    setBidFile(null)
    setBidName('')
    setUploadPercent(0)
  }

  const handleSubmitUpload = () => {
    if (!tenderFile || !bidFile || uploading) return

    const formData = new FormData()
    formData.append('tender_file', tenderFile)
    formData.append('bid_file', bidFile)
    if (bidName.trim()) formData.append('name', bidName.trim())

    setUploading(true)
    setUploadPercent(0)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/v1/bid-lessons/upload')
    const token = localStorage.getItem('token')
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        setUploadPercent(Math.round((e.loaded / e.total) * 100))
      }
    })

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        message.success('已上传，正在分析招标文件并对比标书...')
        setModalOpen(false)
        setTenderFile(null)
        setBidFile(null)
        setBidName('')
        setUploadPercent(0)
        fetchList()
      } else {
        let detail = '上传失败'
        try { detail = JSON.parse(xhr.responseText)?.detail || detail } catch {}
        message.error(detail)
      }
      setUploading(false)
    })

    xhr.addEventListener('error', () => {
      message.error('网络错误，请检查连接后重试')
      setUploading(false)
    })

    xhr.send(formData)
  }

  // ── Report ──

  const openReport = async (id: string) => {
    setReportOpen(true)
    setReportLoading(true)
    setReportData(null)
    try {
      const res = await client.get(`/bid-lessons/${id}`)
      setReportData(res.data)
    } catch (err: any) {
      message.error(err.response?.data?.detail || '获取学习报告失败')
    } finally {
      setReportLoading(false)
    }
  }

  // ── Relearn / Delete ──

  const handleRelearn = async (id: string) => {
    if (relearning) return
    setRelearning(true)
    try {
      await client.post(`/bid-lessons/${id}/relearn`)
      message.success('已重新触发分析')
      fetchList()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '重试失败')
    } finally {
      setRelearning(false)
    }
  }

  const handleDelete = (record: BidLesson) => {
    Modal.confirm({
      title: '确定删除这条学习记录？',
      content: `「${record.name}」删除后不可恢复`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          await client.delete(`/bid-lessons/${record.id}`)
          message.success('已删除')
          fetchList()
        } catch {
          message.error('删除失败')
        }
      },
    })
  }

  const columns: ColumnsType<BidLesson> = [
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '来源',
      dataIndex: 'source_type',
      key: 'source_type',
      width: 110,
      render: (sourceType: string) =>
        sourceType === 'project' ? <Tag color="blue">系统项目</Tag> : <Tag color="green">上传</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 180,
      render: (_: unknown, record: BidLesson) => {
        const badge = <StatusBadge status={record.status} />
        if (record.status === 'failed' && record.error) {
          return (
            <div>
              <Tooltip title={record.error}>{badge}</Tooltip>
              <div
                style={{
                  fontSize: 12,
                  color: '#999',
                  marginTop: 4,
                  maxWidth: 220,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {record.error}
              </div>
            </div>
          )
        }
        return badge
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 150,
      render: (val: string) => dayjs(val).format('YYYY-MM-DD HH:mm'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 230,
      render: (_: unknown, record: BidLesson) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openReport(record.id)}>
            查看报告
          </Button>
          {record.status === 'failed' && (
            <Button size="small" icon={<ReloadOutlined />} loading={relearning} onClick={() => handleRelearn(record.id)}>
              重试
            </Button>
          )}
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(record)} />
        </Space>
      ),
    },
  ]

  const renderLesson = (lesson: Lesson) => (
    <div>
      {lesson.tender_meta?.summary && (
        <div style={{ marginBottom: 24 }}>
          <h4 style={{ margin: '0 0 8px' }}>招标文件摘要</h4>
          <p style={{ whiteSpace: 'pre-wrap', color: '#555', margin: 0 }}>{lesson.tender_meta.summary}</p>
        </div>
      )}

      <h4 style={{ margin: '0 0 8px' }}>要求覆盖情况</h4>
      <Table<RequirementCoverage>
        size="small"
        rowKey={(_, i) => String(i)}
        dataSource={lesson.requirements_coverage || []}
        columns={coverageColumns}
        pagination={false}
      />

      <h4 style={{ margin: '24px 0 8px' }}>结构映射</h4>
      <Table<StructureMapping>
        size="small"
        rowKey={(_, i) => String(i)}
        dataSource={lesson.structure_mapping || []}
        columns={structureColumns}
        pagination={false}
      />

      <h4 style={{ margin: '24px 0 8px' }}>写作风格</h4>
      {lesson.writing_style?.overall && (
        <p style={{ whiteSpace: 'pre-wrap', color: '#555', margin: '0 0 8px' }}>{lesson.writing_style.overall}</p>
      )}
      {lesson.writing_style?.patterns?.length > 0 && (
        <>
          <div style={{ fontWeight: 500, margin: '8px 0 4px' }}>常见模式</div>
          <ul style={{ paddingLeft: 20, margin: 0 }}>
            {lesson.writing_style.patterns.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </>
      )}
      {lesson.writing_style?.strengths?.length > 0 && (
        <>
          <div style={{ fontWeight: 500, margin: '8px 0 4px' }}>优势</div>
          <ul style={{ paddingLeft: 20, margin: 0 }}>
            {lesson.writing_style.strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </>
      )}

      {lesson.lessons?.length > 0 && (
        <>
          <h4 style={{ margin: '24px 0 8px' }}>经验教训</h4>
          <ol style={{ paddingLeft: 20, margin: 0 }}>
            {lesson.lessons.map((l, i) => (
              <li key={i} style={{ marginBottom: 4 }}>{l}</li>
            ))}
          </ol>
        </>
      )}
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>历史标书</h2>
        <Button type="primary" icon={<UploadOutlined />} onClick={() => setModalOpen(true)}>
          上传招标文件与标书
        </Button>
      </div>

      <Table<BidLesson>
        columns={columns}
        dataSource={list}
        rowKey="id"
        loading={loading}
      />

      <Modal
        title="上传招标文件与标书"
        open={modalOpen}
        onCancel={closeModal}
        maskClosable={!uploading}
        closable={!uploading}
        footer={
          uploading
            ? null
            : [
                <Button key="cancel" onClick={closeModal}>取消</Button>,
                <Button
                  key="submit"
                  type="primary"
                  disabled={!tenderFile || !bidFile}
                  onClick={handleSubmitUpload}
                >
                  上传并分析
                </Button>,
              ]
        }
      >
        {uploading ? (
          <div style={{ textAlign: 'center', padding: '24px 0' }}>
            <div style={{ fontSize: 16, marginBottom: 16, fontWeight: 500 }}>
              正在上传招标文件与标书…
            </div>
            <Progress
              type="circle"
              percent={uploadPercent}
              size={120}
              status={uploadPercent < 100 ? 'active' : 'success'}
            />
            <div style={{ marginTop: 16, color: '#888', fontSize: 13 }}>
              {uploadPercent < 100 ? `上传中 ${uploadPercent}% ... 大文件请耐心等待` : '上传完成，正在开始分析...'}
            </div>
          </div>
        ) : (
          <>
            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>名称（可选）</div>
              <Input
                placeholder="如：XX项目2023年度投标"
                value={bidName}
                onChange={(e) => setBidName(e.target.value)}
              />
            </div>

            <div style={{ marginBottom: 16 }}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>招标文件</div>
              <Upload.Dragger
                accept=".docx,.doc,.pdf,.wps"
                beforeUpload={beforeTenderUpload}
                showUploadList={false}
                maxCount={1}
              >
                <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                <p className="ant-upload-text">点击或拖拽招标文件</p>
                <p className="ant-upload-hint">支持 .docx / .doc / .pdf / .wps</p>
              </Upload.Dragger>
              {tenderFile && (
                <div style={{ marginTop: 8, fontSize: 13, color: '#555' }}>
                  已选择：{tenderFile.name}（{formatFileSize(tenderFile.size)}）
                </div>
              )}
            </div>

            <div style={{ marginBottom: 8 }}>
              <div style={{ marginBottom: 8, fontWeight: 500 }}>标书</div>
              <Upload.Dragger
                accept=".docx,.doc,.pdf,.wps"
                beforeUpload={beforeBidUpload}
                showUploadList={false}
                maxCount={1}
              >
                <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                <p className="ant-upload-text">点击或拖拽标书文件</p>
                <p className="ant-upload-hint">支持 .docx / .doc / .pdf / .wps</p>
              </Upload.Dragger>
              {bidFile && (
                <div style={{ marginTop: 8, fontSize: 13, color: '#555' }}>
                  已选择：{bidFile.name}（{formatFileSize(bidFile.size)}）
                </div>
              )}
            </div>
          </>
        )}
      </Modal>

      <Drawer
        title={reportData ? `${reportData.name} — 学习报告` : '学习报告'}
        open={reportOpen}
        onClose={() => { setReportOpen(false); setReportData(null) }}
        width={760}
      >
        {reportLoading ? (
          <div style={{ textAlign: 'center', padding: 48 }}><Spin size="large" /></div>
        ) : !reportData ? (
          <p style={{ color: '#999', textAlign: 'center', padding: 24 }}>报告加载失败</p>
        ) : reportData.status !== 'ready' ? (
          <div style={{ textAlign: 'center', padding: 48 }}>
            <StatusBadge status={reportData.status} />
            <p style={{ color: '#999', marginTop: 16 }}>
              {reportData.status === 'failed'
                ? reportData.error || '分析失败'
                : '正在分析中，请稍后刷新查看报告'}
            </p>
          </div>
        ) : (
          renderLesson(reportData.lesson as Lesson)
        )}
      </Drawer>
    </div>
  )
}
