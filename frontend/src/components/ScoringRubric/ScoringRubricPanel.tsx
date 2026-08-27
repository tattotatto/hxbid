import React, { useEffect, useState } from 'react'
import { Card, Button, Empty, Input, InputNumber, Table, Tag, Space, message as antMessage, Typography } from 'antd'
import { scoringApi, ScoringRubric } from '../../api/scoring'

const KIND_LABELS: Record<string, string> = {
  content: '内容', cert: '资质', personnel: '人员', performance: '业绩',
  quality: '质量', price: '报价',
}

interface Props {
  projectId: string
}

/** 评分办法面板：found → 可编辑指标列表；none → 粘贴入口；manual → 可编辑 */
const ScoringRubricPanel: React.FC<Props> = ({ projectId }) => {
  const [rubric, setRubric] = useState<ScoringRubric | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [parsing, setParsing] = useState(false)

  const load = () => {
    setLoading(true)
    scoringApi.getRubric(projectId).then(setRubric).catch(() => setRubric(null)).finally(() => setLoading(false))
  }
  useEffect(load, [projectId])

  const handleParse = async () => {
    if (!pasteText.trim()) return
    setParsing(true)
    try {
      const { rubric: next, problems } = await scoringApi.parseRubric(projectId, pasteText)
      setRubric(next)
      problems.forEach((p) => antMessage.warning(p))
      antMessage.success(next.status === 'found' ? '已提取评分指标，可核对修改' : '未检测到评分表，可手动填写')
    } catch (err: any) {
      antMessage.error(err?.response?.data?.detail || '解析失败')
    } finally {
      setParsing(false)
    }
  }

  const handleSave = async () => {
    if (!rubric) return
    setSaving(true)
    try {
      const { rubric: next, problems } = await scoringApi.saveRubric(projectId, rubric)
      setRubric(next)
      problems.forEach((p) => antMessage.warning(p))
      antMessage.success('评分指标已保存')
    } catch (err: any) {
      antMessage.error(err?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const updateItem = (index: number, patch: Partial<ScoringRubric['items'][number]>) => {
    if (!rubric) return
    setRubric({ ...rubric, items: rubric.items.map((it, i) => (i === index ? { ...it, ...patch } : it)) })
  }

  if (loading) return <Card size="small" loading />
  if (!rubric || rubric.status === 'none') {
    return (
      <Card size="small" title="评分办法" style={{ marginBottom: 12 }}>
        <Typography.Text type="secondary">
          未检测到评标办法。可粘贴招标文件中的评分办法文本由 AI 提取，或直接手动填写评分指标。
        </Typography.Text>
        <Input.TextArea
          rows={4} value={pasteText} onChange={(e) => setPasteText(e.target.value)}
          placeholder={'粘贴评分表文本，例如：\n技术部分 45 分……'}
          style={{ margin: '8px 0' }}
        />
        <Button type="primary" size="small" loading={parsing} onClick={handleParse}>解析评分办法</Button>
      </Card>
    )
  }

  return (
    <Card size="small" title={`评分办法（${rubric.method_name || rubric.status}）`} style={{ marginBottom: 12 }}
      extra={<Button size="small" onClick={handleSave} loading={saving}>保存指标</Button>}>
      {rubric.items.length === 0 ? (
        <Empty description="暂无评分指标" />
      ) : (
        <Table
          size="small" rowKey="id" pagination={false} dataSource={rubric.items}
          columns={[
            { title: '维度', dataIndex: 'dimension', width: 100 },
            {
              title: '指标', dataIndex: 'name', render: (v, _, i) => (
                <Input size="small" value={v} onChange={(e) => updateItem(i, { name: e.target.value })} />
              ),
            },
            {
              title: '分值', dataIndex: 'points', width: 80, render: (v, _, i) => (
                <InputNumber size="small" min={0} value={v} onChange={(n) => updateItem(i, { points: Number(n) || 0 })} />
              ),
            },
            { title: '类型', dataIndex: 'kind', width: 80, render: (v: string) => <Tag>{KIND_LABELS[v] ?? v}</Tag> },
            {
              title: '给分标准', dataIndex: 'criteria', render: (v, _, i) => (
                <Input.TextArea size="small" rows={1} value={v} onChange={(e) => updateItem(i, { criteria: e.target.value })} />
              ),
            },
          ]}
        />
      )}
    </Card>
  )
}

export default ScoringRubricPanel