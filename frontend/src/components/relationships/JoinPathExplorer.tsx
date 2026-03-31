import React, { useState, useMemo } from 'react'
import { useTables } from '../../hooks/useTables'
import { useJoinPath } from '../../hooks/useJoinPath'

interface TableSelectProps {
  label: string
  value: string
  onChange: (v: string) => void
  tables: Array<{ fqn: string; name: string; schema_name: string }>
  exclude?: string
}

const TableSelect: React.FC<TableSelectProps> = ({ label, value, onChange, tables, exclude }) => {
  const [search, setSearch] = useState('')

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return tables
      .filter((t) => t.fqn !== exclude)
      .filter(
        (t) =>
          !q ||
          t.name.toLowerCase().includes(q) ||
          t.schema_name.toLowerCase().includes(q) ||
          t.fqn.toLowerCase().includes(q),
      )
      .slice(0, 100)
  }, [tables, search, exclude])

  return (
    <div style={{ flex: 1 }}>
      <label style={{ fontSize: 11, color: '#9090a8', display: 'block', marginBottom: 4 }}>
        {label}
      </label>
      <input
        type="text"
        placeholder="Search tables…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        style={{
          width: '100%',
          background: '#1e1e2e',
          border: '1px solid #3a3a5c',
          borderRadius: 4,
          padding: '5px 10px',
          color: '#e0e0f0',
          fontSize: 12,
          outline: 'none',
          marginBottom: 4,
          boxSizing: 'border-box',
        }}
        onFocus={(e) => (e.target.style.borderColor = '#7c6af7')}
        onBlur={(e) => (e.target.style.borderColor = '#3a3a5c')}
      />
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        size={6}
        style={{
          width: '100%',
          background: '#1e1e2e',
          border: '1px solid #3a3a5c',
          borderRadius: 4,
          color: '#e0e0f0',
          fontSize: 12,
          padding: '2px',
        }}
      >
        <option value="">— select a table —</option>
        {filtered.map((t) => (
          <option key={t.fqn} value={t.fqn}>
            {t.schema_name}.{t.name}
          </option>
        ))}
      </select>
    </div>
  )
}

export const JoinPathExplorer: React.FC = () => {
  const { data: tables } = useTables()
  const [tableA, setTableA] = useState('')
  const [tableB, setTableB] = useState('')
  const [submitted, setSubmitted] = useState<{ from: string; to: string } | null>(null)

  const { data: joinPath, isLoading, error } = useJoinPath(
    submitted?.from ?? '',
    submitted?.to ?? '',
  )

  const tableList = useMemo(
    () => tables ?? [],
    [tables],
  )

  const handleFind = () => {
    if (tableA && tableB && tableA !== tableB) {
      setSubmitted({ from: tableA, to: tableB })
    }
  }

  return (
    <div
      style={{
        border: '1px solid #3a3a5c',
        borderRadius: 8,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '10px 14px',
          borderBottom: '1px solid #3a3a5c',
          background: '#2a2a3e',
          fontWeight: 600,
          fontSize: 13,
          color: '#e0e0f0',
        }}
      >
        Join Path Explorer
      </div>

      <div style={{ padding: 14 }}>
        {/* Table selectors */}
        <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
          <TableSelect
            label="Table A"
            value={tableA}
            onChange={setTableA}
            tables={tableList}
            exclude={tableB}
          />
          <TableSelect
            label="Table B"
            value={tableB}
            onChange={setTableB}
            tables={tableList}
            exclude={tableA}
          />
        </div>

        <button
          onClick={handleFind}
          disabled={!tableA || !tableB || tableA === tableB}
          style={{
            padding: '7px 20px',
            background: tableA && tableB && tableA !== tableB ? '#7c6af7' : '#3a3a5c',
            border: 'none',
            borderRadius: 6,
            color: tableA && tableB && tableA !== tableB ? '#fff' : '#6a6a8a',
            fontSize: 13,
            fontWeight: 600,
            cursor: tableA && tableB && tableA !== tableB ? 'pointer' : 'not-allowed',
            marginBottom: 12,
          }}
        >
          Find Join Path
        </button>

        {/* Result */}
        {isLoading && (
          <div style={{ color: '#9090a8', fontSize: 13 }}>Finding join path…</div>
        )}
        {error && (
          <div style={{ color: '#f87171', fontSize: 13 }}>
            Failed to find join path
          </div>
        )}
        {joinPath && !isLoading && (
          <div
            style={{
              background: '#1e1e2e',
              border: '1px solid #3a3a5c',
              borderRadius: 6,
              padding: 12,
            }}
          >
            {joinPath.found ? (
              <>
                <div style={{ display: 'flex', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
                  <span style={{ fontSize: 11, color: '#9090a8' }}>
                    Hops: <strong style={{ color: '#e0e0f0' }}>{joinPath.hops}</strong>
                  </span>
                  <span style={{ fontSize: 11, color: '#9090a8' }}>
                    Type: <strong style={{ color: '#e0e0f0' }}>{joinPath.join_type}</strong>
                  </span>
                  <span style={{ fontSize: 11, color: '#9090a8' }}>
                    Source: <strong style={{ color: '#e0e0f0' }}>{joinPath.source}</strong>
                  </span>
                </div>

                {joinPath.join_columns.length > 0 && (
                  <div style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 11, color: '#9090a8', marginBottom: 4 }}>
                      Join columns:
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {joinPath.join_columns.map((col) => (
                        <span
                          key={col.src}
                          style={{
                            fontSize: 11,
                            padding: '2px 8px',
                            background: 'rgba(124,106,247,0.15)',
                            border: '1px solid rgba(124,106,247,0.3)',
                            borderRadius: 4,
                            color: '#a5b4fc',
                            fontFamily: 'ui-monospace, monospace',
                          }}
                        >
                          {col.src} = {col.tgt}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {joinPath.sql_snippet && (
                  <div>
                    <div style={{ fontSize: 11, color: '#9090a8', marginBottom: 4 }}>
                      SQL snippet:
                    </div>
                    <pre
                      style={{
                        margin: 0,
                        padding: '8px 10px',
                        background: '#1a1a2e',
                        borderRadius: 4,
                        fontFamily: 'ui-monospace, Consolas, monospace',
                        fontSize: 11,
                        color: '#a5b4fc',
                        overflowX: 'auto',
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      {joinPath.sql_snippet}
                    </pre>
                  </div>
                )}
              </>
            ) : (
              <div style={{ color: '#fbbf24', fontSize: 13 }}>
                No join path found between these tables.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
