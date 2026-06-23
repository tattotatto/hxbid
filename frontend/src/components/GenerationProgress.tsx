import { Progress, Tag, Space, Tooltip } from 'antd'
import {
  FileSearchOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  AlertOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons'
import type { ReactNode } from 'react'

interface Chapter {
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

interface AiTraceScores {
  empty_phrase: number
  anchor: number
  repetition: number
  overall: number
}

interface AiTraceInfo {
  verdict: string
  scores: AiTraceScores
  empty_phrase_count: number
  anchor_count: number
  anchor_gaps: Array<{ paragraph_index: number; preview: string }>
  repetitive_openings: Array<{ pattern: string; count: number }>
  empty_phrases: Array<{ phrase: string; count: number }>
}

interface GenerationProgressProps {
  chapters: Chapter[]
  currentChapter: string
  completed: number
  total: number
  ragSources?: Record<string, RagSourceInfo>
  aiTraces?: Record<string, AiTraceInfo>
}

const tagColor = (
  chapter: Chapter,
  currentChapter: string,
): string => {
  if (chapter.status === 'generated') return 'success'
  if (chapter.id === currentChapter) return 'processing'
  return 'default'
}

const verdictConfig = (score: number) => {
  if (score < 30) return { color: '#52c41a', icon: <CheckCircleOutlined />, label: '干净' }
  if (score < 60) return { color: '#faad14', icon: <ExclamationCircleOutlined />, label: '可接受' }
  if (score < 80) return { color: '#fa8c16', icon: <ExclamationCircleOutlined />, label: '需修正' }
  return { color: '#ff4d4f', icon: <CloseCircleOutlined />, label: '严重' }
}

export default function GenerationProgress({
  chapters,
  currentChapter,
  completed,
  total,
  ragSources = {},
  aiTraces = {},
}: GenerationProgressProps): ReactNode {
  const percent = total === 0 ? 0 : Math.round((completed / total) * 100)

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ marginBottom: 16 }}>
        <Progress
          percent={percent}
          status={percent >= 100 ? 'success' : 'active'}
          format={() => `${completed} / ${total}`}
        />
      </div>
      <Space wrap>
        {chapters.map((ch) => {
          const sources = ragSources[ch.id]
          const trace = aiTraces[ch.id]
          const hasSources = sources && (
            sources.similar_count > 0 ||
            sources.qual_count > 0 ||
            sources.personnel_count > 0
          )
          const hasTrace = trace && trace.scores

          // Build tooltip content
          const tooltipParts: string[] = []
          if (sources && hasSources) {
            if (sources.similar_count > 0) {
              tooltipParts.push(`📄 相似章节：${sources.similar_count} 篇`)
              if (sources.similar_titles && sources.similar_titles.length > 0) {
                for (const t of sources.similar_titles) {
                  tooltipParts.push(`  · ${t}`)
                }
              }
            }
            if (sources.qual_count > 0) {
              tooltipParts.push(`📋 匹配资质：${sources.qual_count} 项`)
            }
            if (sources.personnel_count > 0) {
              tooltipParts.push(`👤 匹配人员：${sources.personnel_count} 人`)
            }
          }
          if (hasTrace) {
            if (tooltipParts.length > 0) tooltipParts.push('')
            const v = verdictConfig(trace.scores.overall)
            tooltipParts.push(`🔍 AI痕迹评分：${trace.scores.overall} (${v.label})`)
            tooltipParts.push(`  空泛词：${trace.empty_phrase_count}处 / 锚点：${trace.anchor_count}个`)
            if (trace.anchor_gaps.length > 0) {
              tooltipParts.push(`  ⚠ ${trace.anchor_gaps.length}个段落缺少具体锚点`)
            }
            if (trace.repetitive_openings.length > 0) {
              tooltipParts.push(`  ⚠ ${trace.repetitive_openings.length}处句式重复`)
            }
            if (trace.empty_phrases.length > 0) {
              tooltipParts.push('  发现的空泛词：')
              for (const ep of trace.empty_phrases.slice(0, 5)) {
                tooltipParts.push(`    · "${ep.phrase}" ×${ep.count}`)
              }
            }
          }

          const content = (
            <>
              {ch.title}
              {sources && hasSources && (
                <span style={{ marginLeft: 6, opacity: 0.7, fontSize: 11 }}>
                  <FileSearchOutlined style={{ marginRight: 2 }} />
                  {sources.similar_count + sources.qual_count + sources.personnel_count}
                </span>
              )}
              {hasTrace && (
                <span
                  style={{
                    marginLeft: 4,
                    fontSize: 11,
                    color: verdictConfig(trace.scores.overall).color,
                    fontWeight: 'bold',
                  }}
                >
                  <AlertOutlined style={{ marginRight: 1 }} />
                  {trace.scores.overall}
                </span>
              )}
            </>
          )

          if (tooltipParts.length === 0) {
            return (
              <Tag key={ch.id} color={tagColor(ch, currentChapter)}>
                {content}
              </Tag>
            )
          }

          return (
            <Tooltip
              key={ch.id}
              title={
                <div style={{ whiteSpace: 'pre-line', maxWidth: 350 }}>
                  {tooltipParts.join('\n')}
                </div>
              }
            >
              <Tag color={tagColor(ch, currentChapter)} style={{ cursor: 'help' }}>
                {content}
              </Tag>
            </Tooltip>
          )
        })}
      </Space>

      {/* Summary row */}
      {chapters.length > 0 && (
        <div style={{ marginTop: 12, fontSize: 12, color: '#888' }}>
          <Space size="middle">
            {(() => {
              let totalSimilar = 0; let totalQual = 0; let totalPersonnel = 0
              for (const ch of chapters) {
                const s = ragSources[ch.id]
                if (s) {
                  totalSimilar += s.similar_count
                  totalQual += s.qual_count
                  totalPersonnel += s.personnel_count
                }
              }
              if (totalSimilar === 0 && totalQual === 0 && totalPersonnel === 0) return null
              return (
                <>
                  {totalSimilar > 0 && <span><FileSearchOutlined /> 相似章节 {totalSimilar}</span>}
                  {totalQual > 0 && <span><SafetyCertificateOutlined /> 资质 {totalQual}</span>}
                  {totalPersonnel > 0 && <span><TeamOutlined /> 人员 {totalPersonnel}</span>}
                </>
              )
            })()}
            {(() => {
              let totalScore = 0; let count = 0
              for (const ch of chapters) {
                const t = aiTraces[ch.id]
                if (t?.scores) { totalScore += t.scores.overall; count++ }
              }
              if (count === 0) return null
              const avg = Math.round(totalScore / count)
              const v = verdictConfig(avg)
              return (
                <span style={{ color: v.color, fontWeight: 'bold' }}>
                  {v.icon} AI痕迹均分 {avg} ({v.label})
                </span>
              )
            })()}
          </Space>
        </div>
      )}
    </div>
  )
}
