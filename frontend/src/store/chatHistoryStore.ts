import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ChatMessage, ChatSession, ConversationMessage } from '../types'

const MAX_SESSIONS = 50

interface ChatHistoryStore {
  sessions: ChatSession[]
  /** Save the current chat as a new session (no-op if no user messages). */
  saveSession(messages: ChatMessage[], history: ConversationMessage[]): void
  deleteSession(id: string): void
  clearAllSessions(): void
}

function makeSessionId(): string {
  return `session-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

function sessionTitle(messages: ChatMessage[]): string {
  const first = messages.find((m) => m.type === 'user')
  if (!first) return 'Untitled chat'
  return first.content.length > 60 ? first.content.slice(0, 57) + '…' : first.content
}

export const useChatHistoryStore = create<ChatHistoryStore>()(
  persist(
    (set) => ({
      sessions: [],

      saveSession: (messages, history) => {
        if (!messages.some((m) => m.type === 'user')) return // nothing to save
        const session: ChatSession = {
          id: makeSessionId(),
          title: sessionTitle(messages),
          createdAt: new Date().toISOString(),
          messages,
          history,
        }
        set((state) => ({
          sessions: [session, ...state.sessions].slice(0, MAX_SESSIONS),
        }))
      },

      deleteSession: (id) =>
        set((state) => ({ sessions: state.sessions.filter((s) => s.id !== id) })),

      clearAllSessions: () => set({ sessions: [] }),
    }),
    {
      name: 'knowledgeql-chat-history',
      // Timestamps in ChatMessage are Date objects — revive them after deserialization
      onRehydrateStorage: () => (state) => {
        if (!state) return
        state.sessions = state.sessions.map((session) => ({
          ...session,
          messages: session.messages.map((m) => ({
            ...m,
            timestamp: new Date(m.timestamp),
          })),
        }))
      },
    },
  ),
)
