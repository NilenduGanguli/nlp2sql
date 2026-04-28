import React from 'react'
import { useUserMode } from '../../hooks/useUserMode'

interface Props {
  onRefine: () => void
  onBranch: () => void
  onSaveAsPattern: () => void
  saved?: boolean
}

export const RefineButton: React.FC<Props> = ({ onRefine, onBranch, onSaveAsPattern, saved }) => {
  const { mode } = useUserMode()
  const btn: React.CSSProperties = {
    fontSize: 11,
    padding: '3px 10px',
    background: 'transparent',
    border: '1px solid #4b5563',
    color: '#c7c8d6',
    borderRadius: 4,
    cursor: 'pointer',
    fontWeight: 500,
  }
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      <button style={btn} onClick={onRefine} title="Refine the prior SQL">↻ Refine</button>
      <button style={btn} onClick={onBranch} title="Start a new question, keeping this for reference">⤴ Branch</button>
      {mode === 'curator' && (
        <button
          style={{ ...btn, color: saved ? '#4ade80' : btn.color }}
          onClick={onSaveAsPattern}
          disabled={saved}
          title="Promote to verified pattern"
        >
          {saved ? '★ Saved' : '★ Save as pattern'}
        </button>
      )}
    </div>
  )
}
