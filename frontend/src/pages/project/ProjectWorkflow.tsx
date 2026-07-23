import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Card,
  Steps,
  Button,
  Space,
  Tabs,
  Spin,
  message,
  Modal,
  Tag,
  Descriptions,
  List,
  Divider,
  Select,
} from 'antd'
import {
  ThunderboltOutlined,
  DownloadOutlined,
  ArrowLeftOutlined,
  ExperimentOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import GenerationProgress from '../../components/GenerationProgress'
import BidEditor from '../../components/BidEditor'
import CollectionStep from './CollectionStep'

interface Chapter {
  id: string
  title: string
  order_index: number
  ai_generated_content: string
  final_content: string
  status: string
}

interface SseChapter {
  id: string
  title: string
  status: string
}

interface RagSourceInfo {
  similar_count: number
  qual_count: number
  personnel_count: number
  similar_titles?: string[]
}

interface AiTraceInfo {
  verdict: string
  scores: {
    empty_phrase: number
    anchor: number
    repetition: number
    overall: number
  }
  empty_phrase_count: number
  anchor_count: number
  anchor_gaps: Array<{ paragraph_index: number; preview: string }>
  repetitive_openings: Array<{ pattern: string; count: number }>
  empty_phrases: Array<{ phrase: string; count: number }>
}

const steps = [
  { title: '上传招标文件' },
  { title: 'AI 解析' },
  { title: '信息搜集' },
  { title: 'AI 生成' },
  { title: '在线编辑' },
  { title: '导出' },
]

const statusStepMap: Record<string, number> = {
  draft: 0,
  parsed: 1,
  collecting: 2,
  generating: 3,
  review: 4,
  exported: 5,
}

export default function ProjectWorkflow() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [project, setProject] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [currentChapter, setCurrentChapter] = useState('')
  const [activeChapter, setActiveChapter] = useState<string>('')
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)

  // Edit analysis
  const [analyzing, setAnalyzing] = useState(false)
  const [editAnalysis, setEditAnalysis] = useState<any>(null)
  const [analysisModalOpen, setAnalysisModalOpen] = useState(false)

  // Feedback loop
  const [feedbackLoading, setFeedbackLoading] = useState(false)
  const [bidResult, setBidResult] = useState<string>(project?.bid_result || 'pending')

  // Template selector for export
  const [templates, setTemplates] = useState<Array<{ id: string; name: string; is_default: boolean }>>([])
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>(undefined)

  useEffect(() => {
    client.get('/templates/').then((res) => {
      setTemplates(res.data)
      const def = res.data.find((t: any) => t.is_default)
      if (def) setSelectedTemplateId(def.id)
    }).catch(() => {})
  }, [])

  // Generation progress tracking
  const [sseChapters, setSseChapters] = useState<SseChapter[]>([])
  const [completed, setCompleted] = useState(0)
  const [total, setTotal] = useState(0)
  const [ragSources, setRagSources] = useState<Record<string, RagSourceInfo>>({})
  const [aiTraces, setAiTraces] = useState<Record<string, AiTraceInfo>>({})

  // Local chapter content edits
  const [chapterContent, setChapterContent] = useState<Record<string, string>>({})

  // Retry failed sections
  const [retrying, setRetrying] = useState(false)
  const [failedSections, setFailedSections] = useState<
    Array<{ path: string; title: string; error: string | null }>
  >([])

  const fetchProject = useCallback(async () => {
    if (!id) return
    try {
      const res = await client.get(`/projects/${id}`)
      setProject(res.data)

      // Initialize chapter content map
      const contentMap: Record<string, string> = {}
      const chapters: Chapter[] = res.data.chapters ?? []
      for (const ch of chapters) {
        contentMap[ch.id] = ch.final_content || ch.ai_generated_content
      }
      setChapterContent(contentMap)

      // Set active chapter to first if not set
      if (chapters.length > 0 && !activeChapter) {
        setActiveChapter(chapters[0].id)
      }

      // Parse generation_state_json for failed sections
      try {
        const genStateStr = res.data.generation_state_json
        if (genStateStr && genStateStr !== '{}') {
          const genState = JSON.parse(genStateStr)
          const sections = genState.sections || {}
          const failed: Array<{ path: string; title: string; error: string | null }> = []
          for (const [path, sec] of Object.entries(sections)) {
            const s = sec as any
            if (s.status === 'failed') {
              failed.push({
                path,
                title: path.split(' > ').pop() || path,
                error: s.error || null,
              })
            }
          }
          setFailedSections(failed)
        } else {
          setFailedSections([])
        }
      } catch {
        setFailedSections([])
      }
    } catch {
      message.error('获取项目信息失败')
      navigate('/projects')
    } finally {
      setLoading(false)
    }
  }, [id, navigate, activeChapter])

  useEffect(() => {
    fetchProject()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleGenerate = async () => {
    if (!id) return
    setGenerating(true)
    setCompleted(0)
    setTotal(0)
    setSseChapters([])
    setCurrentChapter('')
    setRagSources({})
    setAiTraces({})

    const token = localStorage.getItem('token')
    let response: Response

    try {
      response = await fetch('/api/v1/bid/generate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ project_id: id }),
      })
    } catch {
      message.error('生成请求失败')
      setGenerating(false)
      return
    }

    if (!response.ok) {
      message.error('生成请求失败')
      setGenerating(false)
      return
    }

    const reader = response.body?.getReader()
    if (!reader) {
      message.error('无法读取生成流')
      setGenerating(false)
      return
    }

    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let currentEvent = ''
        for (const line of lines) {
          const trimmed = line.trim()

          if (trimmed.startsWith('event: ')) {
            currentEvent = trimmed.slice(7).trim()
          } else if (trimmed.startsWith('data: ')) {
            try {
              const data = JSON.parse(trimmed.slice(6))

              switch (currentEvent) {
                case 'status': {
                  // Deep pipeline: phase-based status updates
                  if (data.phase) {
                    if (data.total_leaf_sections) {
                      setTotal(data.total_leaf_sections)
                    }
                    if (data.message) {
                      setCurrentChapter(data.message)
                    }
                    break
                  }
                  // Legacy pipeline: chapter-based status
                  setTotal(data.total)
                  setCurrentChapter(data.chapter_id)
                  setSseChapters((prev) => {
                    const exists = prev.some((c) => c.id === data.chapter_id)
                    if (exists) return prev
                    return [
                      ...prev,
                      { id: data.chapter_id, title: data.title, status: 'pending' },
                    ]
                  })
                  break
                }
                case 'outline_generated': {
                  // Deep pipeline: outline is ready
                  setTotal(data.total_leaf_sections || data.total_parts || 0)
                  setCurrentChapter(`大纲已生成：${data.total_leaf_sections} 个子章节，预计 ${data.estimated_pages} 页`)
                  break
                }
                case 'subsection_status': {
                  // Deep pipeline: subsection progress
                  setCompleted(data.completed || 0)
                  if (data.total) setTotal(data.total)
                  if (data.current_title) {
                    setCurrentChapter(data.current_title)
                  }
                  // Add to SSE chapter list for visual tracking
                  const subId = `sub_${data.completed}_${data.current_title || ''}`
                  setSseChapters((prev) => {
                    // Keep list manageable: show last 20 items
                    const next = [...prev, { id: subId, title: data.current_title || '', status: 'pending' }]
                    return next.slice(-20)
                  })
                  break
                }
                case 'subsection_chunk': {
                  // Deep pipeline: accumulate content per chapter
                  if (data.chapter_id) {
                    setChapterContent((prev) => ({
                      ...prev,
                      [data.chapter_id]:
                        (prev[data.chapter_id] || '') + (data.text || ''),
                    }))
                  }
                  break
                }
                case 'chunk': {
                  // Legacy pipeline: chapter content
                  setChapterContent((prev) => ({
                    ...prev,
                    [data.chapter_id]:
                      (prev[data.chapter_id] || '') + data.text,
                  }))
                  break
                }
                case 'rag_sources': {
                  // Store RAG source info per chapter
                  setRagSources((prev) => ({
                    ...prev,
                    [data.chapter_id]: {
                      similar_count: data.similar_count ?? 0,
                      qual_count: data.qual_count ?? 0,
                      personnel_count: data.personnel_count ?? 0,
                      similar_titles: data.similar_titles ?? [],
                    },
                  }))
                  break
                }
                case 'ai_trace_report': {
                  setAiTraces((prev) => ({
                    ...prev,
                    [data.chapter_id]: {
                      verdict: data.verdict,
                      scores: data.scores,
                      empty_phrase_count: data.empty_phrase_count,
                      anchor_count: data.anchor_count,
                      anchor_gaps: data.anchor_gaps ?? [],
                      repetitive_openings: data.repetitive_openings ?? [],
                      empty_phrases: data.empty_phrases ?? [],
                    },
                  }))
                  break
                }
                case 'chapter_done': {
                  setCompleted((prev) => prev + 1)
                  // Mark chapter as generated in SSE list
                  setSseChapters((prev) =>
                    prev.map((c) =>
                      c.id === data.chapter_id
                        ? { ...c, status: 'generated' }
                        : c,
                    ),
                  )
                  break
                }
                case 'done':
                  break
                case 'error':
                  message.error(data.message || '生成过程中出现错误')
                  break
              }
            } catch {
              // Skip unparseable lines
            }
          }
        }
      }
    } catch {
      message.error('生成流读取失败')
    }

    setGenerating(false)
    // Refresh project data from server
    await fetchProject()
  }

  const handleRetry = async () => {
    if (!id) return
    setRetrying(true)
    setCompleted(0)
    setCurrentChapter('正在重试失败章节...')
    setSseChapters([])

    const token = localStorage.getItem('token')
    let response: Response

    try {
      response = await fetch('/api/v1/bid/generate/retry-failed', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ project_id: id }),
      })
    } catch {
      message.error('重试请求失败')
      setRetrying(false)
      return
    }

    if (!response.ok) {
      try {
        const errData = await response.json()
        message.error(errData.detail || '重试请求失败')
      } catch {
        message.error('重试请求失败')
      }
      setRetrying(false)
      return
    }

    const reader = response.body?.getReader()
    if (!reader) {
      message.error('无法读取重试流')
      setRetrying(false)
      return
    }

    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let currentEvent = ''
        for (const line of lines) {
          const trimmed = line.trim()

          if (trimmed.startsWith('event: ')) {
            currentEvent = trimmed.slice(7).trim()
          } else if (trimmed.startsWith('data: ')) {
            try {
              const data = JSON.parse(trimmed.slice(6))

              switch (currentEvent) {
                case 'status': {
                  if (data.phase === 'retry') {
                    setCurrentChapter(data.message || `正在重试 ${data.retry_count} 个失败章节...`)
                  } else if (data.phase === 'generating') {
                    if (data.total_leaf_sections) setTotal(data.total_leaf_sections)
                    if (data.completed_leaf_sections) setCompleted(data.completed_leaf_sections)
                    if (data.message) setCurrentChapter(data.message)
                  } else if (data.message) {
                    setCurrentChapter(data.message)
                  }
                  break
                }
                case 'outline_generated': {
                  setTotal(data.total_leaf_sections || data.total_parts || 0)
                  setCurrentChapter(`大纲已加载：${data.total_leaf_sections} 个子章节，已完成 ${data.completed_from_previous || 0} 个`)
                  break
                }
                case 'subsection_status': {
                  setCompleted(data.completed || 0)
                  if (data.total) setTotal(data.total)
                  if (data.current_title) {
                    setCurrentChapter(data.current_title)
                  }
                  const subId = `retry_${data.completed}_${data.current_title || ''}`
                  setSseChapters((prev) => {
                    const next = [...prev, { id: subId, title: data.current_title || '', status: 'pending' }]
                    return next.slice(-20)
                  })
                  break
                }
                case 'subsection_chunk': {
                  if (data.chapter_id) {
                    setChapterContent((prev) => ({
                      ...prev,
                      [data.chapter_id]:
                        (prev[data.chapter_id] || '') + (data.text || ''),
                    }))
                  }
                  break
                }
                case 'section_done': {
                  setCompleted((prev) => prev + 1)
                  setSseChapters((prev) =>
                    prev.map((c) =>
                      c.id === `retry_${data.index}_${data.title}`
                        ? { ...c, status: 'generated' }
                        : c,
                    ),
                  )
                  break
                }
                case 'section_error': {
                  message.warning(`${data.title || data.path}: ${data.error || '生成失败'}`)
                  break
                }
                case 'done':
                  break
                case 'error':
                  message.error(data.message || '重试过程中出现错误')
                  break
              }
            } catch {
              // Skip unparseable lines
            }
          }
        }
      }
    } catch {
      message.error('重试流读取失败')
    }

    setRetrying(false)
    await fetchProject()
  }

  const handleSave = async () => {
    if (!id || !activeChapter) return
    setSaving(true)
    try {
      await client.put(`/projects/${id}/chapters/${activeChapter}`, {
        final_content: chapterContent[activeChapter] || '',
        status: 'edited',
      })
      message.success('章节保存成功')

      // Update local project chapter status
      setProject((prev: any) => {
        if (!prev?.chapters) return prev
        return {
          ...prev,
          chapters: prev.chapters.map((ch: Chapter) =>
            ch.id === activeChapter ? { ...ch, status: 'edited' } : ch,
          ),
        }
      })
    } catch {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleExport = async () => {
    if (!id) return
    setExporting(true)
    try {
      const res = await client.post('/bid/export', {
        project_id: id,
        format: 'both',
        template_id: selectedTemplateId,
      })
      const { docx_url, pdf_url } = res.data

      // Use anchor-click pattern to avoid popup blocker after async await
      const triggerDownload = (url: string) => {
        const a = document.createElement('a')
        a.href = url
        a.style.display = 'none'
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
      }

      if (docx_url) {
        triggerDownload(docx_url)
      }
      if (pdf_url) {
        triggerDownload(pdf_url)
      }

      message.success('导出成功')
      // Refresh project to update status to exported
      await fetchProject()
    } catch {
      message.error('导出失败')
    } finally {
      setExporting(false)
    }
  }

  const handleAnalyzeEdits = async () => {
    if (!id) return
    setAnalyzing(true)
    setEditAnalysis(null)
    try {
      const res = await client.post('/bid/analyze-edits', { project_id: id })
      setEditAnalysis(res.data)
      setAnalysisModalOpen(true)
      if (res.data.chapters_analyzed === 0) {
        message.info('没有已编辑的章节可供分析')
      } else {
        message.success(`分析了 ${res.data.chapters_analyzed} 个章节的编辑意图`)
      }
    } catch (err: any) {
      message.error(err.response?.data?.detail || '编辑分析失败')
    } finally {
      setAnalyzing(false)
    }
  }

  const handleRunFeedback = async () => {
    if (!id) return
    setFeedbackLoading(true)
    try {
      const res = await client.post('/feedback/run', {
        project_id: id,
        bid_result: bidResult !== 'pending' ? bidResult : null,
      })
      message.success(
        `反馈闭环完成：新增 ${res.data.rules_new} 条规则，升级 ${res.data.rules_upgraded} 条`,
      )
      await fetchProject()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '反馈闭环执行失败')
    } finally {
      setFeedbackLoading(false)
    }
  }

  const handleMarkResult = async (result: string) => {
    if (!id) return
    try {
      await client.put(`/feedback/projects/${id}/result`, { result })
      setBidResult(result)
      message.success(`已标记为${result === 'won' ? '中标' : result === 'lost' ? '未中标' : '待定'}`)
      await fetchProject()
    } catch (err: any) {
      message.error(err.response?.data?.detail || '更新失败')
    }
  }

  const handleChapterChange = (chapterId: string, html: string) => {
    setChapterContent((prev) => ({
      ...prev,
      [chapterId]: html,
    }))
  }

  // --- Render ---

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    )
  }

  if (!project) {
    return null
  }

  const projectChapters: Chapter[] = project.chapters ?? []
  const currentStep = statusStepMap[project.status] ?? 0
  const hasChapters = projectChapters.length > 0

  return (
    <div>
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
        }}
      >
        <h2 style={{ margin: 0 }}>{project.name}</h2>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/projects')}>
          返回
        </Button>
      </div>

      {/* Steps bar */}
      <Card style={{ marginBottom: 24 }}>
        <Steps
          current={currentStep}
          items={steps}
          style={{ marginBottom: 24 }}
        />

        <Space>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={generating}
            onClick={handleGenerate}
            disabled={generating || retrying || project.status === 'collecting'}
          >
            一键生成标书
          </Button>
          <Select
            value={selectedTemplateId}
            onChange={setSelectedTemplateId}
            style={{ width: 180 }}
            placeholder="选择排版模板"
            options={templates.map((t) => ({
              value: t.id,
              label: t.name + (t.is_default ? ' (默认)' : ''),
            }))}
            prefix={<FileTextOutlined />}
          />
          <Button
            icon={<DownloadOutlined />}
            loading={exporting}
            disabled={!hasChapters || generating || retrying}
            onClick={handleExport}
          >
            导出 Word + PDF
          </Button>
          <Button
            icon={<ExperimentOutlined />}
            loading={analyzing}
            disabled={!hasChapters || generating || project.status === 'draft'}
            onClick={handleAnalyzeEdits}
          >
            分析编辑意图
          </Button>

          {project.status === 'review' || project.status === 'exported' ? (
            <>
              <Select
                value={bidResult}
                onChange={(v) => handleMarkResult(v)}
                style={{ width: 100 }}
                options={[
                  { value: 'pending', label: '待定' },
                  { value: 'won', label: '中标 ✅' },
                  { value: 'lost', label: '未中标 ❌' },
                ]}
              />
              <Button
                type="primary"
                loading={feedbackLoading}
                onClick={handleRunFeedback}
              >
                反馈闭环
              </Button>
            </>
          ) : null}
        </Space>
      </Card>

      {/* Information Collection step */}
      {project.status === 'collecting' && (
        <CollectionStep
          projectId={id!}
          onComplete={() => { fetchProject() }}
        />
      )}

      {/* Generation progress */}
      {(generating || retrying) && (
        <Card title={retrying ? '重试进度' : '生成进度'} style={{ marginBottom: 24 }}>
          <GenerationProgress
            chapters={sseChapters}
            currentChapter={currentChapter}
            completed={completed}
            total={total}
            ragSources={ragSources}
            aiTraces={aiTraces}
          />
        </Card>
      )}

      {/* Failed sections warning with retry button */}
      {!generating && !retrying && failedSections.length > 0 && (
        <Card
          title={
            <Space>
              <Tag color="error">{failedSections.length} 个章节生成失败</Tag>
            </Space>
          }
          style={{ marginBottom: 24, borderColor: '#ff4d4f' }}
          extra={
            <Button
              type="primary"
              danger
              icon={<ThunderboltOutlined />}
              loading={retrying}
              onClick={handleRetry}
            >
              重试失败章节
            </Button>
          }
        >
          <List
            size="small"
            dataSource={failedSections}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={item.title}
                  description={
                    item.error ? (
                      <span style={{ color: '#ff4d4f' }}>{item.error}</span>
                    ) : (
                      '未知错误'
                    )
                  }
                />
              </List.Item>
            )}
          />
        </Card>
      )}

      {/* Chapter editor tabs */}
      {hasChapters && !generating && !retrying && (
        <Card>
          {projectChapters.length > 0 ? (
            <Tabs
              type="card"
              activeKey={activeChapter}
              onChange={setActiveChapter}
              items={[...projectChapters]
                .sort((a, b) => a.order_index - b.order_index)
                .map((ch) => ({
                  key: ch.id,
                  label: ch.title,
                  children: (
                    <BidEditor
                      content={chapterContent[ch.id] || ''}
                      onChange={(html) => handleChapterChange(ch.id, html)}
                      onSave={handleSave}
                      saving={saving}
                    />
                  ),
                }))}
            />
          ) : (
            <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
              暂无章节，请先生成标书内容
            </div>
          )}
        </Card>
      )}

      {/* Edit Analysis Modal */}
      <Modal
        title="编辑意图分析报告"
        open={analysisModalOpen}
        onCancel={() => setAnalysisModalOpen(false)}
        footer={null}
        width={800}
      >
        {editAnalysis ? (
          <div>
            <Descriptions size="small" column={3} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="分析章节">{editAnalysis.chapters_analyzed}</Descriptions.Item>
              <Descriptions.Item label="总修改数">{editAnalysis.total_changes}</Descriptions.Item>
              <Descriptions.Item label="可提炼规则">{editAnalysis.suggested_rules?.length || 0}</Descriptions.Item>
            </Descriptions>

            {Object.keys(editAnalysis.edit_type_totals || {}).length > 0 && (
              <>
                <div style={{ fontWeight: 'bold', marginBottom: 8 }}>修改类型分布</div>
                <Space wrap style={{ marginBottom: 16 }}>
                  {Object.entries(editAnalysis.edit_type_totals).map(
                    ([type, count]: [string, any]) => (
                      <Tag key={type} color="blue">
                        {type}：{count}次
                      </Tag>
                    ),
                  )}
                </Space>
              </>
            )}

            {editAnalysis.suggested_rules?.length > 0 && (
              <>
                <Divider />
                <div style={{ fontWeight: 'bold', marginBottom: 8 }}>
                  可提炼的写作规则（将在后续生成中应用）
                </div>
                <List
                  size="small"
                  dataSource={editAnalysis.suggested_rules}
                  renderItem={(rule: string, i: number) => (
                    <List.Item>
                      <Tag color="green">规则 {i + 1}</Tag> {rule}
                    </List.Item>
                  )}
                />
              </>
            )}

            {editAnalysis.results?.length > 0 && (
              <>
                <Divider />
                <div style={{ fontWeight: 'bold', marginBottom: 8 }}>逐章详情</div>
                {editAnalysis.results.map((r: any) => (
                  <Card
                    key={r.chapter_id}
                    size="small"
                    title={r.chapter_title || '未命名章节'}
                    style={{ marginBottom: 8 }}
                  >
                    {r.error ? (
                      <Tag color="red">分析失败：{r.error}</Tag>
                    ) : (
                      <>
                        <Descriptions size="small" column={3}>
                          <Descriptions.Item label="修改数">{r.total_changes}</Descriptions.Item>
                          <Descriptions.Item label="AI分析">{r.ai_analyzed ? '是' : '否（启发式）'}</Descriptions.Item>
                          <Descriptions.Item label="修改段数">{r.segments?.length || 0}</Descriptions.Item>
                        </Descriptions>
                        {r.segments?.length > 0 && (
                          <List
                            size="small"
                            dataSource={r.segments.slice(0, 5)}
                            renderItem={(seg: any) => (
                              <List.Item>
                                <Space direction="vertical" size={0} style={{ width: '100%' }}>
                                  <span>
                                    <Tag color="orange">{seg.edit_type}</Tag>
                                    置信度：{Math.round(seg.confidence * 100)}%
                                  </span>
                                  <span style={{ color: '#666', fontSize: 12 }}>
                                    {seg.reason}
                                  </span>
                                </Space>
                              </List.Item>
                            )}
                          />
                        )}
                      </>
                    )}
                  </Card>
                ))}
              </>
            )}
          </div>
        ) : (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        )}
      </Modal>
    </div>
  )
}
