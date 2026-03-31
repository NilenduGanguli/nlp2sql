import React, { useState } from 'react'
import { AppShell } from './components/layout/AppShell'
import type { TabId } from './components/layout/AppShell'
import { ChatPage } from './pages/ChatPage'
import { EditorPage } from './pages/EditorPage'
import { GraphPage } from './pages/GraphPage'
import { RelationshipsPage } from './pages/RelationshipsPage'
import { SchemaTab } from './components/schema/SchemaTab'

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>('chat')
  const [editorSql, setEditorSql] = useState('')
  const [selectedSchemaTable, setSelectedSchemaTable] = useState<string | null>(null)

  const handleOpenInEditor = (sql: string) => {
    setEditorSql(sql)
    setActiveTab('editor')
  }

  const handleTableSelect = (fqn: string) => {
    setSelectedSchemaTable(fqn)
    setActiveTab('schema')
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
    </AppShell>
  )
}
