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
} from 'antd'
import {
  ThunderboltOutlined,
  DownloadOutlined,
  ArrowLeftOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import GenerationProgress from '../../components/GenerationProgress'
import BidEditor from '../../components/BidEditor'

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

const steps = [
  { title: '上传招标文件' },
  { title: 'AI 解析' },
  { title: 'AI 生成' },
  { title: '在线编辑' },
  { title: '导出' },
]

const statusStepMap: Record<string, number> = {
  draft: 0,
  parsed: 1,
  generating: 2,
  review: 3,
  exported: 4,
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

  // Generation progress tracking
  const [sseChapters, setSseChapters] = useState<SseChapter[]>([])
  const [completed, setCompleted] = useState(0)
  const [total, setTotal] = useState(0)

  // Local chapter content edits
  const [chapterContent, setChapterContent] = useState<Record<string, string>>({})

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
                  setTotal(data.total)
                  setCurrentChapter(data.chapter_id)
                  // Add chapter to SSE tracking list if new
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
                case 'chunk': {
                  // Accumulate content for this chapter
                  setChapterContent((prev) => ({
                    ...prev,
                    [data.chapter_id]:
                      (prev[data.chapter_id] || '') + data.text,
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
      })
      const { docx_url, pdf_url } = res.data

      if (docx_url) {
        window.open(docx_url, '_blank')
      }
      if (pdf_url) {
        window.open(pdf_url, '_blank')
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
            disabled={generating}
          >
            一键生成标书
          </Button>
          <Button
            icon={<DownloadOutlined />}
            loading={exporting}
            disabled={!hasChapters || generating}
            onClick={handleExport}
          >
            导出 Word + PDF
          </Button>
        </Space>
      </Card>

      {/* Generation progress */}
      {generating && (
        <Card title="生成进度" style={{ marginBottom: 24 }}>
          <GenerationProgress
            chapters={sseChapters}
            currentChapter={currentChapter}
            completed={completed}
            total={total}
          />
        </Card>
      )}

      {/* Chapter editor tabs */}
      {hasChapters && !generating && (
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
    </div>
  )
}
