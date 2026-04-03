import React, { useState, useRef, useCallback } from 'react'
import { useChatStore } from '../../store/chatStore'
import { useChatHistoryStore } from '../../store/chatHistoryStore'
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
  const {
    messages,
    history,
    addUserMessage,
    addResultMessage,
    addErrorMessage,
    addClarificationMessage,
    markClarificationAnswered,
    clearMessages,
  } = useChatStore()
  const { saveSession } = useChatHistoryStore()
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [completedSteps, setCompletedSteps] = useState<QueryStep[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  /** Core submit: accepts explicit content so clarification answers can bypass the input field. */
  const handleSubmitContent = useCallback(
    async (content: string) => {
      if (!content.trim() || isStreaming) return

      const historySnapshot = [...history]
      setInput('')
      setIsStreaming(true)
      setCompletedSteps([])
      addUserMessage(content)

      abortRef.current = streamQuery(
        content,
        historySnapshot,
        (step) => setCompletedSteps((prev) => (prev.includes(step) ? prev : [...prev, step])),
        (_sql) => {
          // sql preview — not shown separately in chat
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
        (question, options) => {
          // Agent is asking for clarification — show card, stop streaming indicator
          addClarificationMessage(question, options)
          setIsStreaming(false)
          setCompletedSteps([])
        },
      )
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isStreaming, history],
  )

  const handleSubmit = useCallback(async () => {
    await handleSubmitContent(input.trim())
  }, [input, handleSubmitContent])

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

  /** Called when the user picks an answer from a ClarificationCard. */
  const handleClarificationAnswer = useCallback(
    (messageId: string, answer: string) => {
      markClarificationAnswered(messageId)
      void handleSubmitContent(answer)
    },
    [markClarificationAnswered, handleSubmitContent],
  )

  /** Save current chat to history and start fresh. */
  const handleNewChat = useCallback(() => {
    if (messages.length > 0) saveSession(messages, history)
    clearMessages()
  }, [messages, history, saveSession, clearMessages])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* Chat header with New Chat button */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 16px',
          borderBottom: '1px solid #2a2a3e',
          background: '#1e1e2e',
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 500, color: '#6a6a8a' }}>
          {messages.length === 0
            ? 'New conversation'
            : `${messages.filter((m) => m.type === 'user').length} message${messages.filter((m) => m.type === 'user').length !== 1 ? 's' : ''}`}
        </span>
        <button
          onClick={handleNewChat}
          disabled={isStreaming}
          title="Save this chat and start a new one"
          style={{
            padding: '4px 12px',
            background: messages.length > 0 ? 'rgba(124,106,247,0.12)' : 'none',
            border: `1px solid ${messages.length > 0 ? 'rgba(124,106,247,0.3)' : '#2a2a3e'}`,
            borderRadius: 6,
            color: messages.length > 0 ? '#a5b4fc' : '#4a4a6a',
            fontSize: 11,
            fontWeight: 500,
            cursor: messages.length > 0 && !isStreaming ? 'pointer' : 'default',
            transition: 'all 0.15s',
          }}
        >
          + New Chat
        </button>
      </div>
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
      <MessageList
        messages={messages}
        onOpenInEditor={onOpenInEditor}
        onClarificationAnswer={handleClarificationAnswer}
      />

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
