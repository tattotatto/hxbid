import client from './client'

export type ChapterType = 'fixed_form' | 'table' | 'ai_generated' | 'attachment' | 'mixed'

export interface OutlineChapter {
  order_index: number
  number: string
  title: string
  type: ChapterType
  required?: boolean
  format_notes?: string
  scoring_context?: string
  table_columns?: string[]
  children?: OutlineChapter[]
}

export interface OutlineGetResponse {
  chapters: OutlineChapter[]
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
}

export const outlineApi = {
  /** 从 GET /bid/{pid}/chapters 提取顶层 chapter_structure_json 树 */
  get: async (projectId: string): Promise<OutlineGetResponse> => {
    const res = await client.get<{ chapters?: OutlineChapter[] }>(`/bid/${projectId}/chapters`)
    // 兼容旧返回：可能是 {chapters: [...]} 也可能直接是数组
    const data = res.data as any
    const chapters: OutlineChapter[] = Array.isArray(data) ? data : (data?.chapters ?? [])
    return { chapters }
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
