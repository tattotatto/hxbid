import React, { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Card, Button, Space, Tag, Spin, Empty, message as antMessage } from 'antd'
import { ArrowLeftOutlined, CheckCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import OutlineTree from '../../components/OutlineEditor/OutlineTree'
import OutlineChat from '../../components/OutlineEditor/OutlineChat'
import ScoringRubricPanel from '../../components/ScoringRubric/ScoringRubricPanel'
import type { RubricCover } from '../../api/scoring'
import { outlineApi, OutlineChapter } from '../../api/outline'

const OutlineConfirm: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [chapters, setChapters] = useState<OutlineChapter[]>([])
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [convId, setConvId] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [rubricCover, setRubricCover] = useState<RubricCover | null>(null)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    outlineApi
      .get(id)
      .then((res) => {
        setChapters(res.chapters)
        setRubricCover(res.rubric_cover ?? null)
      })
      .catch((err: any) => {
        const detail = err?.response?.data?.detail || err?.message || '加载章节失败'
        setError(detail)
      })
      .finally(() => setLoading(false))

    const stored = sessionStorage.getItem(`outline_conv_${id}`)
    if (stored) setConvId(stored)
  }, [id])

  const persistConv = (next: string) => {
    setConvId(next || undefined)
    if (id) {
      if (next) sessionStorage.setItem(`outline_conv_${id}`, next)
      else sessionStorage.removeItem(`outline_conv_${id}`)
    }
  }

  const handleReextract = async () => {
    if (!id || extracting) return
    setExtracting(true)
    try {
      await outlineApi.extract(id)
      const res = await outlineApi.get(id)
      setChapters(res.chapters)
      setRubricCover(res.rubric_cover ?? null)
      setError(null)
      antMessage.success(`已提取 ${res.chapters.length} 个章节`)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || '重新提取失败'
      antMessage.error(detail)
    } finally {
      setExtracting(false)
    }
  }

  const handleConfirm = async () => {
    if (!id || confirming) return
    setConfirming(true)
    try {
      const res = await outlineApi.confirm(id)
      const msg = `已确认 ${res.chapters_count} 个章节，进入信息搜集阶段`
      antMessage.success(
        res.added_from_rubric?.length
          ? `${msg}；已按评标办法自动补充：${res.added_from_rubric.join('、')}`
          : msg,
      )
      if (id) sessionStorage.removeItem(`outline_conv_${id}`)
      navigate(`/projects/${id}`)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || '确认失败'
      antMessage.error(detail)
    } finally {
      setConfirming(false)
    }
  }

  // 总节点数（含子章节）用于显示
  const totalNodes = (function count(nodes: OutlineChapter[]): number {
    let n = 0
    for (const nd of nodes) {
      n += 1
      if (nd.children) n += count(nd.children)
    }
    return n
  })(chapters)

  return (
    <div style={{ padding: 16, height: 'calc(100vh - 64px)', display: 'flex', flexDirection: 'column' }}>
      <Card style={{ marginBottom: 12 }} bodyStyle={{ padding: '12px 16px' }}>
        <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
          <Space size="middle">
            <h2 style={{ margin: 0 }}>目录确认</h2>
            <Tag color="blue">{chapters.length} 个顶级章节</Tag>
            <Tag>{totalNodes} 个总节点</Tag>
            <Tag color="orange">请审阅 AI 提取的章节结构</Tag>
          </Space>
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/projects/${id}`)}>
              返回项目
            </Button>
            <Button
              type="primary"
              icon={<CheckCircleOutlined />}
              loading={confirming}
              onClick={handleConfirm}
              disabled={loading || chapters.length === 0}
            >
              确认并继续
            </Button>
          </Space>
        </Space>
      </Card>

      <ScoringRubricPanel projectId={id!} />
      {rubricCover && rubricCover.missing.length > 0 && (
        rubricCover.applied ? (
          <Card size="small" style={{ marginBottom: 12 }}>
            <Tag color="default">评标办法</Tag>
            已按评标办法补充过目录节点；修改评标办法后可重新确认
          </Card>
        ) : (
          <Card size="small" style={{ marginBottom: 12, borderColor: '#faad14' }}>
            <Tag color="gold">评标办法覆盖</Tag>
            确认目录时将自动补充以下缺失内容项（可改可删）：
            <Space wrap style={{ marginTop: 4 }}>
              {rubricCover.missing.map((m, i) => (
                <Tag key={i} color="gold">{m.name}</Tag>
              ))}
            </Space>
          </Card>
        )
      )}

      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 12,
        }}
      >
        <Card title="章节结构（可手动改名 / 增删）" bodyStyle={{ padding: 12, height: '100%' }}>
          {loading ? (
            <div style={{ textAlign: 'center', padding: 48 }}>
              <Spin />
            </div>
          ) : chapters.length === 0 ? (
            <div style={{ textAlign: 'center', padding: 32 }}>
              <Empty description={error || '尚未提取到章节结构，请重新提取'} />
              <Button
                type="primary"
                icon={<ReloadOutlined />}
                loading={extracting}
                onClick={handleReextract}
                style={{ marginTop: 16 }}
              >
                重新提取章节
              </Button>
              <div style={{ marginTop: 12, color: '#999', fontSize: 13 }}>
                {extracting ? 'AI 正在解析章节结构，可能需要几分钟，请耐心等待…' : 'AI 提取可能需要几分钟'}
              </div>
            </div>
          ) : (
            <OutlineTree chapters={chapters} onChange={setChapters} />
          )}
        </Card>

        <Card
          title="AI 对话修改（自动落库）"
          bodyStyle={{ padding: 12, height: '100%' }}
        >
          <OutlineChat
            projectId={id!}
            conversationId={convId}
            onConversationId={persistConv}
            onTreeUpdate={setChapters}
          />
        </Card>
      </div>
    </div>
  )
}

export default OutlineConfirm
