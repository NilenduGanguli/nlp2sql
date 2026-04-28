import React from 'react'

interface VerifiedPattern {
  pattern_id: string
  exemplar_query: string
  exemplar_sql: string
  tables_used: string[]
  accept_count: number
  consumer_uses: number
  negative_signals: number
  score: number
  promoted_at: number
  manual_promotion: boolean
  source_entry_ids: string[]
}

export const PatternsTab: React.FC = () => {
  const [patterns, setPatterns] = React.useState<VerifiedPattern[]>([])
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    fetch('/api/kyc-agent/verified-patterns')
      .then((r) => r.json())
      .then((data) => setPatterns(data.patterns || []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: 16 }}>Loading patterns…</div>
  if (patterns.length === 0)
    return (
      <div style={{ padding: 16, color: '#9090a8' }}>
        No verified patterns yet. Curator accepts will populate this list.
      </div>
    )

  return (
    <div style={{ padding: 12, overflowY: 'auto' }}>
      {patterns.map((p) => (
        <div
          key={p.pattern_id}
          style={{
            background: '#2a2a3e',
            border: '1px solid #3a3a5c',
            borderRadius: 8,
            padding: 12,
            marginBottom: 8,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span
              style={{
                fontSize: 10,
                padding: '1px 6px',
                borderRadius: 999,
                background: 'rgba(74,222,128,0.15)',
                color: '#4ade80',
                fontWeight: 600,
              }}
            >
              ✓ Verified
            </span>
            {p.manual_promotion && (
              <span style={{ fontSize: 10, color: '#fbbf24' }}>★ manual</span>
            )}
            <span style={{ marginLeft: 'auto', fontSize: 11, color: '#9090a8' }}>
              score {p.score.toFixed(1)} · {p.accept_count} accepts · {p.consumer_uses} uses
            </span>
          </div>
          <div style={{ marginTop: 6, fontSize: 13, color: '#e5e7eb' }}>
            {p.exemplar_query}
          </div>
          <pre
            style={{
              marginTop: 6,
              fontSize: 11,
              background: '#1a1a2e',
              padding: 8,
              borderRadius: 4,
              color: '#a78bfa',
              whiteSpace: 'pre-wrap',
            }}
          >
            {p.exemplar_sql}
          </pre>
          {p.tables_used.length > 0 && (
            <div style={{ fontSize: 11, color: '#7c7c92' }}>
              tables: {p.tables_used.join(', ')}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
