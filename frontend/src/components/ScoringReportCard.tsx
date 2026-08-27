import React from 'react'
import { Card, Tag, Button, Space, Progress, Empty, Typography, Spin } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { ScoringReport } from '../api/scoring'

const STATUS_TAG: Record<string, { color: string; label: string }> = {
  pass: { color: 'green', label: '达标' },
  partial: { color: 'orange', label: '部分达标' },
  fail: { color: 'red', label: '未达标' },
  unscored: { color: 'default', label: '未计分' },
}

interface Props {
  report: ScoringReport | null
  loading?: boolean
  onRescore?: () => void
  rescoring?: boolean
}

const ScoringReportCard: React.FC<Props> = ({ report, loading, onRescore, rescoring }) => {
  if (loading) return <Card size="small" loading style={{ marginBottom: 12 }} />
  if (!report || !report.items?.length) return null
  const ratio = report.scored_total > 0 ? Math.round((report.total / report.scored_total) * 100) : 0
  return (
    <Card
      size="small"
      title={`自我评分：${report.method_name || '综合评分'}`}
      style={{ marginBottom: 12 }}
      extra={onRescore && (
        <Button size="small" icon={<ReloadOutlined />} loading={rescoring} onClick={onRescore}>
          重新评分
        </Button>
      )}
    >
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <Space align="center">
          <Progress type="circle" size={64} percent={ratio} format={(p) => `${p ?? 0}%`} />
          <Space direction="vertical" size={0}>
            <Typography.Text strong>
              {report.total} / {report.scored_total}（可评总分）
            </Typography.Text>
            {report.max_total > 0 && (
              <Typography.Text type="secondary">
                指标满分 {report.max_total} 分
              </Typography.Text>
            )}
            {report.unscored_note && (
              <Tag color="default" style={{ marginTop: 4 }}>{report.unscored_note}</Tag>
            )}
          </Space>
        </Space>
        {report.items.map((it) => {
          const tag = STATUS_TAG[it.status] ?? STATUS_TAG.unscored
          return (
            <Card key={it.id} size="small" type="inner" style={{ marginBottom: 0 }}>
              <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                <Space wrap>
                  <Tag color={tag.color}>{tag.label}</Tag>
                  <Typography.Text strong>{it.name}</Typography.Text>
                  <Typography.Text type="secondary">{it.dimension}</Typography.Text>
                </Space>
                <Typography.Text>
                  {it.points_obtained} / {it.points_total} 分
                </Typography.Text>
              </Space>
              {it.evidence && (
                <Typography.Paragraph type="secondary" style={{ margin: '4px 0 0', fontSize: 12 }}>
                  依据：{it.evidence}
                </Typography.Paragraph>
              )}
              {it.gap && (
                <Typography.Paragraph type="warning" style={{ margin: '4px 0 0', fontSize: 12 }}>
                  失分点：{it.gap}
                </Typography.Paragraph>
              )}
              {it.suggestion && (
                <Typography.Paragraph style={{ margin: '4px 0 0', fontSize: 12 }}>
                  建议：{it.suggestion}
                </Typography.Paragraph>
              )}
            </Card>
          )
        })}
      </Space>
    </Card>
  )
}

export default ScoringReportCard