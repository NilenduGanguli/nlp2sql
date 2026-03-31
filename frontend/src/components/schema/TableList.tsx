import React, { useRef, useMemo } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import type { TableSummary } from '../../types'

const TIER_COLORS: Record<string, { bg: string; text: string }> = {
  core: { bg: 'rgba(124,106,247,0.2)', text: '#7c6af7' },
  reference: { bg: 'rgba(74,222,128,0.15)', text: '#4ade80' },
  audit: { bg: 'rgba(251,191,36,0.15)', text: '#fbbf24' },
  utility: { bg: 'rgba(144,144,168,0.15)', text: '#9090a8' },
}

interface TableListProps {
  tables: TableSummary[]
  selectedFqn?: string | null
  onSelect: (fqn: string) => void
}

export const TableList: React.FC<TableListProps> = ({ tables, selectedFqn, onSelect }) => {
  const parentRef = useRef<HTMLDivElement>(null)

  const virtualizer = useVirtualizer({
    count: tables.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 60,
    overscan: 10,
  })

  if (tables.length === 0) {
    return (
      <div
        style={{
          textAlign: 'center',
          color: '#9090a8',
          padding: 32,
          fontSize: 13,
        }}
      >
        No tables found
      </div>
    )
  }

  return (
    <div
      ref={parentRef}
      style={{ overflow: 'auto', flex: 1 }}
    >
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
        {virtualizer.getVirtualItems().map((vItem) => {
          const table = tables[vItem.index]
          const isSelected = table.fqn === selectedFqn
          const tierStyle = TIER_COLORS[table.importance_tier ?? ''] ?? {
            bg: 'transparent',
            text: '#9090a8',
          }

          return (
            <div
              key={vItem.key}
              data-index={vItem.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute',
                top: vItem.start,
                left: 0,
                right: 0,
              }}
            >
              <button
                onClick={() => onSelect(table.fqn)}
                style={{
                  display: 'flex',
                  width: '100%',
                  background: isSelected ? 'rgba(124,106,247,0.12)' : 'transparent',
                  border: 'none',
                  borderBottom: '1px solid #3a3a5c',
                  borderLeft: isSelected ? '3px solid #7c6af7' : '3px solid transparent',
                  padding: '10px 14px',
                  cursor: 'pointer',
                  textAlign: 'left',
                  alignItems: 'center',
                  gap: 10,
                  transition: 'background 0.1s',
                }}
                onMouseEnter={(e) => {
                  if (!isSelected)
                    (e.currentTarget as HTMLElement).style.background =
                      'rgba(124,106,247,0.06)'
                }}
                onMouseLeave={(e) => {
                  if (!isSelected)
                    (e.currentTarget as HTMLElement).style.background = 'transparent'
                }}
              >
                {/* Left: name + description */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                      marginBottom: 2,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 13,
                        fontWeight: 600,
                        color: '#e0e0f0',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {table.name}
                    </span>
                    <span
                      style={{
                        fontSize: 10,
                        color: '#9090a8',
                        background: '#1e1e2e',
                        borderRadius: 3,
                        padding: '1px 5px',
                        flexShrink: 0,
                      }}
                    >
                      {table.schema_name}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: 11,
                      color: '#9090a8',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {table.comments || table.llm_description || '\u00a0'}
                  </div>
                </div>

                {/* Right: badges */}
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3, flexShrink: 0 }}>
                  {table.importance_tier && (
                    <span
                      style={{
                        fontSize: 9,
                        fontWeight: 700,
                        color: tierStyle.text,
                        background: tierStyle.bg,
                        borderRadius: 3,
                        padding: '1px 5px',
                        textTransform: 'uppercase',
                        letterSpacing: '0.06em',
                      }}
                    >
                      {table.importance_tier}
                    </span>
                  )}
                  <span style={{ fontSize: 10, color: '#9090a8' }}>
                    {table.column_count} cols
                    {table.row_count != null && (
                      <> · {table.row_count.toLocaleString()} rows</>
                    )}
                  </span>
                </div>
              </button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

/**
 * Hook: filter tables client-side by search query.
 */
export function useFilteredTables(tables: TableSummary[], q: string): TableSummary[] {
  return useMemo(() => {
    const query = q.trim().toLowerCase()
    if (!query) return tables
    return tables.filter(
      (t) =>
        t.name.toLowerCase().includes(query) ||
        t.schema_name.toLowerCase().includes(query) ||
        (t.comments ?? '').toLowerCase().includes(query) ||
        (t.llm_description ?? '').toLowerCase().includes(query),
    )
  }, [tables, q])
}
