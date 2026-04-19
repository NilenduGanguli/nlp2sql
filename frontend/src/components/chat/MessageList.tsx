import React, { useRef, useEffect } from 'react'
import type { ChatMessage } from '../../types'
import { MessageBubble } from './MessageBubble'

interface MessageListProps {
  messages: ChatMessage[]
  onOpenInEditor?: (sql: string) => void
  onClarificationAnswer?: (messageId: string, answer: string) => void
  onExecuteSql?: (messageId: string, sql: string) => void
  onSelectCandidate?: (messageId: string, candidate: { id: string; interpretation: string; sql: string; explanation: string }) => void
  onAcceptQuery?: (messageId: string, sql: string, accepted: boolean) => void
  isExecutingSql?: boolean
  executedSqlMessageId?: string
}

export const MessageList: React.FC<MessageListProps> = ({
  messages,
  onOpenInEditor,
  onClarificationAnswer,
  onExecuteSql,
  onSelectCandidate,
  onAcceptQuery,
  isExecutingSql,
  executedSqlMessageId,
}) => {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
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
    <div ref={containerRef} style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 8px' }}>
      {messages.map((msg) => (
        <MessageBubble
          key={msg.id}
          message={msg}
          onOpenInEditor={onOpenInEditor}
          onClarificationAnswer={onClarificationAnswer}
          onExecuteSql={onExecuteSql}
          onSelectCandidate={onSelectCandidate}
          onAcceptQuery={onAcceptQuery}
          isExecutingSql={isExecutingSql}
          executedSqlMessageId={executedSqlMessageId}
        />
      ))}
    </div>
  )
}
