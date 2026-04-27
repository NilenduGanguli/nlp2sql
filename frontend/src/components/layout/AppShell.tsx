import React, { useState, useEffect } from 'react'
import { Sidebar } from './Sidebar'
import { ModeToggle } from './ModeToggle'
import { useUserMode } from '../../hooks/useUserMode'

export type TabId = 'chat' | 'editor' | 'schema' | 'graph' | 'relationships' | 'history' | 'investigate' | 'prompt_studio' | 'kyc_agent'

interface AppShellProps {
  activeTab: TabId
  onTabChange: (tab: TabId) => void
  /** Called when the user clicks a table in the sidebar. Receives the table FQN. */
  onTableSelect?: (fqn: string) => void
  children: React.ReactNode
}

const TAB_LABELS: Record<TabId, string> = {
  chat: 'Chat',
  editor: 'SQL Editor',
  schema: 'Schema',
  graph: 'Knowledge Graph',
  relationships: 'Relationships',
  history: 'History',
  investigate: 'Investigate',
  prompt_studio: 'Prompt Studio',
  kyc_agent: 'KYC Agent',
}

export const AppShell: React.FC<AppShellProps> = ({
  activeTab,
  onTabChange,
  onTableSelect,
  children,
}) => {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const { mode } = useUserMode()

  const visibleTabs = (Object.keys(TAB_LABELS) as TabId[]).filter(
    (tab) => !(mode === 'consumer' && tab === 'investigate'),
  )

  // If the user switches to consumer mode while on the Investigate tab,
  // fall back to the chat tab so they don't get stuck on a hidden surface.
  useEffect(() => {
    if (mode === 'consumer' && activeTab === 'investigate') {
      onTabChange('chat')
    }
  }, [mode, activeTab, onTabChange])

  const handleTableSelect = (fqn: string) => {
    if (onTableSelect) {
      onTableSelect(fqn)
    } else {
      onTabChange('schema')
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        width: '100vw',
        overflow: 'hidden',
        background: '#1e1e2e',
        color: '#e0e0f0',
      }}
    >
      {/* Sidebar */}
      <Sidebar
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen((v) => !v)}
        onTableSelect={handleTableSelect}
      />

      {/* Main area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Tab bar */}
        <nav
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            padding: '0 16px',
            borderBottom: '1px solid #3a3a5c',
            background: '#2a2a3e',
            flexShrink: 0,
            height: 44,
          }}
        >
          {visibleTabs.map((tab) => {
            const isActive = activeTab === tab
            return (
              <button
                key={tab}
                onClick={() => onTabChange(tab)}
                style={{
                  padding: '0 16px',
                  height: 44,
                  background: 'none',
                  border: 'none',
                  borderBottom: isActive ? '2px solid #7c6af7' : '2px solid transparent',
                  color: isActive ? '#7c6af7' : '#9090a8',
                  fontWeight: isActive ? 600 : 400,
                  fontSize: 13,
                  cursor: 'pointer',
                  transition: 'color 0.15s, border-color 0.15s',
                  whiteSpace: 'nowrap',
                }}
              >
                {TAB_LABELS[tab]}
              </button>
            )
          })}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            <ModeToggle />
          </div>
        </nav>

        {/* Page content */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {children}
        </div>
      </div>
    </div>
  )
}
