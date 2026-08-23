import React from 'react'
import { UserOutlined, RobotOutlined } from '@ant-design/icons'

interface ChatBubbleProps {
  role: 'user' | 'ai'
  text: string
  ts: number
}

const ChatBubble: React.FC<ChatBubbleProps> = ({ role, text }) => {
  const isUser = role === 'user'
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: isUser ? 'flex-end' : 'flex-start',
        marginBottom: 12,
      }}
    >
      <div
        style={{
          maxWidth: '85%',
          display: 'flex',
          flexDirection: isUser ? 'row-reverse' : 'row',
          alignItems: 'flex-start',
          gap: 8,
        }}
      >
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            background: isUser ? '#1677ff' : '#52c41a',
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          {isUser ? <UserOutlined /> : <RobotOutlined />}
        </div>
        <div
          style={{
            padding: '8px 12px',
            borderRadius: 8,
            background: isUser ? '#1677ff' : '#f6f6f6',
            color: isUser ? '#fff' : 'rgba(0,0,0,0.85)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: 14,
            lineHeight: 1.6,
          }}
        >
          {text}
        </div>
      </div>
    </div>
  )
}

export default ChatBubble
