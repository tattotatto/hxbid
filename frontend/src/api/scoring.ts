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
}