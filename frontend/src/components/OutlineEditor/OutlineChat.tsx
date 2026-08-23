import React, { useEffect, useRef, useState } from 'react'
import { Input, Button, Space, Alert, Empty } from 'antd'
import { SendOutlined, ClearOutlined } from '@ant-design/icons'
import ChatBubble from './ChatBubble'
import { outlineApi, OutlineChapter } from '../../api/outline'

interface Message {
  role: 'user' | 'ai'
  text: string
  ts: number
}

interface OutlineChatProps {
  projectId: string
  conversationId?: string
  onConversationId: (id: string) => void
  onTreeUpdate: (next: OutlineChapter[]) => void
}

const OutlineChat: React.FC<OutlineChatProps> = ({
  projectId,
  conversationId,
  onConversationId,
  onTreeUpdate,
}) => {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'ai',
      text: '你好！我是章节结构编辑助手。你可以直接告诉我如何调整目录，例如：\n• "把第三章拆成 X 和 Y"\n• "在第二章后加一节：应急预案"\n• "合并第三和第四章"\n• "第一章节标题改成 商务投标文件"\n\n或者直接在左侧手动改名/增删。',
      ts: Date.now(),
    },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, loading])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return
    setError(null)
    const userMsg: Message = { role: 'user', text, ts: Date.now() }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setLoading(true)
    try {
      const res = await outlineApi.chat(projectId, text, conversationId)
      onTreeUpdate(res.chapters)
      if (res.conversation_id) onConversationId(res.conversation_id)
      const aiMsg: Message = {
        role: 'ai',
        text: res.reply || '已应用修改。',
        ts: Date.now(),
      }
      setMessages((prev) => [...prev, aiMsg])
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail ||
        err?.message ||
        '对话失败，请稍后再试'
      setError(detail)
      const errMsg: Message = {
        role: 'ai',
        text: `❌ ${detail}`,
        ts: Date.now(),
      }
      setMessages((prev) => [...prev, errMsg])
    } finally {
      setLoading(false)
    }
  }

  const handleClear = () => {
    setMessages([
      {
        role: 'ai',
        text: '对话历史已清空。再次提醒：手动编辑仅本地生效，AI 对话修改会保存到服务器。',
        ts: Date.now(),
      },
    ])
    onConversationId('')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ flex: 1, overflow: 'auto', padding: '8px 4px', minHeight: 0 }} ref={scrollRef}>
        {messages.length === 0 ? (
          <Empty description="开始对话修改章节结构" />
        ) : (
          messages.map((m, i) => <ChatBubble key={i} role={m.role} text={m.text} ts={m.ts} />)
        )}
        {loading && (
          <div style={{ color: '#999', fontSize: 12, padding: '4px 12px' }}>AI 正在思考…</div>
        )}
      </div>

      {error && (
        <Alert
          type="error"
          message={error}
          showIcon
          closable
          style={{ marginBottom: 8 }}
          onClose={() => setError(null)}
        />
      )}

      <Space.Compact style={{ width: '100%' }}>
        <Input.TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="告诉 AI 如何修改章节结构，例如：把第三章拆成 X 和 Y"
          autoSize={{ minRows: 2, maxRows: 5 }}
          disabled={loading}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          loading={loading}
          onClick={handleSend}
          disabled={!input.trim()}
          style={{ height: 'auto' }}
        >
          发送
        </Button>
      </Space.Compact>

      <Space style={{ marginTop: 8, justifyContent: 'space-between', width: '100%' }}>
        <span style={{ color: '#999', fontSize: 12 }}>
          {conversationId ? `会话已绑定 #${conversationId.slice(0, 8)}` : '尚未开始多轮对话'}
        </span>
        <Button size="small" icon={<ClearOutlined />} onClick={handleClear}>
          清空对话
        </Button>
      </Space>
    </div>
  )
}

export default OutlineChat
