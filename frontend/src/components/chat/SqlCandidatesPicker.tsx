import React, { useState } from 'react'

interface SqlCandidate {
  id: string
  interpretation: string
  sql: string
  explanation: string
}

interface SqlCandidatesPickerProps {
  candidates: SqlCandidate[]
  /** Called when the user clicks "Accept Selected & Run". */
  onAccept: (
    accepted: SqlCandidate[],
    rejected: SqlCandidate[],
    executedId: string,
  ) => void
  reusedFromSession?: boolean
}

export const SqlCandidatesPicker: React.FC<SqlCandidatesPickerProps> = ({
  candidates,
  onAccept,
  reusedFromSession,
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [checkedIds, setCheckedIds] = useState<Set<string>>(
    new Set(candidates[0] ? [candidates[0].id] : []),
  )
  const [executeId, setExecuteId] = useState<string>(candidates[0]?.id ?? '')
  const [submitted, setSubmitted] = useState(false)

  const toggleChecked = (id: string) => {
    setCheckedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      // Execute id must always be a checked candidate
      if (!next.has(executeId) && next.size > 0) {
        setExecuteId(Array.from(next)[0])
      }
      return next
    })
  }

  const handleAccept = () => {
    if (submitted) return
    const accepted = candidates.filter((c) => checkedIds.has(c.id))
    const rejected = candidates.filter((c) => !checkedIds.has(c.id))
    if (accepted.length === 0 || !executeId) return
    setSubmitted(true)
    onAccept(accepted, rejected, executeId)
  }

  const headerLabel = reusedFromSession
    ? 'Reused from learned session'
    : `Multiple Interpretations Found (${candidates.length})`

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
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid #2a2a3e',
          background: reusedFromSession ? 'rgba(74,222,128,0.08)' : '#242438',
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
          {reusedFromSession ? '\u267B ' : ''}
          {headerLabel}
        </div>
        <div style={{ fontSize: 12, color: '#7a7a9a', lineHeight: 1.5 }}>
          Check each interpretation that is valid for your question. Pick one to execute now.
          The set you accept will be remembered so we can answer similar questions without re-asking.
        </div>
      </div>

      <div
        style={{
          padding: '8px 12px 12px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        {candidates.map((candidate, index) => {
          const isChecked = checkedIds.has(candidate.id)
          const isExecute = executeId === candidate.id
          const isExpanded = expandedId === candidate.id

          return (
            <div
              key={candidate.id}
              style={{
                background: isChecked ? 'rgba(124,106,247,0.12)' : 'rgba(42,42,62,0.6)',
                border: `1px solid ${isChecked ? '#7c6af7' : '#3a3a5c'}`,
                borderRadius: 8,
                overflow: 'hidden',
                transition: 'all 0.2s',
              }}
            >
              <div style={{ padding: '10px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => toggleChecked(candidate.id)}
                    disabled={submitted}
                    style={{ marginTop: 4, accentColor: '#7c6af7' }}
                  />
                  <span
                    style={{
                      width: 22,
                      height: 22,
                      borderRadius: '50%',
                      background: isChecked ? '#7c6af7' : 'rgba(124,106,247,0.18)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 11,
                      fontWeight: 600,
                      color: isChecked ? '#fff' : '#a5b4fc',
                      flexShrink: 0,
                    }}
                  >
                    {index + 1}
                  </span>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 600,
                        color: '#e0e0f0',
                        lineHeight: 1.5,
                        marginBottom: 4,
                      }}
                    >
                      {candidate.interpretation}
                    </div>
                    <div
                      style={{
                        fontSize: 12,
                        fontStyle: 'italic',
                        color: '#7a7a9a',
                        lineHeight: 1.5,
                      }}
                    >
                      {candidate.explanation}
                    </div>
                  </div>
                </div>

                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    marginTop: 8,
                    marginLeft: 32,
                  }}
                >
                  <label
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      fontSize: 11,
                      color: isChecked ? '#a5b4fc' : '#5a5a7a',
                    }}
                  >
                    <input
                      type="radio"
                      name="execute_candidate"
                      checked={isExecute}
                      disabled={!isChecked || submitted}
                      onChange={() => setExecuteId(candidate.id)}
                      style={{ accentColor: '#7c6af7' }}
                    />
                    Execute this one
                  </label>
                  <button
                    onClick={() =>
                      setExpandedId((p) => (p === candidate.id ? null : candidate.id))
                    }
                    disabled={submitted}
                    style={{
                      padding: '4px 10px',
                      background: 'transparent',
                      border: '1px solid #3a3a5c',
                      borderRadius: 5,
                      color: '#8a8aac',
                      fontSize: 11,
                      cursor: 'pointer',
                      fontFamily: 'ui-monospace, Consolas, monospace',
                    }}
                  >
                    {isExpanded ? 'Hide SQL \u25B4' : 'Show SQL \u25BE'}
                  </button>
                </div>
              </div>

              {isExpanded && (
                <pre
                  style={{
                    margin: 0,
                    padding: '12px 14px',
                    fontFamily: 'ui-monospace, Consolas, monospace',
                    fontSize: 11,
                    color: '#a5b4fc',
                    overflowX: 'auto',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    maxHeight: 200,
                    overflowY: 'auto',
                    borderTop: '1px solid #3a3a5c',
                    background: '#1a1a2e',
                    lineHeight: 1.6,
                  }}
                >
                  {candidate.sql}
                </pre>
              )}
            </div>
          )
        })}

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 10,
            marginTop: 4,
          }}
        >
          <button
            onClick={handleAccept}
            disabled={submitted || checkedIds.size === 0 || !executeId}
            style={{
              padding: '8px 16px',
              background: submitted ? '#4ade80' : '#7c6af7',
              border: 'none',
              borderRadius: 6,
              color: '#fff',
              fontSize: 13,
              fontWeight: 600,
              cursor: submitted || checkedIds.size === 0 ? 'default' : 'pointer',
              opacity: submitted || checkedIds.size === 0 ? 0.6 : 1,
            }}
          >
            {submitted ? '\u2713 Saved' : `Accept Selected (${checkedIds.size}) & Run`}
          </button>
        </div>
      </div>
    </div>
  )
}
