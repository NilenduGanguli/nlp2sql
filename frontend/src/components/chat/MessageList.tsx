import React, { useRef, useEffect } from 'react'
import type { ChatMessage } from '../../types'
import { MessageBubble } from './MessageBubble'
import { useChatStore } from '../../store/chatStore'

interface MessageListProps {
  messages: ChatMessage[]
  onOpenInEditor?: (sql: string) => void
  onClarificationAnswer?: (messageId: string, answer: string) => void
  onExecuteSql?: (messageId: string, sql: string) => void
  onAcceptCandidates?: (
    messageId: string,
    accepted: Array<{ id: string; interpretation: string; sql: string; explanation: string }>,
    rejected: Array<{ id: string; interpretation: string; sql: string; explanation: string }>,
    executedId: string,
  ) => void
  onAcceptQuery?: (messageId: string, sql: string, accepted: boolean) => void
  isExecutingSql?: boolean
  executedSqlMessageId?: string
}

export const MessageList: React.FC<MessageListProps> = ({
  messages,
  onOpenInEditor,
  onClarificationAnswer,
  onExecuteSql,
  onAcceptCandidates,
  onAcceptQuery,
  isExecutingSql,
  executedSqlMessageId,
}) => {
  const containerRef = useRef<HTMLDivElement>(null)
  const setZeroRowsState = useChatStore((s) => s.setZeroRowsState)

  useEffect(() => {
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  useEffect(() => {
    const last = messages[messages.length - 1]
    const result = last?.result
    if (result && result.total_rows === 0 && result.sql) {
      setZeroRowsState({ ts: Date.now(), sql: result.sql })
    }
  }, [messages, setZeroRowsState])

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
          onAcceptCandidates={onAcceptCandidates}
          onAcceptQuery={onAcceptQuery}
          isExecutingSql={isExecutingSql}
          executedSqlMessageId={executedSqlMessageId}
        />
      ))}
    </div>
  )
}
