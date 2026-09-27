import React from 'react'
import { Drawer, Space, Tag, Alert, List, Typography, Empty, Spin } from 'antd'
import type { OutlineMatchCandidate, OutlineMatchResult } from '../../api/outline'

interface MatchDrawerProps {
  open: boolean
  loading: boolean
  title: string
  result: OutlineMatchResult | null
  onPick: (c: OutlineMatchCandidate) => void
  onClose: () => void
}

const STATUS_META: Record<string, { color: string; text: string }> = {
  matched: { color: 'success', text: '已匹配' },
  ambiguous: { color: 'warning', text: '多个候选' },
  missing: { color: 'error', text: '未匹配' },
  na: { color: 'default', text: '不适用（AI 生成）' },
}

const SOURCE_LABEL: Record<string, string> = {
  format_section: '投标文件格式章节',
  full_text: '招标文件全文',
  table: '招标文件表格',
}

const MatchDrawer: React.FC<MatchDrawerProps> = ({
  open,
  loading,
  title,
  result,
  onPick,
  onClose,
}) => {
  const meta = result ? STATUS_META[result.status] ?? STATUS_META.missing : null

  return (
    <Drawer
      title={`「${title}」的原文匹配`}
      open={open}
      onClose={onClose}
      width={560}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin />
        </div>
      ) : !result ? (
        <Empty description="尚未匹配" />
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Space wrap>
            <Tag color={meta!.color}>{meta!.text}</Tag>
            {result.source && <Tag>{SOURCE_LABEL[result.source] ?? result.source}</Tag>}
            {result.best?.page != null && <Tag color="blue">第 {result.best.page} 页</Tag>}
            {result.picked === 'manual' && <Tag color="cyan">已手动指定</Tag>}
          </Space>

          {result.status === 'missing' && (
            <Alert
              type="error"
              showIcon
              message="招标文件里没找到这一节"
              description="该章节不会照抄招标原文，将由 AI 撰写。建议改标题、改类型，或从下方候选中选一个。"
            />
          )}
          {result.status === 'ambiguous' && (
            <Alert
              type="warning"
              showIcon
              message="有多个候选，请确认用哪一个"
              description="点选下面任意一条即固化该选择，生成时就用它。"
            />
          )}

          {(result.best || result.table_preview) && (
            <>
              <Typography.Text strong>
                {result.best ? `当前命中：${result.best.raw}` : '命中的招标表格'}
              </Typography.Text>
              <div
                style={{
                  whiteSpace: 'pre-wrap',
                  maxHeight: 320,
                  overflow: 'auto',
                  background: '#fafafa',
                  border: '1px solid #f0f0f0',
                  borderRadius: 6,
                  padding: 12,
                  fontSize: 12,
                }}
              >
                {result.best ? result.best.preview : result.table_preview}
                {result.best?.preview_truncated && (
                  <div style={{ color: '#999', marginTop: 8 }}>
                    （预览已截断，生成时使用完整原文）
                  </div>
                )}
              </div>
            </>
          )}

          {result.candidates.length > 0 && (
            <List
              size="small"
              header={`候选（${result.candidates.length}）`}
              dataSource={result.candidates}
              renderItem={(c) => (
                <List.Item
                  style={{ cursor: 'pointer' }}
                  onClick={() => onPick(c)}
                  actions={[
                    <Typography.Text type="secondary" key="s">
                      {c.score.toFixed(2)}
                    </Typography.Text>,
                  ]}
                >
                  <Space>
                    <Tag>层级 {c.level}</Tag>
                    <span>{c.raw}</span>
                    {c.page != null && <Tag color="blue">第 {c.page} 页</Tag>}
                  </Space>
                </List.Item>
              )}
            />
          )}
        </Space>
      )}
    </Drawer>
  )
}

export default MatchDrawer
