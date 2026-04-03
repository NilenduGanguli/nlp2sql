import { create } from 'zustand'
import type { ChatMessage, ConversationMessage, QueryResult } from '../types'

interface ChatStore {
  messages: ChatMessage[]
  history: ConversationMessage[]
  addUserMessage(content: string): void
  addResultMessage(result: QueryResult): void
  addErrorMessage(content: string): void
  addClarificationMessage(question: string, options: string[]): void
  markClarificationAnswered(id: string): void
  /** Replace current chat with a saved session. */
  restoreSession(messages: ChatMessage[], history: ConversationMessage[]): void
  clearMessages(): void
}

function makeId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export const useChatStore = create<ChatStore>((set) => ({
  messages: [],
  history: [],

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
    })),

  addErrorMessage: (content) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { id: makeId(), type: 'error', content, timestamp: new Date() },
      ],
    })),

  addClarificationMessage: (question, options) =>
    set((state) => ({
      messages: [
        ...state.messages,
        {
          id: makeId(),
          type: 'clarification',
          content: question,
          question,
          options,
          answered: false,
          timestamp: new Date(),
        },
      ],
      // Add to history so follow-up knows this Q was asked
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

  clearMessages: () => set({ messages: [], history: [] }),

  restoreSession: (messages, history) => set({ messages, history }),
}))
