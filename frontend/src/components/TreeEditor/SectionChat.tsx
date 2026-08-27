import { useEffect, useRef, useState } from 'react'
import { Button, Input, message, Space, Typography } from 'antd'
import client from '../../api/client'

interface Props {
  projectId: string
  chapterId: string
  sectionPath: string[]       // 当前节路径
  currentContent: string      // 当前编辑器内容（每次发送时实时取）
  onApplyContent: (content: string) => void  // 应用修改：走 diff 预览
}

interface Msg { role: 'user' | 'assistant'; content: string }

export default function SectionChat({ projectId, chapterId, sectionPath, currentContent, onApplyContent }: Props) {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [revisedContent, setRevisedContent] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setMessages([])
    setRevisedContent(null)
    setInput('')
  }, [chapterId, JSON.stringify(sectionPath)])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, revisedContent])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    const next: Msg[] = [...messages, { role: 'user', content: text }]
    setMessages(next)
    setLoading(true)
    try {
      const res = await client.post(
        `/bid/${projectId}/chapters/${chapterId}/sections/chat`,
        {
          section_path: sectionPath,
          current_content: currentContent,
          messages: next,
          instruction: text,
        }
      )
      setMessages([...next, { role: 'assistant', content: res.data.reply || '（无回复）' }])
      if (res.data.revised_content && res.data.revised_content !== currentContent) {
        setRevisedContent(res.data.revised_content)
      } else {
        setRevisedContent(null)
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '对话失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ marginTop: 12, borderTop: '1px solid #f0f0f0', paddingTop: 12 }}>
      <Typography.Text strong>AI 对话修正本节</Typography.Text>
      <div ref={scrollRef} style={{ maxHeight: 240, overflowY: 'auto', marginTop: 8 }}>
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: 8, textAlign: m.role === 'user' ? 'right' : 'left' }}>
            <div
              style={{
                display: 'inline-block', maxWidth: '85%', padding: '6px 10px', borderRadius: 8,
                background: m.role === 'user' ? '#e6f4ff' : '#f5f5f5', whiteSpace: 'pre-wrap',
                textAlign: 'left',
              }}
            >
              {m.content}
            </div>
          </div>
        ))}
        {revisedContent && (
          <div style={{ marginTop: 8 }}>
            <Button
              type="primary" size="small"
              onClick={() => { onApplyContent(revisedContent); setRevisedContent(null) }}
            >
              应用修改（预览 diff）
            </Button>
          </div>
        )}
      </div>
      <Space.Compact style={{ marginTop: 8, width: '100%' }}>
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入修改意见，可与 AI 多轮对话…"
          onPressEnter={handleSend}
          disabled={loading}
        />
        <Button type="primary" onClick={handleSend} loading={loading}>发送</Button>
      </Space.Compact>
    </div>
  )
}
