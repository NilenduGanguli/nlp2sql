import React, { useState } from 'react'

interface ClarificationCardProps {
  question: string
  options: string[]
  context?: string       // agent's plain-English understanding summary
  multiSelect?: boolean  // true = user can pick multiple options (AND logic)
  onAnswer: (answer: string) => void
  answered?: boolean
}

export const ClarificationCard: React.FC<ClarificationCardProps> = ({
  question,
  options,
  context,
  multiSelect = false,
  onAnswer,
  answered = false,
}) => {
  const [selectedOptions, setSelectedOptions] = useState<string[]>([])
  const [showFreetext, setShowFreetext] = useState(false)
  const [freeform, setFreeform] = useState('')

  const toggleOption = (opt: string) => {
    if (answered) return
    if (multiSelect) {
      setSelectedOptions((prev) =>
        prev.includes(opt) ? prev.filter((o) => o !== opt) : [...prev, opt],
      )
    } else {
      // Single-select: submit immediately
      onAnswer(opt)
    }
  }

  const handleApplyMultiSelect = () => {
    if (answered || selectedOptions.length === 0) return
    onAnswer(selectedOptions.join(', '))
  }

  const handleFreeformSubmit = () => {
    if (answered || !freeform.trim()) return
    onAnswer(freeform.trim())
  }

  const isCustomOption = (opt: string) =>
    opt.toLowerCase().startsWith('custom') || opt.toLowerCase().includes("let me")

  return (
    <div
      style={{
        background: 'rgba(124,106,247,0.06)',
        border: '1px solid rgba(124,106,247,0.22)',
        borderRadius: 12,
        overflow: 'hidden',
        maxWidth: '88%',
      }}
    >
      {/* Agent understanding context */}
      {context && (
        <div
          style={{
            background: 'rgba(124,106,247,0.10)',
            borderBottom: '1px solid rgba(124,106,247,0.15)',
            padding: '10px 14px',
            display: 'flex',
            gap: 8,
            alignItems: 'flex-start',
          }}
        >
          <span style={{ fontSize: 14, lineHeight: 1, marginTop: 1, flexShrink: 0 }}>🧠</span>
          <div>
            <div style={{ fontSize: 10, fontWeight: 600, color: '#6a6af0', letterSpacing: '0.04em', marginBottom: 3, textTransform: 'uppercase' }}>
              My current understanding
            </div>
            <div style={{ fontSize: 12, color: '#b0b0d0', lineHeight: 1.55 }}>{context}</div>
          </div>
        </div>
      )}

      {/* Question */}
      <div style={{ padding: '12px 14px 6px' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9, marginBottom: 10 }}>
          <div
            style={{
              width: 24,
              height: 24,
              borderRadius: '50%',
              background: 'rgba(124,106,247,0.2)',
              border: '1px solid rgba(124,106,247,0.4)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 12,
              flexShrink: 0,
              marginTop: 1,
            }}
          >
            ?
          </div>
          <div style={{ fontSize: 13, color: '#d0d0f0', lineHeight: 1.6, fontWeight: 500 }}>
            {question}
          </div>
        </div>

        {/* Options grid */}
        {options.length > 0 && !showFreetext && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginBottom: 8 }}>
            {options.map((opt) => {
              const isCustom = isCustomOption(opt)
              const isSelected = selectedOptions.includes(opt)

              if (isCustom) {
                return (
                  <button
                    key={opt}
                    onClick={() => { if (!answered) setShowFreetext(true) }}
                    disabled={answered}
                    style={{
                      padding: '7px 12px',
                      background: 'transparent',
                      border: '1px dashed rgba(124,106,247,0.3)',
                      borderRadius: 7,
                      color: answered ? '#4a4a6a' : '#7c7cf0',
                      fontSize: 12,
                      cursor: answered ? 'default' : 'pointer',
                      textAlign: 'left',
                      fontStyle: 'italic',
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      if (!answered) (e.currentTarget as HTMLElement).style.borderColor = 'rgba(124,106,247,0.6)'
                    }}
                    onMouseLeave={(e) => {
                      if (!answered) (e.currentTarget as HTMLElement).style.borderColor = 'rgba(124,106,247,0.3)'
                    }}
                  >
                    ✏️ {opt}
                  </button>
                )
              }

              return (
                <button
                  key={opt}
                  onClick={() => toggleOption(opt)}
                  disabled={answered}
                  style={{
                    padding: '8px 12px',
                    background: isSelected
                      ? 'rgba(124,106,247,0.22)'
                      : 'rgba(255,255,255,0.03)',
                    border: `1px solid ${
                      isSelected ? '#7c6af7' : 'rgba(124,106,247,0.2)'
                    }`,
                    borderRadius: 7,
                    color: answered ? '#5a5a7a' : isSelected ? '#c4b5fd' : '#c0c0e0',
                    fontSize: 13,
                    cursor: answered ? 'default' : 'pointer',
                    textAlign: 'left',
                    fontWeight: isSelected ? 500 : 400,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    transition: 'all 0.12s',
                  }}
                  onMouseEnter={(e) => {
                    if (!answered && !isSelected)
                      (e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.12)'
                  }}
                  onMouseLeave={(e) => {
                    if (!answered && !isSelected)
                      (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.03)'
                  }}
                >
                  {multiSelect && (
                    <span
                      style={{
                        width: 14,
                        height: 14,
                        border: `2px solid ${isSelected ? '#7c6af7' : '#4a4a7a'}`,
                        borderRadius: 3,
                        background: isSelected ? '#7c6af7' : 'transparent',
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                        fontSize: 9,
                        color: '#fff',
                      }}
                    >
                      {isSelected ? '✓' : ''}
                    </span>
                  )}
                  {opt}
                </button>
              )
            })}

            {/* Multi-select apply button */}
            {multiSelect && selectedOptions.length > 0 && !answered && (
              <button
                onClick={handleApplyMultiSelect}
                style={{
                  marginTop: 4,
                  padding: '7px 16px',
                  background: '#7c6af7',
                  border: 'none',
                  borderRadius: 7,
                  color: '#fff',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: 'pointer',
                  alignSelf: 'flex-start',
                }}
              >
                Apply ({selectedOptions.length} selected)
              </button>
            )}
          </div>
        )}

        {/* Free-text input — shown when "Custom" is clicked or no options */}
        {(showFreetext || options.length === 0) && !answered && (
          <div style={{ marginTop: showFreetext ? 0 : 4 }}>
            {showFreetext && (
              <button
                onClick={() => setShowFreetext(false)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: '#7c7cf0',
                  fontSize: 11,
                  cursor: 'pointer',
                  padding: '0 0 6px 0',
                  textDecoration: 'underline',
                }}
              >
                ← Back to options
              </button>
            )}
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                value={freeform}
                onChange={(e) => setFreeform(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleFreeformSubmit() }}
                placeholder="Describe your requirement in plain English…"
                autoFocus={showFreetext}
                style={{
                  flex: 1,
                  background: '#1e1e2e',
                  border: '1px solid #3a3a5c',
                  borderRadius: 6,
                  padding: '6px 10px',
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
                  padding: '6px 14px',
                  background: freeform.trim() ? '#7c6af7' : '#3a3a5c',
                  border: 'none',
                  borderRadius: 6,
                  color: freeform.trim() ? '#fff' : '#6a6a8a',
                  fontSize: 12,
                  cursor: freeform.trim() ? 'pointer' : 'not-allowed',
                  whiteSpace: 'nowrap',
                }}
              >
                Send
              </button>
            </div>
          </div>
        )}

        {/* Answered state */}
        {answered && (
          <div style={{ fontSize: 10, color: '#4a4a6a', marginTop: 4, marginBottom: 4 }}>
            ✓ Answered — gathering more context…
          </div>
        )}
      </div>
    </div>
  )
}
