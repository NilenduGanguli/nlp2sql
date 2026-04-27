import React from 'react'
import { useUserMode } from '../../hooks/useUserMode'

export const ModeToggle: React.FC = () => {
  const { mode, setMode } = useUserMode()
  const next = mode === 'curator' ? 'consumer' : 'curator'
  return (
    <button
      onClick={() => setMode(next)}
      title={`Switch to ${next} mode`}
      style={{
        padding: '4px 10px',
        fontSize: 12,
        background: mode === 'curator' ? '#7c6af7' : '#10b981',
        color: 'white',
        border: 'none',
        borderRadius: 6,
        cursor: 'pointer',
        fontWeight: 600,
      }}
    >
      {mode === 'curator' ? '🛠 Curator' : '👤 Consumer'}
    </button>
  )
}
