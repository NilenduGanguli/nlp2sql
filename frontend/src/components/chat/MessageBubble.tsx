import React from 'react'
import type { ChatMessage } from '../../types'
import { SqlResultCard } from './SqlResultCard'
import { SqlPreviewCard } from './SqlPreviewCard'
import { ClarificationCard } from './ClarificationCard'
import { SqlCandidatesPicker } from './SqlCandidatesPicker'
import { KycAutoAnswerBadge } from './KycAutoAnswerBadge'

interface MessageBubbleProps {
  message: ChatMessage
  onOpenInEditor?: (sql: string) => void
  onClarificationAnswer?: (messageId: string, answer: string) => void
  onExecuteSql?: (messageId: string, sql: string) => void
  onSelectCandidate?: (messageId: string, candidate: { id: string; interpretation: string; sql: string; explanation: string }) => void
  onAcceptQuery?: (messageId: string, sql: string, accepted: boolean) => void
  isExecutingSql?: boolean
  executedSqlMessageId?: string
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  message,
  onOpenInEditor,
  onClarificationAnswer,
  onExecuteSql,
  onSelectCandidate,
  onAcceptQuery,
  isExecutingSql = false,
  executedSqlMessageId,
}) => {
  if (message.type === 'user') {
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <div
          style={{
            maxWidth: '75%',
            padding: '10px 14px',
            background: 'rgba(124,106,247,0.2)',
            border: '1px solid rgba(124,106,247,0.35)',
            borderRadius: '16px 16px 4px 16px',
            color: '#e0e0f0',
            fontSize: 14,
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {message.content}
        </div>
      </div>
    )
  }

  if (message.type === 'error') {
    return (
      <div style={{ marginBottom: 12 }}>
        <div
          style={{
            padding: '10px 14px',
            background: 'rgba(248,113,113,0.1)',
            border: '1px solid rgba(248,113,113,0.3)',
            borderRadius: 8,
            color: '#f87171',
            fontSize: 13,
          }}
        >
          <span style={{ fontWeight: 600, marginRight: 6 }}>Error:</span>
          {message.content}
        </div>
      </div>
    )
  }

  if (message.type === 'clarification') {
    return (
      <div style={{ marginBottom: 12 }}>
        <ClarificationCard
          question={message.question ?? message.content}
          options={message.options ?? []}
          context={message.context}
          multiSelect={message.multiSelect}
          answered={message.answered}
          onAnswer={(answer) => onClarificationAnswer?.(message.id, answer)}
        />
      </div>
    )
  }

  if (message.type === 'sql_preview' && message.sqlPreview) {
    const isThisExecuting = isExecutingSql && executedSqlMessageId === message.id
    const wasExecuted = !isExecutingSql && executedSqlMessageId === message.id
    return (
      <div style={{ marginBottom: 12 }}>
        <SqlPreviewCard
          sql={message.sqlPreview.sql}
          explanation={message.sqlPreview.explanation}
          validationPassed={message.sqlPreview.validationPassed}
          validationErrors={message.sqlPreview.validationErrors}
          onRunQuery={(sql) => onExecuteSql?.(message.id, sql)}
          onOpenInEditor={onOpenInEditor}
          onAcceptQuery={(sql, accepted) => onAcceptQuery?.(message.id, sql, accepted)}
          isExecuting={isThisExecuting}
          executed={wasExecuted}
        />
      </div>
    )
  }

  if (message.type === 'sql_candidates' && message.sqlCandidates) {
    return (
      <div style={{ marginBottom: 12 }}>
        <SqlCandidatesPicker
          candidates={message.sqlCandidates}
          onSelect={(candidate) => onSelectCandidate?.(message.id, candidate)}
        />
      </div>
    )
  }

  if (message.type === 'kyc_auto_answer' && message.kycAutoAnswer) {
    return (
      <div style={{ marginBottom: 12 }}>
        <KycAutoAnswerBadge
          question={message.kycAutoAnswer.question}
          autoAnswer={message.kycAutoAnswer.autoAnswer}
          source={message.kycAutoAnswer.source}
        />
      </div>
    )
  }

  // result type
  if (message.result) {
    return (
      <div style={{ marginBottom: 12 }}>
        <SqlResultCard result={message.result} onOpenInEditor={onOpenInEditor} />
      </div>
    )
  }

  return null
}
