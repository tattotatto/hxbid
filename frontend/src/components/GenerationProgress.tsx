import { Progress, Tag, Space } from 'antd'
import type { ReactNode } from 'react'

interface Chapter {
  id: string
  title: string
  status: string
}

interface GenerationProgressProps {
  chapters: Chapter[]
  currentChapter: string
  completed: number
  total: number
}

const tagColor = (
  chapter: Chapter,
  currentChapter: string,
): string => {
  if (chapter.status === 'generated') return 'success'
  if (chapter.id === currentChapter) return 'processing'
  return 'default'
}

export default function GenerationProgress({
  chapters,
  currentChapter,
  completed,
  total,
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
        {chapters.map((ch) => (
          <Tag key={ch.id} color={tagColor(ch, currentChapter)}>
            {ch.title}
          </Tag>
        ))}
      </Space>
    </div>
  )
}
