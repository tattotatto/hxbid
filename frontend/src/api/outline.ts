import client from './client'
import type { RubricCover } from './scoring'

export type ChapterType = 'fixed_form' | 'table' | 'ai_generated' | 'attachment' | 'mixed'

/** 用户可在结构页选择的类型（mixed 由 AI 提取产生，保留展示但不可选） */
export const USER_SELECTABLE_TYPES: ChapterType[] = [
  'fixed_form',
  'table',
  'attachment',
  'ai_generated',
]

export interface OutlineMatchCandidate {
  title: string
  raw: string
  level: number
  start: number
  end: number
  score: number
  page: number | null
  preview: string
  preview_truncated: boolean
}

export interface OutlineMatchResult {
  status: 'matched' | 'ambiguous' | 'missing' | 'na'
  source: string | null
  best: OutlineMatchCandidate | null
  candidates: OutlineMatchCandidate[]
  table_index: number | null
  /** 表格章节的命中内容（best 为空时抽屉靠它显示） */
  table_preview: string | null
  /** auto=系统最佳；manual=用户从候选里点选过 */
  picked: 'auto' | 'manual'
  corpus_hash: string | null
}

export interface OutlineAttachment {
  kind: 'qualification' | 'personnel_cert' | 'contract' | 'upload'
  id: string
  label: string
  path: string
}

export interface OutlineChapter {
  order_index: number
  number: string
  title: string
  /** 章节类型；后端 ProjectChapter 行用 chapter_type，extract/chat 输出用 type。
   *  读取时统一用 chapterType 兼容两者（见 normalize 函数）。*/
  type?: ChapterType
  chapter_type?: ChapterType
  required?: boolean
  format_notes?: string
  scoring_context?: string
  table_columns?: string[]
  /** 评分指标自动补入的节点标记（「来自评标办法」徽标） */
  source?: string
  /** 结构页固化的招标原文匹配引用（生成阶段据此切片） */
  match?: OutlineMatchResult | null
  /** 附件清单（仅 type === 'attachment' 有意义） */
  attachments?: OutlineAttachment[]
  children?: OutlineChapter[]
}

/** 把后端多种返回形态统一为 {type, title, ...} — 上层不必关心字段名差异 */
function normalize(ch: any): OutlineChapter {
  return {
    ...ch,
    type: ch.type ?? ch.chapter_type ?? 'ai_generated',
    order_index: ch.order_index ?? 0,
    number: ch.number ?? '',
    title: ch.title ?? '(未命名)',
    children: Array.isArray(ch.children) ? ch.children.map(normalize) : [],
  }
}

/** 按索引路径不可变地更新节点（与 OutlineTree 内的同名 helper 语义一致） */
export function updateNodeByPath(
  chapters: OutlineChapter[],
  path: number[],
  updater: (n: OutlineChapter) => OutlineChapter,
): OutlineChapter[] {
  if (path.length === 0) return chapters
  const [head, ...rest] = path
  return chapters.map((ch, idx) => {
    if (idx !== head) return ch
    if (rest.length === 0) return updater(ch)
    return { ...ch, children: updateNodeByPath(ch.children ?? [], rest, updater) }
  })
}

/** 按索引路径取节点 */
export function getNodeByPath(
  chapters: OutlineChapter[],
  path: number[],
): OutlineChapter | null {
  let arr: OutlineChapter[] | undefined = chapters
  let node: OutlineChapter | null = null
  for (const idx of path) {
    if (!arr) return null
    node = arr[idx]
    arr = node?.children
  }
  return node
}

export interface OutlineGetResponse {
  chapters: OutlineChapter[]
  rubric_cover?: RubricCover | null
}

export interface OutlineChatResponse {
  reply: string
  chapters: OutlineChapter[]
  conversation_id: string
}

export interface OutlineConfirmResponse {
  success: boolean
  chapters_count: number
  status: string
  /** 本次确认按评标办法自动补充的章节/小节标题 */
  added_from_rubric: string[]
}

export const outlineApi = {
  /** 从 GET /bid/{pid}/chapters 提取顶层 chapter_structure_json 树 */
  get: async (projectId: string): Promise<OutlineGetResponse> => {
    const res = await client.get<{ chapters?: any[] }>(`/bid/${projectId}/chapters`)
    const data = res.data as any
    const raw: any[] = Array.isArray(data) ? data : (data?.chapters ?? [])
    // 统一 type/title/number 字段，递归处理 children
    return { chapters: raw.map(normalize), rubric_cover: data?.rubric_cover ?? null }
  },

  /** 触发 AI 提取章节结构：上传解析后必须调用，status -> structure_ready */
  extract: async (projectId: string): Promise<void> => {
    await client.post(`/bid/${projectId}/extract-chapters`)
  },

  /** 复用已有 /chapters/chat 端点：对话式编辑顶层章节结构 */
  chat: async (
    projectId: string,
    message: string,
    conversationId?: string,
  ): Promise<OutlineChatResponse> => {
    const res = await client.post<OutlineChatResponse>(
      `/bid/${projectId}/chapters/chat`,
      { message, conversation_id: conversationId ?? null },
    )
    return res.data
  },

  /** 保存整棵章节结构树（拖拽/改名/改类型/挂附件后调用） */
  saveStructure: async (projectId: string, chapters: OutlineChapter[]): Promise<void> => {
    await client.put(`/bid/${projectId}/chapter-structure`, { chapters })
  },

  /** 试匹配：给定标题与类型，返回命中的招标原文片段或候选 */
  match: async (
    projectId: string,
    title: string,
    type: ChapterType,
  ): Promise<OutlineMatchResult> => {
    const res = await client.post<OutlineMatchResult>(
      `/bid/${projectId}/chapter-structure/match`,
      { title, type },
    )
    return res.data
  },

  /** 上传本项目专用附件，返回一条可塞进章节节点的附件记录 */
  uploadAttachment: async (
    projectId: string,
    file: File,
    label: string,
  ): Promise<OutlineAttachment> => {
    const form = new FormData()
    form.append('file', file)
    // label 是标量字段：后端用 Form() 接，必须走 FormData 而不是 query
    form.append('label', label)
    const res = await client.post<OutlineAttachment>(
      `/bid/${projectId}/attachments/upload`,
      form,
    )
    return res.data
  },

  /** 唯一的「目录确认门」出口，物化 ProjectChapter + status -> collecting。
   *  带 chapters 时先存再物化，避免"改了忘保存" */
  confirm: async (
    projectId: string,
    chapters?: OutlineChapter[],
  ): Promise<OutlineConfirmResponse> => {
    const res = await client.post<OutlineConfirmResponse>(
      `/bid/${projectId}/outline/confirm`,
      chapters ? { chapters } : {},
    )
    return res.data
  },
}
