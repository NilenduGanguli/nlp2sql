import React, { useState, useEffect } from 'react'
import { AppShell } from './components/layout/AppShell'
import type { TabId } from './components/layout/AppShell'
import { ChatPage } from './pages/ChatPage'
import { EditorPage } from './pages/EditorPage'
import { GraphPage } from './pages/GraphPage'
import { RelationshipsPage } from './pages/RelationshipsPage'
import { HistoryPage } from './pages/HistoryPage'
import { InvestigatePage } from './pages/InvestigatePage'
import { TeachPage } from './pages/TeachPage'
import { PromptStudioPage } from './pages/PromptStudioPage'
import { KYCAgentPage } from './pages/KYCAgentPage'
import { SchemaTab } from './components/schema/SchemaTab'
import { useChatStore } from './store/chatStore'
import { useUserMode, USER_MODE_STORAGE_KEY } from './hooks/useUserMode'
import type { ChatSession } from './types'

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>('chat')
  const [editorSql, setEditorSql] = useState('')
  const [selectedSchemaTable, setSelectedSchemaTable] = useState<string | null>(null)
  const { restoreSession } = useChatStore()

  const handleOpenInEditor = (sql: string) => {
    setEditorSql(sql)
    setActiveTab('editor')
  }

  const handleTableSelect = (fqn: string) => {
    setSelectedSchemaTable(fqn)
    setActiveTab('schema')
  }

  const handleResumeSession = (session: ChatSession) => {
    restoreSession(session.messages, session.history)
    setActiveTab('chat')
  }

  useEffect(() => {
    // Sync initial mode from server default only when the user has never picked one.
    // zustand-persist stores its envelope at this key synchronously on store init,
    // so by the time this effect fires the key is absent iff the user has no prior choice.
    if (localStorage.getItem(USER_MODE_STORAGE_KEY) !== null) return
    fetch('/api/admin/config')
      .then((r) => r.json())
      .then((cfg) => {
        if (cfg.default_user_mode === 'consumer' || cfg.default_user_mode === 'curator') {
          useUserMode.getState().setMode(cfg.default_user_mode)
        }
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { query?: string } | undefined
      if (!detail?.query) return
      setActiveTab('chat')
      // Defer prefill until the chat tab is mounted-visible.
      setTimeout(() => {
        window.dispatchEvent(
          new CustomEvent('chat-prefill-input', { detail: { query: detail.query } }),
        )
      }, 0)
    }
    window.addEventListener('rerun-query-from-session', handler)
    return () => window.removeEventListener('rerun-query-from-session', handler)
  }, [])

  return (
    <AppShell activeTab={activeTab} onTabChange={setActiveTab} onTableSelect={handleTableSelect}>
      {/* Render all tabs but only show the active one — avoids remounting state */}
      <div
        style={{
          display: activeTab === 'chat' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <ChatPage onOpenInEditor={handleOpenInEditor} />
      </div>

      <div
        style={{
          display: activeTab === 'editor' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <EditorPage initialSql={editorSql} onSqlChange={setEditorSql} />
      </div>

      <div
        style={{
          display: activeTab === 'schema' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <SchemaTab selectedTable={selectedSchemaTable} />
      </div>

      <div
        style={{
          display: activeTab === 'graph' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <GraphPage />
      </div>

      <div
        style={{
          display: activeTab === 'relationships' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <RelationshipsPage />
      </div>

      <div
        style={{
          display: activeTab === 'history' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <HistoryPage onResume={handleResumeSession} />
      </div>

      <div
        style={{
          display: activeTab === 'investigate' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <InvestigatePage />
      </div>

      <div
        style={{
          display: activeTab === 'teach' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <TeachPage />
      </div>

      <div
        style={{
          display: activeTab === 'prompt_studio' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <PromptStudioPage />
      </div>

      <div
        style={{
          display: activeTab === 'kyc_agent' ? 'flex' : 'none',
          flexDirection: 'column',
          height: '100%',
        }}
      >
        <KYCAgentPage />
      </div>
    </AppShell>
  )
}
