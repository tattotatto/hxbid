import client from './client'
import type { RubricCover } from './scoring'

export type ChapterType = 'fixed_form' | 'table' | 'ai_generated' | 'attachment' | 'mixed'

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

  /** 新增：唯一的「目录确认门」出口，物化 ProjectChapter + status -> collecting */
  confirm: async (projectId: string): Promise<OutlineConfirmResponse> => {
    const res = await client.post<OutlineConfirmResponse>(`/bid/${projectId}/outline/confirm`)
    return res.data
  },
}
