import { create } from 'zustand'
import type { ChatMessage, ConversationMessage, QueryResult } from '../types'

interface ChatStore {
  messages: ChatMessage[]
  history: ConversationMessage[]
  addUserMessage(content: string): void
  addResultMessage(result: QueryResult): void
  addErrorMessage(content: string): void
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

  clearMessages: () => set({ messages: [], history: [] }),
}))
