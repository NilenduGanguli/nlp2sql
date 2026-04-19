import React, { useState } from 'react'

interface SqlCandidate {
  id: string
  interpretation: string
  sql: string
  explanation: string
}

interface SqlCandidatesPickerProps {
  candidates: SqlCandidate[]
  onSelect: (candidate: SqlCandidate) => void
  selected?: string // id of selected candidate (after user picks one)
}

export const SqlCandidatesPicker: React.FC<SqlCandidatesPickerProps> = ({
  candidates,
  onSelect,
  selected,
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id))
  }

  return (
    <div
      style={{
        background: '#1e1e2e',
        border: '1px solid #2a2a3e',
        borderRadius: 12,
        overflow: 'hidden',
        maxWidth: '100%',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid #2a2a3e',
          background: '#242438',
        }}
      >
        <div
          style={{
            fontSize: 14,
            fontWeight: 600,
            color: '#e0e0f0',
            marginBottom: 4,
          }}
        >
          Multiple Interpretations Found
        </div>
        <div style={{ fontSize: 12, color: '#7a7a9a', lineHeight: 1.5 }}>
          Your query can be interpreted in {candidates.length} different ways.
          Review and select the one that best matches your intent.
        </div>
      </div>

      {/* Candidate cards */}
      <div style={{ padding: '8px 12px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {candidates.map((candidate, index) => {
          const isSelected = selected === candidate.id
          const isGreyedOut = selected != null && !isSelected
          const isExpanded = expandedId === candidate.id

          return (
            <div
              key={candidate.id}
              style={{
                background: isSelected
                  ? 'rgba(124,106,247,0.12)'
                  : isGreyedOut
                    ? 'rgba(30,30,46,0.5)'
                    : 'rgba(42,42,62,0.6)',
                border: `1px solid ${
                  isSelected
                    ? '#7c6af7'
                    : isGreyedOut
                      ? '#252538'
                      : '#3a3a5c'
                }`,
                borderRadius: 8,
                overflow: 'hidden',
                opacity: isGreyedOut ? 0.45 : 1,
                transition: 'all 0.2s',
              }}
            >
              {/* Candidate header */}
              <div style={{ padding: '10px 14px' }}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 10,
                    marginBottom: 6,
                  }}
                >
                  {/* Index badge */}
                  <span
                    style={{
                      width: 22,
                      height: 22,
                      borderRadius: '50%',
                      background: isSelected
                        ? '#7c6af7'
                        : 'rgba(124,106,247,0.18)',
                      border: `1px solid ${isSelected ? '#7c6af7' : 'rgba(124,106,247,0.35)'}`,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 11,
                      fontWeight: 600,
                      color: isSelected ? '#fff' : '#a5b4fc',
                      flexShrink: 0,
                      marginTop: 1,
                    }}
                  >
                    {isSelected ? '\u2713' : index + 1}
                  </span>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    {/* Interpretation text */}
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 600,
                        color: isGreyedOut ? '#5a5a7a' : '#e0e0f0',
                        lineHeight: 1.5,
                        marginBottom: 4,
                      }}
                    >
                      {candidate.interpretation}
                    </div>

                    {/* Explanation */}
                    <div
                      style={{
                        fontSize: 12,
                        fontStyle: 'italic',
                        color: isGreyedOut ? '#4a4a6a' : '#7a7a9a',
                        lineHeight: 1.5,
                      }}
                    >
                      {candidate.explanation}
                    </div>
                  </div>
                </div>

                {/* SQL toggle + select button row */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    marginTop: 8,
                    marginLeft: 32,
                  }}
                >
                  <button
                    onClick={() => toggleExpand(candidate.id)}
                    style={{
                      padding: '4px 10px',
                      background: 'transparent',
                      border: `1px solid ${isGreyedOut ? '#252538' : '#3a3a5c'}`,
                      borderRadius: 5,
                      color: isGreyedOut ? '#4a4a6a' : '#8a8aac',
                      fontSize: 11,
                      cursor: 'pointer',
                      fontFamily: 'ui-monospace, Consolas, monospace',
                      transition: 'all 0.15s',
                    }}
                    onMouseEnter={(e) => {
                      if (!isGreyedOut) {
                        ;(e.currentTarget as HTMLElement).style.borderColor = '#7c6af7'
                        ;(e.currentTarget as HTMLElement).style.color = '#a5b4fc'
                      }
                    }}
                    onMouseLeave={(e) => {
                      if (!isGreyedOut) {
                        ;(e.currentTarget as HTMLElement).style.borderColor = '#3a3a5c'
                        ;(e.currentTarget as HTMLElement).style.color = '#8a8aac'
                      }
                    }}
                  >
                    {isExpanded ? 'Hide SQL \u25B4' : 'Show SQL \u25BE'}
                  </button>

                  {!selected && (
                    <button
                      onClick={() => onSelect(candidate)}
                      style={{
                        padding: '5px 14px',
                        background: '#7c6af7',
                        border: 'none',
                        borderRadius: 5,
                        color: '#fff',
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: 'pointer',
                        transition: 'background 0.15s',
                      }}
                      onMouseEnter={(e) => {
                        ;(e.currentTarget as HTMLElement).style.background = '#6b5ce6'
                      }}
                      onMouseLeave={(e) => {
                        ;(e.currentTarget as HTMLElement).style.background = '#7c6af7'
                      }}
                    >
                      Select This
                    </button>
                  )}

                  {isSelected && (
                    <span
                      style={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: '#4ade80',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                      }}
                    >
                      <span
                        style={{
                          width: 14,
                          height: 14,
                          borderRadius: '50%',
                          background: 'rgba(74,222,128,0.2)',
                          border: '1px solid rgba(74,222,128,0.4)',
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: 9,
                        }}
                      >
                        {'\u2713'}
                      </span>
                      Selected
                    </span>
                  )}
                </div>
              </div>

              {/* Expandable SQL preview */}
              {isExpanded && (
                <div
                  style={{
                    borderTop: `1px solid ${isGreyedOut ? '#252538' : '#3a3a5c'}`,
                    background: '#1a1a2e',
                  }}
                >
                  <pre
                    style={{
                      margin: 0,
                      padding: '12px 14px',
                      fontFamily: 'ui-monospace, Consolas, monospace',
                      fontSize: 11,
                      color: isGreyedOut ? '#4a4a6a' : '#a5b4fc',
                      overflowX: 'auto',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-all',
                      maxHeight: 200,
                      overflowY: 'auto',
                      lineHeight: 1.6,
                    }}
                  >
                    {candidate.sql}
                  </pre>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
