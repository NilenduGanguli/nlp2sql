import React, { useCallback } from 'react'

interface SqlPreviewCardProps {
  sql: string
  explanation: string
  validationPassed: boolean
  validationErrors: string[]
  onRunQuery: (sql: string) => void
  onOpenInEditor?: (sql: string) => void
  onAcceptQuery?: (sql: string, accepted: boolean) => void
  isExecuting?: boolean
  executed?: boolean
}

export const SqlPreviewCard: React.FC<SqlPreviewCardProps> = ({
  sql,
  explanation,
  validationPassed,
  validationErrors,
  onRunQuery,
  onOpenInEditor,
  onAcceptQuery,
  isExecuting = false,
  executed = false,
}) => {
  const [copied, setCopied] = React.useState(false)
  const [feedback, setFeedback] = React.useState<'accepted' | 'rejected' | null>(null)

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(sql)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [sql])

  const handleAccept = useCallback(() => {
    setFeedback('accepted')
    onAcceptQuery?.(sql, true)
  }, [sql, onAcceptQuery])

  const handleReject = useCallback(() => {
    setFeedback('rejected')
    onAcceptQuery?.(sql, false)
  }, [sql, onAcceptQuery])

  return (
    <div
      style={{
        background: '#2a2a3e',
        border: '1px solid #3a3a5c',
        borderRadius: 8,
        overflow: 'hidden',
        maxWidth: '100%',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 14px',
          borderBottom: '1px solid #3a3a5c',
          background: '#242438',
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: '#e0e0f0' }}>
          SQL Preview
        </span>

        {/* Validation badge */}
        {validationPassed ? (
          <span
            style={{
              fontSize: 10,
              padding: '2px 8px',
              borderRadius: 999,
              background: 'rgba(74,222,128,0.15)',
              color: '#4ade80',
              fontWeight: 600,
            }}
          >
            Validated
          </span>
        ) : (
          <span
            style={{
              fontSize: 10,
              padding: '2px 8px',
              borderRadius: 999,
              background: 'rgba(251,191,36,0.15)',
              color: '#fbbf24',
              fontWeight: 600,
            }}
          >
            {validationErrors.length} warning{validationErrors.length !== 1 ? 's' : ''}
          </span>
        )}

        {executed && (
          <span
            style={{
              fontSize: 10,
              padding: '2px 8px',
              borderRadius: 999,
              background: 'rgba(99,102,241,0.15)',
              color: '#818cf8',
              fontWeight: 600,
            }}
          >
            Executed
          </span>
        )}

        <div style={{ flex: 1 }} />
      </div>

      {/* SQL block */}
      <div style={{ position: 'relative', background: '#1a1a2e' }}>
        <pre
          style={{
            margin: 0,
            padding: '16px 40px 16px 16px',
            fontFamily: 'ui-monospace, Consolas, monospace',
            fontSize: 12,
            color: '#a5b4fc',
            overflowX: 'auto',
            overflowY: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
            maxHeight: 300,
            borderRadius: 0,
            lineHeight: 1.6,
          }}
        >
          {sql}
        </pre>
        <button
          onClick={handleCopy}
          title="Copy SQL"
          style={{
            position: 'absolute',
            top: 8,
            right: 8,
            background: 'rgba(60,60,80,0.8)',
            border: '1px solid #4a4a6c',
            borderRadius: 4,
            color: copied ? '#4ade80' : '#9090a8',
            fontSize: 11,
            padding: '2px 8px',
            cursor: 'pointer',
          }}
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>

      {/* Explanation */}
      {explanation && (
        <div
          style={{
            padding: '10px 14px',
            fontSize: 13,
            color: '#c0c0d8',
            borderTop: '1px solid #3a3a5c',
            lineHeight: 1.55,
          }}
        >
          {explanation}
        </div>
      )}

      {/* Validation errors */}
      {!validationPassed && validationErrors.length > 0 && (
        <div
          style={{
            padding: '8px 14px',
            borderTop: '1px solid #3a3a5c',
            background: 'rgba(251,191,36,0.05)',
          }}
        >
          <div style={{ fontSize: 11, fontWeight: 600, color: '#fbbf24', marginBottom: 4 }}>
            Validation Warnings:
          </div>
          {validationErrors.map((err, i) => (
            <div
              key={i}
              style={{
                fontSize: 12,
                color: '#fbbf24',
                padding: '2px 0',
                lineHeight: 1.4,
              }}
            >
              - {err}
            </div>
          ))}
        </div>
      )}

      {/* Action buttons — hidden once executed */}
      {!executed && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 14px',
            borderTop: '1px solid #3a3a5c',
          }}
        >
          <button
            onClick={() => onRunQuery(sql)}
            disabled={isExecuting}
            style={{
              padding: '8px 20px',
              background: isExecuting ? '#3a3a5c' : '#7c6af7',
              border: 'none',
              borderRadius: 6,
              color: isExecuting ? '#6a6a8a' : '#fff',
              fontSize: 13,
              fontWeight: 600,
              cursor: isExecuting ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              transition: 'background 0.15s',
            }}
          >
            {isExecuting && (
              <span
                style={{
                  display: 'inline-block',
                  width: 12,
                  height: 12,
                  border: '2px solid rgba(255,255,255,0.3)',
                  borderTopColor: '#fff',
                  borderRadius: '50%',
                  animation: 'sql-preview-spin 0.6s linear infinite',
                }}
              />
            )}
            {isExecuting ? 'Running...' : 'Run Query'}
          </button>
          {onOpenInEditor && (
            <button
              onClick={() => onOpenInEditor(sql)}
              disabled={isExecuting}
              style={{
                padding: '8px 16px',
                background: 'rgba(124,106,247,0.15)',
                border: '1px solid rgba(124,106,247,0.4)',
                borderRadius: 6,
                color: isExecuting ? '#5a5a7a' : '#7c6af7',
                fontSize: 13,
                fontWeight: 500,
                cursor: isExecuting ? 'not-allowed' : 'pointer',
                transition: 'all 0.15s',
              }}
            >
              Open in Editor
            </button>
          )}

          <style>{`
            @keyframes sql-preview-spin {
              to { transform: rotate(360deg); }
            }
          `}</style>
        </div>
      )}
      {/* Accept / Reject feedback */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 14px',
          borderTop: '1px solid #3a3a5c',
        }}
      >
        <span style={{ fontSize: 12, color: '#7a7a9a', marginRight: 4 }}>
          Accept generated query?
        </span>
        {feedback === null ? (
          <>
            <button
              onClick={handleAccept}
              title="Accept — save this query pattern to knowledge base"
              style={{
                padding: '4px 12px',
                background: 'rgba(74,222,128,0.1)',
                border: '1px solid rgba(74,222,128,0.3)',
                borderRadius: 6,
                color: '#4ade80',
                fontSize: 16,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                ;(e.currentTarget as HTMLElement).style.background = 'rgba(74,222,128,0.2)'
              }}
              onMouseLeave={(e) => {
                ;(e.currentTarget as HTMLElement).style.background = 'rgba(74,222,128,0.1)'
              }}
            >
              {'👍'}
            </button>
            <button
              onClick={handleReject}
              title="Reject — do not learn from this query"
              style={{
                padding: '4px 12px',
                background: 'rgba(248,113,113,0.1)',
                border: '1px solid rgba(248,113,113,0.3)',
                borderRadius: 6,
                color: '#f87171',
                fontSize: 16,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                transition: 'all 0.15s',
              }}
              onMouseEnter={(e) => {
                ;(e.currentTarget as HTMLElement).style.background = 'rgba(248,113,113,0.2)'
              }}
              onMouseLeave={(e) => {
                ;(e.currentTarget as HTMLElement).style.background = 'rgba(248,113,113,0.1)'
              }}
            >
              {'👎'}
            </button>
          </>
        ) : (
          <span
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: feedback === 'accepted' ? '#4ade80' : '#f87171',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
            }}
          >
            {feedback === 'accepted' ? '👍 Accepted — saved to knowledge base' : '👎 Rejected'}
          </span>
        )}
      </div>
    </div>
  )
}
