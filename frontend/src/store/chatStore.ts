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

  addUserMessage(content: string): void
  addResultMessage(result: QueryResult): void
  addErrorMessage(content: string): void
  addClarificationMessage(question: string, options: string[], context?: string, multiSelect?: boolean): void
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

  clearMessages: () =>
    set({ messages: [], history: [], activeBaseQuery: '', clarificationPairs: [] }),

  restoreSession: (messages, history) =>
    set({ messages, history, activeBaseQuery: '', clarificationPairs: [] }),
}))
