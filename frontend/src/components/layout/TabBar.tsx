import React from 'react'

type TabId = 'chat' | 'editor' | 'graph' | 'relationships'

interface TabBarProps {
  activeTab: TabId
  onTabChange: (tab: TabId) => void
}

const TABS: { id: TabId; label: string }[] = [
  { id: 'chat', label: 'Chat' },
  { id: 'editor', label: 'SQL Editor' },
  { id: 'graph', label: 'Knowledge Graph' },
  { id: 'relationships', label: 'Relationships' },
]

export const TabBar: React.FC<TabBarProps> = ({ activeTab, onTabChange }) => {
  return (
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
      {TABS.map(({ id, label }) => {
        const isActive = activeTab === id
        return (
          <button
            key={id}
            onClick={() => onTabChange(id)}
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
            {label}
          </button>
        )
      })}
    </nav>
  )
}
