import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Card, Button, Space, Tag, Spin, Empty, message as antMessage } from 'antd'
import { ArrowLeftOutlined, CheckCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import OutlineTree from '../../components/OutlineEditor/OutlineTree'
import OutlineChat from '../../components/OutlineEditor/OutlineChat'
import MatchDrawer from '../../components/OutlineEditor/MatchDrawer'
import ScoringRubricPanel from '../../components/ScoringRubric/ScoringRubricPanel'
import type { RubricCover } from '../../api/scoring'
import {
  outlineApi,
  getNodeByPath,
  updateNodeByPath,
  type ChapterType,
  type OutlineChapter,
  type OutlineMatchCandidate,
  type OutlineMatchResult,
} from '../../api/outline'

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

  // 匹配抽屉
  const [matchOpen, setMatchOpen] = useState(false)
  const [matchLoading, setMatchLoading] = useState(false)
  const [matchResult, setMatchResult] = useState<OutlineMatchResult | null>(null)
  const [matchPath, setMatchPath] = useState<number[] | null>(null)

  const saveTimer = useRef<number | null>(null)
  const matchTimer = useRef<number | null>(null)

  /** 保存防抖：拖拽/改名/改类型/挂附件后都会调它 */
  const scheduleSave = (next: OutlineChapter[]) => {
    if (!id) return
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      outlineApi.saveStructure(id, next).catch((err: any) => {
        antMessage.error(err?.response?.data?.detail || '章节结构保存失败')
      })
    }, 800)
  }

  const applyTree = (next: OutlineChapter[]) => {
    setChapters(next)
    scheduleSave(next)
  }

  /** 重新匹配（防抖 500ms）。只有固定格式/表格需要匹配 */
  const requestMatch = (path: number[], title: string, type: ChapterType) => {
    if (!id || (type !== 'fixed_form' && type !== 'table')) return
    if (matchTimer.current) window.clearTimeout(matchTimer.current)
    matchTimer.current = window.setTimeout(async () => {
      setMatchLoading(true)
      try {
        const res = await outlineApi.match(id, title, type)
        setChapters((prev) => {
          // 防抖窗口内可能发生拖拽/删除，此时下标路径已指向**别的章节**。
          // 不校验就会把 A 的原文区间冻到 B 上 —— 那是错误原文进标书。
          const node = getNodeByPath(prev, path)
          if (!node || node.title !== title) return prev
          const next = updateNodeByPath(prev, path, (n) => ({ ...n, match: res }))
          scheduleSave(next)
          return next
        })
      } catch {
        /* 匹配失败不打断编辑；徽标沿用旧值 */
      } finally {
        setMatchLoading(false)
      }
    }, 500)
  }

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
    // 确认会把项目推进到 collecting，此后任何迟到的 PUT 都会拿到 409
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    setConfirming(true)
    try {
      const res = await outlineApi.confirm(id, chapters)
      const msg = `已确认 ${res.chapters_count} 个章节，进入信息搜集阶段`
      antMessage.success(
        res.added_from_rubric?.length
          ? `${msg}；已按评标办法自动补充：${res.added_from_rubric.join('、')}`
          : msg,
      )
      // 附件被静默剔除过就得说一声——不说他会到生成时才发现那一页是空的
      if (res.pruned_attachments?.length) {
        antMessage.warning(
          `有 ${res.pruned_attachments.length} 个附件已被剔除（资源库行已删除、路径失效或与其它章节重复）：` +
            res.pruned_attachments.join('、'),
        )
      }
      if (id) sessionStorage.removeItem(`outline_conv_${id}`)
      navigate(`/projects/${id}`)
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || '确认失败'
      antMessage.error(detail)
    } finally {
      setConfirming(false)
    }
  }

  /**
   * 固定格式/表格章节的匹配概况.
   *
   * 徽标是逐章的，但用户可能一屏几十个红点却没意识到这意味着什么 ——
   * 这里给一句整体说明。确认时**有意不拦截**（用户裁定），所以这句话
   * 就是唯一的聚合告知渠道。
   */
  const matchStats = useMemo(() => {
    let fixedTotal = 0
    let unmatched = 0
    let noCorpus = false
    const walk = (nodes: OutlineChapter[]) => {
      for (const n of nodes) {
        const t = (n.type ?? 'ai_generated') as ChapterType
        if (t === 'fixed_form' || t === 'table') {
          fixedTotal += 1
          const s = n.match?.status
          // 多候选但用户没点选 → 生成时会退 AI，也算"未定"
          if (s === 'missing' || (s === 'ambiguous' && n.match?.picked !== 'manual')) {
            unmatched += 1
          }
          if (n.match && n.match.corpus_available === false) noCorpus = true
        }
        if (n.children?.length) walk(n.children)
      }
    }
    walk(chapters)
    return { fixedTotal, unmatched, noCorpus }
  }, [chapters])

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

      {matchStats.unmatched > 0 && (
        <Alert
          type={matchStats.noCorpus ? 'error' : 'warning'}
          showIcon
          style={{ marginBottom: 12 }}
          message={`${matchStats.unmatched} / ${matchStats.fixedTotal} 个固定格式/表格章节未匹配到招标原文`}
          description={
            matchStats.noCorpus
              ? '本次没能从招标文件里解析出可匹配的文本（可能是扫描件 PDF，没有文本层），' +
                '这些章节生成时将由 AI 撰写。改标题无济于事 —— 需要换一份带文本层的招标文件，' +
                '或先把格式章节的内容人工准备好。'
              : '这些章节生成时将由 AI 撰写，不会照抄招标原文。点章节上的红色「未匹配」徽标' +
                '可以看到具体原因与候选，或改写标题。'
          }
        />
      )}

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
            <OutlineTree
              chapters={chapters}
              onChange={applyTree}
              onMatchRequest={requestMatch}
              onOpenMatch={(path) => {
                setMatchPath(path)
                setMatchResult(getNodeByPath(chapters, path)?.match ?? null)
                setMatchOpen(true)
              }}
            />
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

      <MatchDrawer
        open={matchOpen}
        loading={matchLoading}
        projectId={id!}
        title={matchPath ? getNodeByPath(chapters, matchPath)?.title ?? '' : ''}
        result={matchResult}
        attachments={matchPath ? getNodeByPath(chapters, matchPath)?.attachments ?? [] : []}
        onAttachmentsChange={(next) => {
          if (!matchPath) return
          applyTree(
            updateNodeByPath(chapters, matchPath, (n) => ({ ...n, attachments: next })),
          )
        }}
        onPick={(c: OutlineMatchCandidate) => {
          if (!matchPath) return
          const next = updateNodeByPath(chapters, matchPath, (n) => ({
            ...n,
            match: {
              ...(n.match as OutlineMatchResult),
              status: 'matched',
              picked: 'manual',
              // 扁平记录必须同步 —— 生成阶段照这些键切片，只改 best 是无效的
              matched_title: c.title,
              page: c.page,
              start: c.start,
              end: c.end,
              score: c.score,
              // candidates 留空表示"已定"
              best: c,
              candidates: [],
            },
          }))
          applyTree(next)
          setMatchResult(getNodeByPath(next, matchPath)?.match ?? null)
          setMatchOpen(false)
        }}
        onClose={() => setMatchOpen(false)}
      />
    </div>
  )
}

export default OutlineConfirm
