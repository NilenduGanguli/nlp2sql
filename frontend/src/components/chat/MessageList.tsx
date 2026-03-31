import React, { useRef, useEffect } from 'react'
import type { ChatMessage } from '../../types'
import { MessageBubble } from './MessageBubble'

interface MessageListProps {
  messages: ChatMessage[]
  onOpenInEditor?: (sql: string) => void
}

export const MessageList: React.FC<MessageListProps> = ({ messages, onOpenInEditor }) => {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#5a5a7a',
          fontSize: 14,
        }}
      >
        Ask a question about your database in natural language
      </div>
    )
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 8px' }}>
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} onOpenInEditor={onOpenInEditor} />
      ))}
      <div ref={bottomRef} />
    </div>
  )
}
