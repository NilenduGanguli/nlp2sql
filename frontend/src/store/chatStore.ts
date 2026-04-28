import { create } from 'zustand'
import { postSignal, sha1Hex } from '../api/signals'
import { useUserMode } from '../hooks/useUserMode'
import type { ChatMessage, ConversationMessage, QueryResult, TraceStep, SignalEventType } from '../types'

interface ClarificationPair {
  question: string
  answer: string
}

export interface SessionDigest {
  tool_calls: Array<{ tool: string; args: Record<string, unknown>; result_summary: string }>
  schema_context_tables: string[]
  intent: string
  entities: Record<string, unknown>
  enriched_query: string
  clarifications: Array<{ question: string; answer: string }>
  validation_retries: number
}

const _MAX_TOOL_CALLS = 30
const _MAX_RESULT_SUMMARY_CHARS = 200

const _emptyDigest = (): SessionDigest => ({
  tool_calls: [],
  schema_context_tables: [],
  intent: 'DATA_QUERY',
  entities: {},
  enriched_query: '',
  clarifications: [],
  validation_retries: 0,
})

function _summarizeOp(op: { op: string; params?: Record<string, unknown>; result_count?: number; result_sample?: unknown[] }): { tool: string; args: Record<string, unknown>; result_summary: string } {
  const sample = op.result_sample ?? []
  const summary = `count=${op.result_count ?? 0}; sample=${JSON.stringify(sample)}`
  return {
    tool: op.op ?? '',
    args: op.params ?? {},
    result_summary: summary.slice(0, _MAX_RESULT_SUMMARY_CHARS),
  }
}

interface ChatStore {
  messages: ChatMessage[]
  history: ConversationMessage[]
  /** Original user query at the start of the current topic/clarification chain. */
  activeBaseQuery: string
  /** All clarification Q&A pairs gathered so far for the current topic. */
  clarificationPairs: ClarificationPair[]
  /** Last successful query result (non-error) for follow-up context. */
  lastSuccessfulResult: QueryResult | null
  /** Accumulated digest of the current turn (tool calls, schema, etc.). Used to seed accept-query. */
  currentSessionDigest: SessionDigest
  /** Set true when the latest assistant turn was short-circuited from a saved session. */
  lastReusedFromSession: boolean
  /** UUID for the current top-level query session. Reset on every new top-level query. */
  sessionId: string
  /** The matched query_session entry_id, if the backend short-circuited via session-match. */
  matchedEntryId: string | null
  /** The last SQL shown to the user (via sql_preview or sql_candidates). Used for abandon detection. */
  lastSqlShown: string | null
  /** True once the user explicitly accepted the last shown SQL. Reset whenever lastSqlShown changes. */
  lastSqlAccepted: boolean
  /** Non-null when the most recent result had zero rows, within the last 60 s. */
  zeroRowsState: { ts: number; sql: string } | null
  /** Snapshot of the most recent successful SQL/explanation, sent to the backend so the LLM can refine it. */
  previousSqlContext: { sql: string; explanation: string } | null

  addUserMessage(content: string): void
  addResultMessage(result: QueryResult): void
  addErrorMessage(content: string): void
  addClarificationMessage(question: string, options: string[], context?: string, multiSelect?: boolean): void
  addSqlPreviewMessage(sql: string, explanation: string, validationPassed: boolean, validationErrors: string[]): void
  addSqlCandidatesMessage(candidates: Array<{ id: string; interpretation: string; sql: string; explanation: string }>, reusedFromSession?: boolean): void
  /** Add an auto-answer message from the KYC business agent + mark latest clarification as answered. */
  addKycAutoAnswerMessage(question: string, autoAnswer: string, source: string): void
  markClarificationAnswered(id: string): void
  /** Save the original query that started the current topic. */
  setActiveBaseQuery(query: string): void
  /** Accumulate a clarification answer into the running requirements. */
  addClarificationPair(question: string, answer: string): void
  /** Reset accumulated session digest at the start of a fresh turn. */
  resetSessionDigest(): void
  /** Append a trace step's graph_ops to the running digest. */
  recordTraceForDigest(traceStep: TraceStep): void
  /** Mark that the next assistant turn comes from a saved session. */
  setReusedFromSession(value: boolean): void
  /**
   * Build a self-contained cumulative query that packages the original question
   * plus all clarification requirements gathered so far.
   * This is what gets sent as user_input when answering a clarification.
   */
  getCumulativeQuery(): string
  /**
   * Check if newInput looks like a follow-up referencing previous results.
   * If so AND lastSuccessfulResult exists, append context.
   * Otherwise return newInput unchanged.
   */
  getFollowUpContext(newInput: string): string
  /** Replace current chat with a saved session. */
  restoreSession(messages: ChatMessage[], history: ConversationMessage[]): void
  clearMessages(): void
  /** Mint a fresh sessionId (called at the start of every new top-level query). */
  newSessionId(): void
  /** Set or clear the matched entry_id from a session-match SSE event. */
  setMatchedEntryId(id: string | null): void
  /** Fire a signal event for the given SQL. */
  emitSignal(event: SignalEventType, sql: string, metadata?: Record<string, unknown>): Promise<void>
  setLastSqlShown(sql: string | null): void
  setLastSqlAccepted(v: boolean): void
  setZeroRowsState(s: { ts: number; sql: string } | null): void
  setPreviousSqlContext(ctx: { sql: string; explanation: string } | null): void
  /** Logical reset: clear messages/history but keep sessionId. Used by ⤴ Branch. */
  branchConversation(): void
}

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  history: [],
  activeBaseQuery: '',
  clarificationPairs: [],
  lastSuccessfulResult: null,
  currentSessionDigest: _emptyDigest(),
  lastReusedFromSession: false,
  sessionId: crypto.randomUUID(),
  matchedEntryId: null,
  lastSqlShown: null,
  lastSqlAccepted: false,
  zeroRowsState: null,
  previousSqlContext: null,

  addUserMessage: (content) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { id: makeId(), type: 'user', content, timestamp: new Date() },
      ],
      history: [...state.history, { role: 'user' as const, content }].slice(-20),
    })),

  addResultMessage: (result) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { id: makeId(), type: 'result', content: result.summary, result, timestamp: new Date() },
      ],
      history: [
        ...state.history,
        { role: 'assistant' as const, content: result.summary },
      ].slice(-20),
      // Reset clarification chain on successful result
      clarificationPairs: [],
      activeBaseQuery: '',
      // Track last successful result for follow-up context
      lastSuccessfulResult: result.type !== 'error' ? result : state.lastSuccessfulResult,
    })),

  addErrorMessage: (content) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { id: makeId(), type: 'error', content, timestamp: new Date() },
      ],
    })),

  addClarificationMessage: (question, options, context, multiSelect) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          id: makeId(),
          type: 'clarification',
          content: question,
          question,
          options,
          context,
          multiSelect,
          answered: false,
          timestamp: new Date(),
        },
      ],
      // Add to history so follow-up LLM calls know this Q was asked
      history: [
        ...state.history,
        { role: 'assistant' as const, content: question },
      ].slice(-20),
    })),

  addSqlPreviewMessage: (sql, explanation, validationPassed, validationErrors) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          id: makeId(),
          type: 'sql_preview' as const,
          content: explanation,
          sqlPreview: { sql, explanation, validationPassed, validationErrors },
          timestamp: new Date(),
        },
      ],
    })),

  addSqlCandidatesMessage: (candidates, reusedFromSession) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          id: makeId(),
          type: 'sql_candidates' as const,
          content: `${candidates.length} SQL interpretation${candidates.length !== 1 ? 's' : ''} available`,
          sqlCandidates: candidates,
          reusedFromSession: reusedFromSession ?? state.lastReusedFromSession,
          timestamp: new Date(),
        },
      ],
    })),

  addKycAutoAnswerMessage: (question, autoAnswer, source) =>
    set((state) => {
      // Mark the latest unanswered clarification as answered
      const updatedMessages = state.messages.map((m) =>
        m.type === 'clarification' && !m.answered ? { ...m, answered: true } : m,
      )
      // Add the auto-answer as a visible message + accumulate as a clarification pair
      return {
        messages: [
          ...updatedMessages,
          {
            id: makeId(),
            type: 'kyc_auto_answer' as const,
            content: autoAnswer,
            kycAutoAnswer: { question, autoAnswer, source },
            timestamp: new Date(),
          },
        ],
        history: [
          ...state.history,
          { role: 'user' as const, content: autoAnswer },
        ].slice(-20),
        clarificationPairs: [...state.clarificationPairs, { question, answer: autoAnswer }],
      }
    }),

  markClarificationAnswered: (id) =>
    set((state) => ({
      messages: state.messages.map((m) =>
        m.id === id ? { ...m, answered: true } : m,
      ),
    })),

  setActiveBaseQuery: (query) => set({ activeBaseQuery: query }),

  addClarificationPair: (question, answer) =>
    set((state) => ({
      clarificationPairs: [...state.clarificationPairs, { question, answer }],
      currentSessionDigest: {
        ...state.currentSessionDigest,
        clarifications: [
          ...state.currentSessionDigest.clarifications,
          { question, answer },
        ],
      },
    })),

  resetSessionDigest: () => set({ currentSessionDigest: _emptyDigest(), lastReusedFromSession: false }),

  setReusedFromSession: (value) => set({ lastReusedFromSession: value }),

  recordTraceForDigest: (traceStep) =>
    set((state) => {
      const digest = state.currentSessionDigest
      const next = { ...digest }
      const summary = (traceStep.output_summary ?? {}) as Record<string, unknown>
      if (traceStep.node === 'enrich_query' && typeof summary.enriched === 'string') {
        next.enriched_query = summary.enriched as string
      }
      if (traceStep.node === 'classify_intent' && typeof summary.intent === 'string') {
        next.intent = summary.intent as string
      }
      if (traceStep.node === 'extract_entities' && typeof summary.entities === 'object' && summary.entities !== null) {
        next.entities = summary.entities as Record<string, unknown>
      }
      if (traceStep.node === 'retrieve_schema' && Array.isArray(summary.tables)) {
        next.schema_context_tables = summary.tables as string[]
      }
      if (traceStep.node === 'validate_sql' && typeof summary.retry_count === 'number') {
        next.validation_retries = summary.retry_count as number
      }
      const ops = traceStep.graph_ops ?? []
      if (ops.length > 0 && next.tool_calls.length < _MAX_TOOL_CALLS) {
        const room = _MAX_TOOL_CALLS - next.tool_calls.length
        const newCalls = ops.slice(0, room).map((op) => _summarizeOp({
          op: op.op,
          params: op.params,
          result_count: op.result_count,
          result_sample: op.result_sample,
        }))
        next.tool_calls = [...next.tool_calls, ...newCalls]
      }
      return { currentSessionDigest: next }
    }),

  getCumulativeQuery: () => {
    const { activeBaseQuery, clarificationPairs } = get()
    if (!activeBaseQuery) return ''
    if (clarificationPairs.length === 0) return activeBaseQuery
    const refinements = clarificationPairs
      .map(({ question, answer }) => `- ${question}: ${answer}`)
      .join('\n')
    return `${activeBaseQuery}\n\nAdditional requirements clarified:\n${refinements}`
  },

  getFollowUpContext: (newInput: string) => {
    const { lastSuccessfulResult } = get()
    const followUpPatterns = /\b(those|that|them|the results|now |also |but |filter|sort|group by|break down|only active|exclude)\b/i
    if (followUpPatterns.test(newInput) && lastSuccessfulResult) {
      const { sql, total_rows, columns, explanation } = lastSuccessfulResult
      return `${newInput}\n\n[Context: Previous query was: ${sql}\nReturned ${total_rows} rows, columns: ${columns.join(', ')}\nExplanation: ${explanation}]`
    }
    return newInput
  },

  clearMessages: () =>
    set({
      messages: [], history: [], activeBaseQuery: '', clarificationPairs: [],
      lastSuccessfulResult: null, currentSessionDigest: _emptyDigest(), lastReusedFromSession: false,
      matchedEntryId: null, lastSqlShown: null, lastSqlAccepted: false, zeroRowsState: null,
      previousSqlContext: null,
    }),

  restoreSession: (messages, history) =>
    set({
      messages, history, activeBaseQuery: '', clarificationPairs: [],
      lastSuccessfulResult: null, currentSessionDigest: _emptyDigest(), lastReusedFromSession: false,
      matchedEntryId: null, lastSqlShown: null, lastSqlAccepted: false, zeroRowsState: null,
      previousSqlContext: null,
    }),

  newSessionId: () => set({ sessionId: crypto.randomUUID(), matchedEntryId: null }),

  setMatchedEntryId: (id) => set({ matchedEntryId: id }),

  setLastSqlShown: (sql) => set({ lastSqlShown: sql, lastSqlAccepted: false }),
  setLastSqlAccepted: (v) => set({ lastSqlAccepted: v }),
  setZeroRowsState: (s) => set({ zeroRowsState: s }),
  setPreviousSqlContext: (ctx) => set({ previousSqlContext: ctx }),
  branchConversation: () => set({
    messages: [],
    history: [],
    activeBaseQuery: '',
    clarificationPairs: [],
    lastSuccessfulResult: null,
    currentSessionDigest: _emptyDigest(),
    lastReusedFromSession: false,
    matchedEntryId: null,
    lastSqlShown: null,
    lastSqlAccepted: false,
    zeroRowsState: null,
    previousSqlContext: null,
  }),

  emitSignal: async (event, sql, metadata = {}) => {
    const { sessionId, matchedEntryId } = get()
    const mode = useUserMode.getState().mode
    const sqlHash = sql ? await sha1Hex(sql) : ''
    await postSignal({
      event,
      session_id: sessionId,
      entry_id: matchedEntryId,
      mode,
      sql_hash: sqlHash,
      metadata,
    })
  },
}))
