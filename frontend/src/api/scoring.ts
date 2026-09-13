import client from './client'

export type RubricKind = 'quality' | 'content' | 'cert' | 'personnel' | 'performance' | 'price'

export interface RubricItem {
  id: string
  dimension: string
  name: string
  points: number
  kind: RubricKind
  criteria: string
  key_terms: string[]
}

export interface ScoringRubric {
  status: 'found' | 'none' | 'manual'
  method_name: string
  max_total: number
  applied: boolean
  raw_text: string
  items: RubricItem[]
}

export interface RubricCover {
  status: string
  applied: boolean
  missing: Array<{ dimension: string; name: string }>
}

export interface ScoringReportItem {
  id: string
  dimension: string
  name: string
  points_total: number
  points_obtained: number
  status: 'pass' | 'partial' | 'fail' | 'unscored'
  evidence: string
  gap: string
  suggestion: string
  // 后端判定是否提供「自动修改」：报价项（kind=price）与无改进建议的项为 false。
  // 老报告里没有这两个字段，取不到就不显示按钮。
  kind?: string
  auto_fixable?: boolean
}

export interface AutoFixResult {
  success: boolean
  chapter_id: string
  chapter_title: string
  section_path: string[]
  section_title: string
  diff_summary: string
  modified_content: string
}

export interface ScoringReport {
  generated_at: string
  method_name: string
  max_total: number
  total: number
  scored_total: number
  unscored_note: string
  warnings?: string[]
  items: ScoringReportItem[]
}

export const scoringApi = {
  getRubric: async (projectId: string): Promise<ScoringRubric> =>
    (await client.get(`/bid/${projectId}/scoring-rubric`)).data.rubric,
  parseRubric: async (projectId: string, text: string): Promise<{ rubric: ScoringRubric; problems: string[] }> =>
    (await client.post(`/bid/${projectId}/scoring-rubric/parse`, { text })).data,
  saveRubric: async (projectId: string, rubric: ScoringRubric): Promise<{ rubric: ScoringRubric; problems: string[] }> =>
    (await client.put(`/bid/${projectId}/scoring-rubric`, { rubric })).data,
  rescore: async (projectId: string): Promise<ScoringReport> =>
    (await client.post(`/bid/${projectId}/score`)).data,
  getReport: async (projectId: string): Promise<ScoringReport | Record<string, never>> =>
    (await client.get(`/bid/${projectId}/scoring-report`)).data,
  // 按评分意见自动改写对应小节（后端写 final_content，保留 ai_generated_content 基线）
  autoFixItem: async (projectId: string, itemId: string): Promise<AutoFixResult> =>
    (await client.post(`/bid/${projectId}/scoring-items/${itemId}/auto-fix`)).data,
}