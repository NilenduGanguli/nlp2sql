import React, { useState, useRef, useCallback } from 'react'
import { useChatStore } from '../../store/chatStore'
import { useChatHistoryStore } from '../../store/chatHistoryStore'
import { useTraceStore } from '../../store/traceStore'
import { streamQuery, executeConfirmedSql, executeCandidateSql, acceptGeneratedQuery } from '../../api/query'
import { MessageList } from './MessageList'
import { StreamingIndicator } from './StreamingIndicator'
import type { ConversationMessage, QueryStep, TraceStep } from '../../types'

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
    activeBaseQuery,
    addUserMessage,
    addResultMessage,
    addErrorMessage,
    addClarificationMessage,
    addSqlPreviewMessage,
    addSqlCandidatesMessage,
    markClarificationAnswered,
    setActiveBaseQuery,
    addClarificationPair,
    getCumulativeQuery,
    getFollowUpContext,
    clearMessages,
  } = useChatStore()
  const { saveSession } = useChatHistoryStore()
  const { startQuery, addLiveStep, finalizeTrace } = useTraceStore()
  const traceIdRef = useRef<string>('')
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [isExecutingSql, setIsExecutingSql] = useState(false)
  const [executedSqlMessageId, setExecutedSqlMessageId] = useState<string | undefined>(undefined)
  const [completedSteps, setCompletedSteps] = useState<QueryStep[]>([])
  const abortRef = useRef<AbortController | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const lastUserInputRef = useRef<string>('')

  /** Internal: kick off an SSE stream for the given input + history snapshot. */
  const _stream = useCallback(
    (userInput: string, historySnap: ConversationMessage[]) => {
      setIsStreaming(true)
      setCompletedSteps([])
      lastUserInputRef.current = userInput
      traceIdRef.current = startQuery(userInput)

      abortRef.current = streamQuery(
        userInput,
        historySnap,
        (step) => setCompletedSteps((prev) => (prev.includes(step) ? prev : [...prev, step])),
        (_sql) => {
          // sql preview — not shown separately in chat
        },
        (result) => {
          addResultMessage(result)
          setIsStreaming(false)
          setCompletedSteps([])
          const steps = (result as { _trace?: TraceStep[] })._trace ?? []
          finalizeTrace(traceIdRef.current, steps)
          setTimeout(() => textareaRef.current?.focus(), 0)
        },
        (errMsg) => {
          addErrorMessage(errMsg)
          setIsStreaming(false)
          setCompletedSteps([])
          setTimeout(() => textareaRef.current?.focus(), 0)
        },
        (question, options, context, multiSelect) => {
          // Agent is asking for clarification — show card, stop streaming indicator
          addClarificationMessage(question, options, context, multiSelect)
          setIsStreaming(false)
          setCompletedSteps([])
        },
        (step) => addLiveStep(step),
        // onSqlReady — backend paused before execution, show preview card
        (data) => {
          addSqlPreviewMessage(
            data.sql,
            data.explanation,
            data.validation_passed,
            data.validation_errors,
          )
          setIsStreaming(false)
          setCompletedSteps([])
        },
        // onSqlCandidates — multiple interpretations
        (candidates) => {
          addSqlCandidatesMessage(candidates)
          setIsStreaming(false)
          setCompletedSteps([])
        },
        // onKycAutoAnswer — informational, no action needed from user
        undefined,
      )
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [addResultMessage, addErrorMessage, addClarificationMessage, addSqlPreviewMessage, addSqlCandidatesMessage, addLiveStep, finalizeTrace, startQuery],
  )

  /** Fresh query submitted by the user via the input box. */
  const handleSubmitContent = useCallback(
    (content: string) => {
      if (!content.trim() || isStreaming) return
      // Save as the base query for any clarification chain that follows
      setActiveBaseQuery(content)
      const historySnap = [...history]
      setInput('')
      addUserMessage(content)
      // Enrich with follow-up context if this references previous results
      const enrichedInput = getFollowUpContext(content)
      _stream(enrichedInput, historySnap)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isStreaming, history, _stream, getFollowUpContext],
  )

  const handleSubmit = useCallback(() => {
    handleSubmitContent(input.trim())
  }, [input, handleSubmitContent])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleSuggestedQuery = (q: string) => {
    setInput(q)
    textareaRef.current?.focus()
  }

  const handleStop = () => {
    abortRef.current?.abort()
    setIsStreaming(false)
    setIsExecutingSql(false)
    setCompletedSteps([])
  }

  /**
   * Called when user clicks "Run Query" on an SqlPreviewCard.
   * Streams execution results from POST /api/query/execute.
   */
  const handleExecuteConfirmed = useCallback(
    (messageId: string, sql: string) => {
      setIsStreaming(true)
      setIsExecutingSql(true)
      setExecutedSqlMessageId(messageId)
      setCompletedSteps([])

      const queryContext = activeBaseQuery || lastUserInputRef.current
      const historySnap = [...history]

      abortRef.current = executeConfirmedSql(
        sql,
        queryContext,
        historySnap,
        (step) => setCompletedSteps((prev) => (prev.includes(step) ? prev : [...prev, step])),
        (result) => {
          addResultMessage(result)
          setIsStreaming(false)
          setIsExecutingSql(false)
          setCompletedSteps([])
          const steps = (result as { _trace?: TraceStep[] })._trace ?? []
          finalizeTrace(traceIdRef.current, steps)
          setTimeout(() => textareaRef.current?.focus(), 0)
        },
        (errMsg) => {
          addErrorMessage(errMsg)
          setIsStreaming(false)
          setIsExecutingSql(false)
          setCompletedSteps([])
          setTimeout(() => textareaRef.current?.focus(), 0)
        },
        (step) => addLiveStep(step),
      )
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeBaseQuery, history, addResultMessage, addErrorMessage, addLiveStep, finalizeTrace],
  )

  /**
   * Called when the user picks an answer from a ClarificationCard.
   *
   * Instead of sending just the isolated answer, this builds a cumulative
   * query that packages the original question + all clarification Q&A pairs
   * gathered so far, including this latest answer.  The backend therefore
   * always receives a self-contained, complete requirements spec.
   */
  const handleClarificationAnswer = useCallback(
    (messageId: string, answer: string) => {
      // Find the clarification question text from the message
      const msg = messages.find((m) => m.id === messageId)
      const question = msg?.question ?? ''

      // Accumulate this Q&A pair into the running requirements
      addClarificationPair(question, answer)
      markClarificationAnswered(messageId)

      // Show the user's answer as a visible chat bubble
      addUserMessage(answer)

      // Build the full cumulative query (base + all pairs including latest)
      const cumulativeQuery = getCumulativeQuery()

      // Build history snapshot including the answer the user just gave.
      // (addUserMessage updates history in Zustand but hasn't re-rendered yet,
      //  so we append the answer explicitly to the current snapshot.)
      const historyWithAnswer: ConversationMessage[] = [
        ...history,
        { role: 'user' as const, content: answer },
      ]

      // Stream using the cumulative query as user_input
      _stream(cumulativeQuery, historyWithAnswer)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [messages, history, _stream, addClarificationPair, markClarificationAnswered, addUserMessage, getCumulativeQuery],
  )

  /**
   * Called when the user picks a candidate from the SqlCandidatesPicker.
   * Sends the selected SQL through validate -> optimize -> sql_ready pipeline.
   */
  const handleSelectCandidate = useCallback(
    (_messageId: string, candidate: { id: string; interpretation: string; sql: string; explanation: string }) => {
      addUserMessage(`Selected: ${candidate.interpretation}`)
      setIsStreaming(true)
      setCompletedSteps([])

      const queryContext = activeBaseQuery || lastUserInputRef.current
      const historySnap = [...history]

      abortRef.current = executeCandidateSql(
        candidate.sql,
        candidate.explanation,
        queryContext,
        historySnap,
        (step) => setCompletedSteps((prev) => (prev.includes(step) ? prev : [...prev, step])),
        (data) => {
          addSqlPreviewMessage(
            data.sql,
            data.explanation,
            data.validation_passed,
            data.validation_errors,
          )
          setIsStreaming(false)
          setCompletedSteps([])
        },
        (errMsg) => {
          addErrorMessage(errMsg)
          setIsStreaming(false)
          setCompletedSteps([])
          setTimeout(() => textareaRef.current?.focus(), 0)
        },
        (step) => addLiveStep(step),
      )
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeBaseQuery, history, addUserMessage, addSqlPreviewMessage, addErrorMessage, addLiveStep],
  )

  /**
   * Called when user clicks thumbs up/down on an SqlPreviewCard.
   * Sends feedback to the backend to record (or skip) in the KYC knowledge store.
   */
  const handleAcceptQuery = useCallback(
    (_messageId: string, sql: string, accepted: boolean) => {
      const queryContext = activeBaseQuery || lastUserInputRef.current
      const { clarificationPairs } = useChatStore.getState()
      // Find the explanation from the sql_preview message
      const msg = messages.find((m) => m.id === _messageId)
      const explanation = msg?.sqlPreview?.explanation ?? ''

      acceptGeneratedQuery(sql, explanation, queryContext, clarificationPairs, accepted).catch(
        (err) => console.warn('Failed to send query feedback:', err),
      )
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeBaseQuery, messages],
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
        onExecuteSql={handleExecuteConfirmed}
        onSelectCandidate={handleSelectCandidate}
        onAcceptQuery={handleAcceptQuery}
        isExecutingSql={isExecutingSql}
        executedSqlMessageId={executedSqlMessageId}
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
              onClick={handleSubmit}
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
