import { create } from 'zustand'
import type { ChatMessage, ConversationMessage, QueryResult } from '../types'

interface ClarificationPair {
  question: string
  answer: string
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

  addUserMessage(content: string): void
  addResultMessage(result: QueryResult): void
  addErrorMessage(content: string): void
  addClarificationMessage(question: string, options: string[], context?: string, multiSelect?: boolean): void
  addSqlPreviewMessage(sql: string, explanation: string, validationPassed: boolean, validationErrors: string[]): void
  addSqlCandidatesMessage(candidates: Array<{ id: string; interpretation: string; sql: string; explanation: string }>): void
  markClarificationAnswered(id: string): void
  /** Save the original query that started the current topic. */
  setActiveBaseQuery(query: string): void
  /** Accumulate a clarification answer into the running requirements. */
  addClarificationPair(question: string, answer: string): void
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

  addSqlCandidatesMessage: (candidates) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          id: makeId(),
          type: 'sql_candidates' as const,
          content: `${candidates.length} SQL interpretation${candidates.length !== 1 ? 's' : ''} available`,
          sqlCandidates: candidates,
          timestamp: new Date(),
        },
      ],
    })),

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
    })),

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
    set({ messages: [], history: [], activeBaseQuery: '', clarificationPairs: [], lastSuccessfulResult: null }),

  restoreSession: (messages, history) =>
    set({ messages, history, activeBaseQuery: '', clarificationPairs: [], lastSuccessfulResult: null }),
}))
