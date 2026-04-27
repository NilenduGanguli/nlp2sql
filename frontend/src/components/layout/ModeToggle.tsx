import React from 'react'
import { useUserMode } from '../../hooks/useUserMode'

const CURATOR_COLOR = '#7c6af7'
const CONSUMER_COLOR = '#10b981'

export const ModeToggle: React.FC = () => {
  const { mode, setMode } = useUserMode()
  const isCurator = mode === 'curator'
  const nextMode = isCurator ? 'consumer' : 'curator'
  return (
    <button
      onClick={() => setMode(nextMode)}
      title={`Switch to ${nextMode} mode`}
      aria-pressed={isCurator}
      aria-label={`User mode: ${mode}. Click to switch to ${nextMode}.`}
      style={{
        padding: '4px 10px',
        fontSize: 12,
        background: isCurator ? CURATOR_COLOR : CONSUMER_COLOR,
        color: 'white',
        border: 'none',
        borderRadius: 6,
        cursor: 'pointer',
        fontWeight: 600,
      }}
    >
      {isCurator ? '🛠 Curator' : '👤 Consumer'}
    </button>
  )
}
