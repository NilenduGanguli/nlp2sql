import React, { useState } from 'react'
import { AppShell } from './components/layout/AppShell'
import type { TabId } from './components/layout/AppShell'
import { ChatPage } from './pages/ChatPage'
import { EditorPage } from './pages/EditorPage'
import { GraphPage } from './pages/GraphPage'
import { RelationshipsPage } from './pages/RelationshipsPage'
import { HistoryPage } from './pages/HistoryPage'
import { InvestigatePage } from './pages/InvestigatePage'
import { PromptStudioPage } from './pages/PromptStudioPage'
import { KYCAgentPage } from './pages/KYCAgentPage'
import { SchemaTab } from './components/schema/SchemaTab'
import { useChatStore } from './store/chatStore'
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
