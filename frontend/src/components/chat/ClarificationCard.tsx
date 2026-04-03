import React, { useState } from 'react'

interface ClarificationCardProps {
  question: string
  options: string[]
  onAnswer: (answer: string) => void
  answered?: boolean
}

export const ClarificationCard: React.FC<ClarificationCardProps> = ({
  question,
  options,
  onAnswer,
  answered = false,
}) => {
  const [selected, setSelected] = useState<string | null>(null)
  const [freeform, setFreeform] = useState('')

  const handleSelect = (opt: string) => {
    if (answered) return
    setSelected(opt)
    onAnswer(opt)
  }

  const handleFreeformSubmit = () => {
    if (answered || !freeform.trim()) return
    onAnswer(freeform.trim())
  }

  return (
    <div
      style={{
        background: 'rgba(124,106,247,0.07)',
        border: '1px solid rgba(124,106,247,0.28)',
        borderRadius: 10,
        padding: '12px 16px',
        maxWidth: '82%',
      }}
    >
      {/* Agent indicator + question */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 10 }}>
        <div
          style={{
            width: 26,
            height: 26,
            borderRadius: '50%',
            background: 'rgba(124,106,247,0.18)',
            border: '1px solid rgba(124,106,247,0.35)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 13,
            flexShrink: 0,
            marginTop: 1,
          }}
        >
          ?
        </div>
        <div style={{ fontSize: 13, color: '#c8c8e0', lineHeight: 1.6 }}>{question}</div>
      </div>

      {/* Option buttons */}
      {options.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {options.map((opt) => (
            <button
              key={opt}
              onClick={() => handleSelect(opt)}
              disabled={answered}
              style={{
                padding: '5px 14px',
                background:
                  selected === opt ? 'rgba(124,106,247,0.3)' : 'rgba(124,106,247,0.08)',
                border: `1px solid ${selected === opt ? '#7c6af7' : 'rgba(124,106,247,0.28)'}`,
                borderRadius: 999,
                color: answered ? '#5a5a7a' : '#a5b4fc',
                fontSize: 12,
                cursor: answered ? 'default' : 'pointer',
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                if (!answered)
                  (e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.2)'
              }}
              onMouseLeave={(e) => {
                if (!answered && selected !== opt)
                  (e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.08)'
              }}
            >
              {opt}
            </button>
          ))}
        </div>
      )}

      {/* Free-form input for open-ended questions */}
      {options.length === 0 && !answered && (
        <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
          <input
            value={freeform}
            onChange={(e) => setFreeform(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleFreeformSubmit()
            }}
            placeholder="Type your answer…"
            autoFocus
            style={{
              flex: 1,
              background: '#1e1e2e',
              border: '1px solid #3a3a5c',
              borderRadius: 6,
              padding: '5px 10px',
              color: '#e0e0f0',
              fontSize: 12,
              outline: 'none',
            }}
            onFocus={(e) => (e.target.style.borderColor = '#7c6af7')}
            onBlur={(e) => (e.target.style.borderColor = '#3a3a5c')}
          />
          <button
            onClick={handleFreeformSubmit}
            disabled={!freeform.trim()}
            style={{
              padding: '5px 14px',
              background: freeform.trim() ? '#7c6af7' : '#3a3a5c',
              border: 'none',
              borderRadius: 6,
              color: freeform.trim() ? '#fff' : '#6a6a8a',
              fontSize: 12,
              cursor: freeform.trim() ? 'pointer' : 'not-allowed',
            }}
          >
            Send
          </button>
        </div>
      )}

      {answered && (
        <div style={{ fontSize: 10, color: '#4a4a6a', marginTop: 6 }}>✓ Answered</div>
      )}
    </div>
  )
}
