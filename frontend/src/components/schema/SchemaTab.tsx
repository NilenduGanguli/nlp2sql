import React, { useState } from 'react'
import { useTables, useTableDetail } from '../../hooks/useTables'
import { useSearch } from '../../hooks/useSearch'
import { SearchBox } from '../common/SearchBox'
import { TableList, useFilteredTables } from './TableList'
import type { ColumnDetail } from '../../types'

interface SchemaTabProps {
  /** FQN pre-selected from sidebar click */
  selectedTable?: string | null
}

function ColumnRow({ col }: { col: ColumnDetail }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '5px 0',
        borderBottom: '1px solid #3a3a5c',
        fontSize: 12,
      }}
    >
      <span
        style={{
          flex: '0 0 24px',
          textAlign: 'center',
          color: col.is_pk ? '#fbbf24' : col.is_fk ? '#7c6af7' : '#9090a8',
          fontSize: 11,
          fontWeight: 700,
        }}
        title={col.is_pk ? 'Primary key' : col.is_fk ? 'Foreign key' : ''}
      >
        {col.is_pk ? 'PK' : col.is_fk ? 'FK' : ''}
      </span>
      <span style={{ flex: '0 0 180px', color: '#e0e0f0', fontWeight: 600, fontFamily: 'monospace' }}>
        {col.name}
      </span>
      <span style={{ flex: '0 0 120px', color: '#9090a8', fontFamily: 'monospace' }}>
        {col.data_type}
      </span>
      <span
        style={{
          flex: '0 0 60px',
          color: col.nullable === 'Y' ? '#9090a8' : '#f87171',
          fontSize: 10,
          textTransform: 'uppercase',
        }}
      >
        {col.nullable === 'Y' ? 'null' : 'NOT NULL'}
      </span>
      <span style={{ flex: 1, color: '#9090a8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {col.comments}
      </span>
    </div>
  )
}

function TableDetail({ fqn, onClose }: { fqn: string; onClose: () => void }) {
  const { data: detail, isLoading } = useTableDetail(fqn, true)

  return (
    <div
      style={{
        flex: '0 0 420px',
        borderLeft: '1px solid #3a3a5c',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        background: '#2a2a3e',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '12px 16px',
          borderBottom: '1px solid #3a3a5c',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexShrink: 0,
        }}
      >
        <div>
          <span style={{ color: '#9090a8', fontSize: 11 }}>{detail?.schema_name}.</span>
          <span style={{ color: '#e0e0f0', fontSize: 14, fontWeight: 700 }}>
            {detail?.name ?? fqn}
          </span>
        </div>
        <button
          onClick={onClose}
          style={{ background: 'none', border: 'none', color: '#9090a8', fontSize: 18, lineHeight: 1, padding: 4 }}
        >
          ✕
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
        {isLoading && (
          <div style={{ color: '#9090a8', fontSize: 13, textAlign: 'center', paddingTop: 40 }}>
            Loading…
          </div>
        )}

        {detail && (
          <>
            {/* Summary badges */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
              {detail.importance_tier && (
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: '#7c6af7',
                    background: 'rgba(124,106,247,0.15)',
                    borderRadius: 4,
                    padding: '2px 7px',
                    textTransform: 'uppercase',
                  }}
                >
                  {detail.importance_tier}
                </span>
              )}
              {detail.row_count != null && (
                <span style={{ fontSize: 11, color: '#9090a8' }}>
                  {detail.row_count.toLocaleString()} rows
                </span>
              )}
              <span style={{ fontSize: 11, color: '#9090a8' }}>{detail.columns.length} cols</span>
            </div>

            {/* Description */}
            {(detail.comments || detail.llm_description) && (
              <p style={{ color: '#c0c0d8', fontSize: 13, lineHeight: 1.5, marginBottom: 16 }}>
                {detail.comments || detail.llm_description}
              </p>
            )}

            {/* Columns */}
            <div style={{ marginBottom: 16 }}>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: '#9090a8',
                  textTransform: 'uppercase',
                  letterSpacing: '0.06em',
                  marginBottom: 8,
                }}
              >
                Columns ({detail.columns.length})
              </div>
              {detail.columns.map((col) => (
                <ColumnRow key={col.name} col={col} />
              ))}
            </div>

            {/* Foreign keys */}
            {detail.foreign_keys?.length > 0 && (
              <div>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    color: '#9090a8',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    marginBottom: 8,
                  }}
                >
                  Foreign Keys ({detail.foreign_keys.length})
                </div>
                {detail.foreign_keys.map((fk, i) => (
                  <div
                    key={i}
                    style={{
                      fontSize: 11,
                      padding: '5px 0',
                      borderBottom: '1px solid #3a3a5c',
                      color: '#9090a8',
                    }}
                  >
                    <span style={{ color: '#7c6af7', fontFamily: 'monospace' }}>
                      {fk.fk_col}
                    </span>{' '}
                    →{' '}
                    <span style={{ color: '#4ade80', fontFamily: 'monospace' }}>
                      {fk.ref_table}.{fk.ref_col}
                    </span>
                    <span style={{ marginLeft: 6, fontSize: 10, color: '#9090a8' }}>
                      ({fk.constraint_name})
                    </span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export const SchemaTab: React.FC<SchemaTabProps> = ({ selectedTable }) => {
  const [search, setSearch] = useState('')
  const [activeFqn, setActiveFqn] = useState<string | null>(selectedTable ?? null)
  const { data: allTables = [], isLoading: tablesLoading } = useTables()
  const { data: searchResults, isFetching: searchFetching } = useSearch(search)

  // When parent changes selectedTable (sidebar click), update activeFqn
  React.useEffect(() => {
    if (selectedTable) setActiveFqn(selectedTable)
  }, [selectedTable])

  const filtered = useFilteredTables(allTables, search.length >= 2 ? '' : search)

  // When searching, use server search results; otherwise use local filter
  const displayTables =
    search.trim().length >= 2 && searchResults
      ? searchResults.results.map((r) => ({
          fqn: r.fqn,
          name: r.name,
          schema_name: r.schema_name,
          row_count: null,
          table_type: '',
          comments: r.description,
          importance_tier: null,
          importance_rank: null,
          llm_description: null,
          column_count: 0,
        }))
      : filtered

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left: table browser */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          borderRight: activeFqn ? '1px solid #3a3a5c' : 'none',
        }}
      >
        {/* Search */}
        <div style={{ padding: '10px 14px', borderBottom: '1px solid #3a3a5c', flexShrink: 0 }}>
          <SearchBox
            value={search}
            onChange={setSearch}
            placeholder="Search tables, columns, descriptions… (≥2 chars uses API)"
            debounceMs={200}
            isLoading={searchFetching}
          />
          <div style={{ marginTop: 5, color: '#9090a8', fontSize: 11 }}>
            {tablesLoading
              ? 'Loading tables…'
              : search.trim().length >= 2
                ? `${displayTables.length} search result${displayTables.length !== 1 ? 's' : ''}`
                : `${filtered.length} of ${allTables.length} table${allTables.length !== 1 ? 's' : ''}`}
          </div>
        </div>

        {/* Virtual list */}
        <TableList
          tables={displayTables}
          selectedFqn={activeFqn}
          onSelect={(fqn) => setActiveFqn((prev) => (prev === fqn ? null : fqn))}
        />
      </div>

      {/* Right: table detail panel */}
      {activeFqn && (
        <TableDetail fqn={activeFqn} onClose={() => setActiveFqn(null)} />
      )}
    </div>
  )
}
