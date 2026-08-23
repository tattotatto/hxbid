import React, { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Card, Button, Space, Tag, Spin, message as antMessage } from 'antd'
import { ArrowLeftOutlined, CheckCircleOutlined } from '@ant-design/icons'
import OutlineTree from '../../components/OutlineEditor/OutlineTree'
import OutlineChat from '../../components/OutlineEditor/OutlineChat'
import { outlineApi, OutlineChapter } from '../../api/outline'

const OutlineConfirm: React.FC = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [chapters, setChapters] = useState<OutlineChapter[]>([])
  const [loading, setLoading] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [convId, setConvId] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    setError(null)
    outlineApi
      .get(id)
      .then((res) => {
        setChapters(res.chapters)
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

  const handleConfirm = async () => {
    if (!id || confirming) return
    setConfirming(true)
    try {
      const res = await outlineApi.confirm(id)
      antMessage.success(`已确认 ${res.chapters_count} 个章节，进入信息搜集阶段`)
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
          ) : error ? (
            <div style={{ color: '#cf1322', padding: 16 }}>{error}</div>
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
