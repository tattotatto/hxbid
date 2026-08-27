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
  InputNumber,
  Alert,
} from 'antd'
import {
  ThunderboltOutlined,
  DownloadOutlined,
  ArrowLeftOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  FileSearchOutlined,
} from '@ant-design/icons'
import client from '../../api/client'
import GenerationProgress from '../../components/GenerationProgress'
import BidEditor from '../../components/BidEditor'
import TreeEditor from '../../components/TreeEditor/TreeEditor'
import CollectionStep from './CollectionStep'
import ScoringReportCard from '../../components/ScoringReportCard'
import { scoringApi, ScoringReport } from '../../api/scoring'

interface Chapter {
  id: string
  title: string
  order_index: number
  ai_generated_content: string
  final_content: string
  status: string
  chapter_type?: string
  chapter_meta_json?: string
  children_json?: string
  review_status?: string
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
  { title: '目录确认' },
  { title: '信息搜集' },
  { title: 'AI 生成' },
  { title: '在线编辑' },
  { title: '导出' },
]

const statusStepMap: Record<string, number> = {
  draft: 0,
  parsed: 1,
  structure_ready: 2,
  collecting: 3,
  generating: 4,
  review: 5,
  exported: 6,
}

// Parse markdown headings from AI-generated content into a tree structure
function parseMarkdownHeadings(content: string): any[] {
  if (!content) return [];
  const lines = content.split('\n');
  const root: any[] = [];
  const stack: { level: number; node: any }[] = [];

  for (let li = 0; li < lines.length; li++) {
    const line = lines[li];
    const match = line.trim().match(/^(#{1,4})\s+(.+)$/);
    if (!match) continue;

    const level = match[1].length;
    const title = match[2].trim();

    // Extract content under this heading (until next heading of same/higher level)
    let contentEnd = lines.length;
    for (let i = li + 1; i < lines.length; i++) {
      const nextMatch = lines[i].trim().match(/^(#{1,4})\s+(.+)$/);
      if (nextMatch && nextMatch[1].length <= level) {
        contentEnd = i;
        break;
      }
    }
    const sectionContent = lines.slice(li + 1, contentEnd).join('\n').trim();

    const node: any = {
      level,
      title,
      content: sectionContent,
      children: [],
    };

    // Place node at correct level
    while (stack.length > 0 && stack[stack.length - 1].level >= level) {
      stack.pop();
    }

    if (stack.length === 0) {
      root.push(node);
    } else {
      stack[stack.length - 1].node.children.push(node);
    }

    stack.push({ level, node });
  }

  return root;
}

// Reconstruct tree structure from flattened task list
function rebuildTreeFromTasks(tasks: any[]): any[] {
  const root: any[] = [];
  const nodeMap = new Map<string, any>();

  for (const task of tasks) {
    const path: string[] = task.path || [];
    if (path.length === 0) continue;

    // Ensure all ancestors exist
    for (let i = 0; i < path.length; i++) {
      const key = path.slice(0, i + 1).join('::');
      if (!nodeMap.has(key)) {
        const node = {
          title: path[i],
          content: i === path.length - 1 ? (task.content || '') : '',
          human_edited: i === path.length - 1 ? (task.human_edited || false) : false,
          token_budget_hint: i === path.length - 1 ? task.token_budget_hint : undefined,
          children: [] as any[],
        };
        nodeMap.set(key, node);

        if (i === 0) {
          root.push(node);
        } else {
          const parentKey = path.slice(0, i).join('::');
          const parent = nodeMap.get(parentKey);
          if (parent && !parent.children.find((c: any) => c.title === node.title)) {
            parent.children.push(node);
          }
        }
      } else if (i === path.length - 1) {
        // Update existing node with content
        const node = nodeMap.get(key);
        if (node) {
          node.content = task.content || '';
          node.human_edited = task.human_edited || false;
        }
      }
    }
  }

  return root;
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

  // Target page count (per-project, default 2000)
  const [targetPages, setTargetPages] = useState(2000)
  // Format verification result from the new pipeline
  const [formatVerification, setFormatVerification] = useState<{
    overall_status: string
    message?: string
  } | null>(null)
  // Self-scoring report (evaluated by the scoring rubric, SSE `scoring_report` / GET /scoring-report)
  const [scoringReport, setScoringReport] = useState<ScoringReport | null>(null)
  const [rescoring, setRescoring] = useState(false)

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
      setTargetPages(res.data.target_pages || 2000)
      setFormatVerification(null)

      // Load persisted scoring report (if the project has been scored before)
      scoringApi.getReport(id).then((r) => {
        if (r.items?.length) setScoringReport(r as ScoringReport)
      }).catch(() => {})

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

      // Reconcile failed sections from two sources, unioned by `path`:
      // (1) generation_state_json; (2) each chapter's children_json — the new
      //     pipeline writes back status === 'failed' onto leaf nodes.
      const failedByPath = new Map<
        string,
        { path: string; title: string; error: string | null }
      >()
      const addFailed = (path: string, title: string, error: string | null) => {
        if (!failedByPath.has(path)) {
          failedByPath.set(path, { path, title, error })
        }
      }

      // Source 1: generation_state_json
      try {
        const genStateStr = res.data.generation_state_json
        if (genStateStr && genStateStr !== '{}') {
          const genState = JSON.parse(genStateStr)
          const sections = genState.sections || {}
          for (const [path, sec] of Object.entries(sections)) {
            const s = sec as any
            if (s.status === 'failed') {
              addFailed(path, path.split(' > ').pop() || path, s.error || null)
            }
          }
        }
      } catch {
        // generation_state parse failure is non-fatal; children_json scan still runs
      }

      // Source 2: children_json — path semantics match backend _collect_leaf_tasks:
      // a node with a `path` array is a flat task (its path is the FULL path,
      // already includes the chapter title); otherwise it is nested and we build
      // [...ancestor, n.title] starting from [ch.title].
      const collectFailed = (nodes: any[], ancestor: string[]) => {
        for (const n of nodes ?? []) {
          if (!n || typeof n !== 'object') continue
          const fullPath: string[] = Array.isArray(n.path)
            ? n.path
            : [...ancestor, n.title]
          if (n.status === 'failed') {
            addFailed(fullPath.join(' > '), n.title, n.error || null)
          }
          if (Array.isArray(n.children) && n.children.length > 0) {
            collectFailed(n.children, fullPath)
          }
        }
      }
      for (const ch of chapters) {
        if (!ch.children_json) continue
        try {
          const parsed = JSON.parse(ch.children_json)
          if (Array.isArray(parsed)) {
            collectFailed(parsed, [ch.title])
          }
        } catch {
          // skip unparseable children_json
        }
      }

      setFailedSections(Array.from(failedByPath.values()))
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

  const handleTargetPagesChange = async (value: number | null) => {
    const v = value ?? 2000
    setTargetPages(v)
    if (!id) return
    try {
      await client.put(`/projects/${id}`, { target_pages: v })
    } catch {
      // 静默失败：生成请求仍会带上 targetPages 值
    }
  }

  const readGenerateStream = async (response: Response, done: () => void) => {
    const reader = response.body?.getReader()
    if (!reader) {
      message.error('无法读取生成流')
      done()
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
                  // New pipeline: outline ready with per-leaf page budget
                  setTotal(data.total_leaves || data.total_leaf_sections || data.total_parts || 0)
                  setCurrentChapter(
                    `大纲已生成：共 ${data.total_leaves} 个小节，目标 ${data.target_pages || targetPages} 页，预计约 ${data.estimated_pages} 页`,
                  )
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
                case 'chapter_start': {
                  // New pipeline: per-chapter serial submission
                  setCurrentChapter(`正在生成第 ${data.index}/${data.total} 章：${data.title}...`)
                  break
                }
                case 'chapter_done': {
                  setCurrentChapter(`第 ${data.title} 章完成（成功 ${data.leaf_success ?? 0}，失败 ${data.leaf_failed ?? 0}）`)
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
                case 'chapter_error': {
                  // New pipeline: whole chapter failed to submit
                  setCurrentChapter(`第 ${data.title} 章生成失败${data.error ? '：' + data.error : ''}`)
                  break
                }
                case 'section_start': {
                  // New pipeline: a leaf section is starting
                  if (data.total) setTotal(data.total)
                  setCurrentChapter(data.title || data.path || '')
                  const subId = `sec_${data.index}_${data.title || data.path || ''}`
                  setSseChapters((prev) => {
                    const exists = prev.some((c) => c.id === subId)
                    if (exists) return prev
                    return [
                      ...prev,
                      { id: subId, title: data.title || data.path || '', status: 'pending' },
                    ].slice(-20)
                  })
                  break
                }
                case 'section_done': {
                  // New pipeline: a leaf section finished (index = completed count).
                  // Resumed (already-generated) leaves emit an "orphan done" with no
                  // preceding section_start — the subId map lookup is a harmless no-op
                  // and setCompleted(data.index) still advances progress. Never align
                  // on index; alignment is by path.
                  setCompleted(data.index || 0)
                  const subId = `sec_${data.index}_${data.title || data.path || ''}`
                  setSseChapters((prev) =>
                    prev.map((c) =>
                      c.id === subId ? { ...c, status: 'generated' } : c,
                    ),
                  )
                  // 生成失败时管道仍会带 error 字段发 section_done（内容为失败提示）
                  if (data.error) {
                    setFailedSections((prev) => {
                      const exists = prev.some((f) => f.path === data.path)
                      if (exists) return prev
                      return [
                        ...prev,
                        {
                          path: data.path || '',
                          title: data.title || data.path || '',
                          error: data.error || null,
                        },
                      ]
                    })
                  }
                  break
                }
                case 'section_error': {
                  setFailedSections((prev) => {
                    const exists = prev.some((f) => f.path === data.path)
                    if (exists) return prev
                    return [
                      ...prev,
                      {
                        path: data.path || '',
                        title: data.title || data.path || '',
                        error: data.error || null,
                      },
                    ]
                  })
                  setCompleted(data.index || 0)
                  break
                }
                case 'progress': {
                  setCompleted(data.completed || 0)
                  if (data.total) setTotal(data.total)
                  break
                }
                case 'format_verification': {
                  setFormatVerification({
                    overall_status: data.overall_status || 'pass',
                    message: data.message || '',
                  })
                  if (data.overall_status === 'fail') {
                    message.warning('格式校验未通过：' + (data.message || '存在缺失必需章节'))
                  } else if (data.overall_status === 'pass_with_warnings') {
                    message.info('格式校验通过（有提示）：' + (data.message || ''))
                  } else {
                    message.success('格式校验通过')
                  }
                  break
                }
                case 'scoring_report': {
                  // Full scoring report JSON (ai_pipeline sends json.dumps(report)).
                  // `data` is already parsed at the top of the reader — do not JSON.parse
                  // again. Shape guard: only accept objects bearing an `items` array so
                  // a malformed event cannot overwrite the report with garbage.
                  if (data && Array.isArray(data.items)) {
                    setScoringReport(data as ScoringReport)
                  }
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

    done()
  }

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
        body: JSON.stringify({ project_id: id, target_pages: targetPages }),
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

    await readGenerateStream(response, () => setGenerating(false))
    // Refresh project data from server
    await fetchProject()
  }

  const handleRetry = async () => {
    if (!id) return
    setRetrying(true)
    setCompleted(0)
    setTotal(0)
    setSseChapters([])
    setCurrentChapter('重新生成（跳过已完成章节，补齐未生成的）')
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
        body: JSON.stringify({ project_id: id, target_pages: targetPages }),
      })
    } catch {
      message.error('生成请求失败')
      setRetrying(false)
      return
    }

    if (!response.ok) {
      message.error('生成请求失败')
      setRetrying(false)
      return
    }

    await readGenerateStream(response, () => setRetrying(false))
    await fetchProject()
  }

  const handleRescore = async () => {
    if (!id || rescoring) return
    setRescoring(true)
    try {
      const report = await scoringApi.rescore(id)
      setScoringReport(report)
      message.success('重新评分完成')
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '重新评分失败')
    } finally {
      setRescoring(false)
    }
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

        {/* 「目录确认门」提示：仅当 status === structure_ready 时显示，
            引导用户回到 outline 页面确认目录。 */}
        {project.status === 'structure_ready' && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="AI 已提取章节结构，请先确认目录后再进入信息搜集"
            description="您可以手动调整章节、让 AI 对话修改，或直接进入目录确认页。"
            action={
              <Button
                type="primary"
                size="small"
                icon={<FileSearchOutlined />}
                onClick={() => navigate(`/projects/${id}/outline`)}
              >
                前往目录确认 →
              </Button>
            }
          />
        )}

        <Space wrap>
          <InputNumber
            min={200}
            max={5000}
            step={100}
            value={targetPages}
            onChange={handleTargetPagesChange}
            addonBefore="目标页数"
            style={{ width: 180 }}
            disabled={generating || retrying}
          />
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

      {/* Format verification result (new pipeline, persists after generation) */}
      {formatVerification && !generating && !retrying && (
        <Card size="small" style={{ marginBottom: 24 }} title="格式校验结果">
          <Space>
            <Tag
              color={
                formatVerification.overall_status === 'pass'
                  ? 'success'
                  : formatVerification.overall_status === 'pass_with_warnings'
                    ? 'warning'
                    : 'error'
              }
            >
              {formatVerification.overall_status === 'pass'
                ? '通过'
                : formatVerification.overall_status === 'pass_with_warnings'
                  ? '通过（有提示）'
                  : '未通过'}
            </Tag>
            {formatVerification.message ? <span>{formatVerification.message}</span> : null}
          </Space>
        </Card>
      )}

      {/* Self-scoring report card (renders nothing until a report is present) */}
      <ScoringReportCard
        report={scoringReport}
        onRescore={handleRescore}
        rescoring={rescoring}
      />

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

      {/* Chapter editor — Tree Editor (优先) or Tabs (回退) */}
      {hasChapters && !generating && !retrying && (
        <Card bodyStyle={{ padding: 0 }}>
          {projectChapters.length > 0 ? (
            <TreeEditor
              chapters={projectChapters
                .sort((a, b) => a.order_index - b.order_index)
                .map(ch => ({
                  id: ch.id,
                  title: ch.title,
                  order_index: ch.order_index,
                  chapter_type: ch.chapter_type || 'text',
                  review_status: ch.review_status || '',
                  status: ch.status,
                  content: ch.final_content || ch.ai_generated_content || '',
                  children: (() => {
                    try {
                      const parsed = ch.children_json ? JSON.parse(ch.children_json) : [];
                      // New pipeline: structured children_json
                      if (Array.isArray(parsed) && parsed.length > 0) {
                        if ('path' in parsed[0]) {
                          return rebuildTreeFromTasks(parsed);
                        }
                        return parsed;
                      }
                      // Old project: parse markdown headings from content
                      const content = ch.final_content || ch.ai_generated_content || '';
                      return parseMarkdownHeadings(content);
                    } catch { return []; }
                  })(),
                }))}
              projectId={id || ''}
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
