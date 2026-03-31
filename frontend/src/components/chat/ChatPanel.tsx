import React, { useState, useRef, useCallback } from 'react'
import { useChatStore } from '../../store/chatStore'
import { streamQuery } from '../../api/query'
import { MessageList } from './MessageList'
import { StreamingIndicator } from './StreamingIndicator'
import type { QueryStep } from '../../types'

const SUGGESTED_QUERIES = [
  'Show me all customers with KYC status pending',
  'What tables contain transaction data?',
  'Find customers who submitted documents in the last 30 days',
  'Show the relationship between customer and account tables',
]

interface ChatPanelProps {
  onOpenInEditor?: (sql: string) => void
}

export const ChatPanel: React.FC<ChatPanelProps> = ({ onOpenInEditor }) => {
  const { messages, history, addUserMessage, addResultMessage, addErrorMessage } = useChatStore()
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [completedSteps, setCompletedSteps] = useState<QueryStep[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSubmit = useCallback(async () => {
    const content = input.trim()
    if (!content || isStreaming) return

    // Snapshot history before current message (backend expects prior context only)
    const historySnapshot = [...history]

    setInput('')
    setIsStreaming(true)
    setCompletedSteps([])

    addUserMessage(content)

    abortRef.current = streamQuery(
      content,
      historySnapshot,
      (step) => {
        setCompletedSteps((prev) => (prev.includes(step) ? prev : [...prev, step]))
      },
      (_sql) => {
        // sql preview - not shown separately in chat
      },
      (result) => {
        addResultMessage(result)
        setIsStreaming(false)
        setCompletedSteps([])
      },
      (errMsg) => {
        addErrorMessage(errMsg)
        setIsStreaming(false)
        setCompletedSteps([])
      },
    )
  }, [input, isStreaming, history, addUserMessage, addResultMessage, addErrorMessage])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void handleSubmit()
    }
  }

  const handleSuggestedQuery = (q: string) => {
    setInput(q)
    textareaRef.current?.focus()
  }

  const handleStop = () => {
    abortRef.current?.abort()
    setIsStreaming(false)
    setCompletedSteps([])
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* Suggested queries */}
      {messages.length === 0 && (
        <div
          style={{
            padding: '16px 20px 0',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
            flexShrink: 0,
          }}
        >
          {SUGGESTED_QUERIES.map((q) => (
            <button
              key={q}
              onClick={() => handleSuggestedQuery(q)}
              style={{
                padding: '6px 12px',
                background: 'rgba(124,106,247,0.1)',
                border: '1px solid rgba(124,106,247,0.3)',
                borderRadius: 999,
                color: '#a5b4fc',
                fontSize: 12,
                cursor: 'pointer',
                transition: 'background 0.15s',
              }}
              onMouseEnter={(e) =>
                ((e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.2)')
              }
              onMouseLeave={(e) =>
                ((e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.1)')
              }
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Message list */}
      <MessageList messages={messages} onOpenInEditor={onOpenInEditor} />

      {/* Streaming indicator */}
      {isStreaming && (
        <div style={{ flexShrink: 0 }}>
          <StreamingIndicator steps={completedSteps} isStreaming={isStreaming} />
        </div>
      )}

      {/* Input bar */}
      <div
        style={{
          padding: '12px 16px',
          borderTop: '1px solid #3a3a5c',
          background: '#2a2a3e',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about your data in natural language… (Enter to send, Shift+Enter for newline)"
            rows={2}
            disabled={isStreaming}
            style={{
              flex: 1,
              background: '#1e1e2e',
              border: '1px solid #3a3a5c',
              borderRadius: 8,
              padding: '8px 12px',
              color: '#e0e0f0',
              fontSize: 14,
              resize: 'none',
              outline: 'none',
              lineHeight: 1.5,
              transition: 'border-color 0.15s',
            }}
            onFocus={(e) => (e.target.style.borderColor = '#7c6af7')}
            onBlur={(e) => (e.target.style.borderColor = '#3a3a5c')}
          />
          {isStreaming ? (
            <button
              onClick={handleStop}
              style={{
                padding: '8px 16px',
                background: 'rgba(248,113,113,0.15)',
                border: '1px solid rgba(248,113,113,0.4)',
                borderRadius: 8,
                color: '#f87171',
                fontSize: 13,
                fontWeight: 600,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
                alignSelf: 'flex-end',
              }}
            >
              Stop
            </button>
          ) : (
            <button
              onClick={() => void handleSubmit()}
              disabled={!input.trim()}
              style={{
                padding: '8px 20px',
                background: input.trim() ? '#7c6af7' : '#3a3a5c',
                border: 'none',
                borderRadius: 8,
                color: input.trim() ? '#fff' : '#6a6a8a',
                fontSize: 13,
                fontWeight: 600,
                cursor: input.trim() ? 'pointer' : 'not-allowed',
                whiteSpace: 'nowrap',
                alignSelf: 'flex-end',
                transition: 'background 0.15s',
              }}
            >
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
