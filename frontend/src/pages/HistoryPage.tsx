import React from 'react'
import { useChatHistoryStore } from '../store/chatHistoryStore'
import type { ChatSession } from '../types'

interface HistoryPageProps {
  onResume: (session: ChatSession) => void
}

export const HistoryPage: React.FC<HistoryPageProps> = ({ onResume }) => {
  const { sessions, deleteSession, clearAllSessions } = useChatHistoryStore()

  if (sessions.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#5a5a7a',
          gap: 8,
        }}
      >
        <div style={{ fontSize: 32 }}>💬</div>
        <div style={{ fontSize: 14 }}>No saved chats yet</div>
        <div style={{ fontSize: 12 }}>
          Start a conversation and use "New Chat" to save it to history
        </div>
      </div>
    )
  }

  return (
    <div
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '20px 24px',
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: 20,
          flexShrink: 0,
        }}
      >
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, color: '#e0e0f0' }}>Chat History</div>
          <div style={{ fontSize: 12, color: '#6a6a8a', marginTop: 2 }}>
            {sessions.length} saved conversation{sessions.length !== 1 ? 's' : ''}
          </div>
        </div>
        <button
          onClick={() => {
            if (confirm('Delete all chat history?')) clearAllSessions()
          }}
          style={{
            padding: '5px 12px',
            background: 'none',
            border: '1px solid rgba(248,113,113,0.35)',
            borderRadius: 6,
            color: '#f87171',
            fontSize: 11,
            cursor: 'pointer',
          }}
        >
          Clear all
        </button>
      </div>

      {/* Session list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {sessions.map((session) => {
          const userCount = session.messages.filter((m) => m.type === 'user').length
          const resultCount = session.messages.filter((m) => m.type === 'result').length
          const date = new Date(session.createdAt)
          const dateStr = date.toLocaleDateString(undefined, {
            month: 'short',
            day: 'numeric',
            year: date.getFullYear() !== new Date().getFullYear() ? 'numeric' : undefined,
          })
          const timeStr = date.toLocaleTimeString(undefined, {
            hour: '2-digit',
            minute: '2-digit',
          })

          return (
            <div
              key={session.id}
              style={{
                background: '#2a2a3e',
                border: '1px solid #3a3a5c',
                borderRadius: 10,
                padding: '14px 16px',
                display: 'flex',
                alignItems: 'center',
                gap: 14,
              }}
            >
              {/* Icon */}
              <div
                style={{
                  width: 36,
                  height: 36,
                  borderRadius: 8,
                  background: 'rgba(124,106,247,0.15)',
                  border: '1px solid rgba(124,106,247,0.25)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 16,
                  flexShrink: 0,
                }}
              >
                💬
              </div>

              {/* Info */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 500,
                    color: '#d0d0e8',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    marginBottom: 4,
                  }}
                  title={session.title}
                >
                  {session.title}
                </div>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                  <span style={{ fontSize: 11, color: '#6a6a8a' }}>
                    {dateStr} · {timeStr}
                  </span>
                  <span
                    style={{
                      fontSize: 10,
                      padding: '1px 6px',
                      borderRadius: 999,
                      background: 'rgba(124,106,247,0.12)',
                      color: '#9090c0',
                    }}
                  >
                    {userCount} message{userCount !== 1 ? 's' : ''}
                  </span>
                  {resultCount > 0 && (
                    <span
                      style={{
                        fontSize: 10,
                        padding: '1px 6px',
                        borderRadius: 999,
                        background: 'rgba(74,222,128,0.10)',
                        color: '#4ade80',
                      }}
                    >
                      {resultCount} result{resultCount !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
              </div>

              {/* Actions */}
              <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                <button
                  onClick={() => onResume(session)}
                  style={{
                    padding: '5px 14px',
                    background: 'rgba(124,106,247,0.15)',
                    border: '1px solid rgba(124,106,247,0.35)',
                    borderRadius: 6,
                    color: '#a5b4fc',
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: 'pointer',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) =>
                    ((e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.25)')
                  }
                  onMouseLeave={(e) =>
                    ((e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.15)')
                  }
                >
                  Resume
                </button>
                <button
                  onClick={() => deleteSession(session.id)}
                  title="Delete"
                  style={{
                    padding: '5px 8px',
                    background: 'none',
                    border: '1px solid #3a3a5c',
                    borderRadius: 6,
                    color: '#6a6a8a',
                    fontSize: 12,
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    const el = e.currentTarget as HTMLElement
                    el.style.borderColor = 'rgba(248,113,113,0.4)'
                    el.style.color = '#f87171'
                  }}
                  onMouseLeave={(e) => {
                    const el = e.currentTarget as HTMLElement
                    el.style.borderColor = '#3a3a5c'
                    el.style.color = '#6a6a8a'
                  }}
                >
                  ✕
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
