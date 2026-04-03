import React, { useState, useEffect, useRef } from 'react'

interface GraphControlsProps {
  limit: number
  onLimitChange: (n: number) => void
  /** All table names available in the current graph data (for the picker). */
  allTableNames: string[]
  /** Currently applied filter — empty Set means "show all". */
  appliedTables: Set<string>
  onApplyFilter: (selected: Set<string>) => void
  onClearFilter: () => void
  onReset: () => void
  showKnowledge: boolean
  onToggleKnowledge: () => void
}

export const GraphControls: React.FC<GraphControlsProps> = ({
  limit,
  onLimitChange,
  allTableNames,
  appliedTables,
  onApplyFilter,
  onClearFilter,
  onReset,
  showKnowledge,
  onToggleKnowledge,
}) => {
  const [open, setOpen] = useState(false)
  const [innerSearch, setInnerSearch] = useState('')
  const [draft, setDraft] = useState<Set<string>>(new Set(appliedTables))
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Sync draft whenever applied state changes externally (e.g. clear)
  useEffect(() => {
    setDraft(new Set(appliedTables))
  }, [appliedTables])

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const filtered = allTableNames.filter((name) =>
    name.toLowerCase().includes(innerSearch.toLowerCase()),
  )

  const toggleDraft = (name: string) => {
    setDraft((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const handleApply = () => {
    onApplyFilter(new Set(draft))
    setOpen(false)
  }

  const handleClear = () => {
    setDraft(new Set())
    onClearFilter()
    setOpen(false)
  }

  const isFiltered = appliedTables.size > 0
  const draftCount = draft.size

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 12,
        padding: '8px 16px',
        borderBottom: '1px solid #2e2e4e',
        background: '#1e1e30',
        flexShrink: 0,
        position: 'relative',
        zIndex: 20,
      }}
    >
      {/* ── Table multi-select filter ── */}
      <div ref={dropdownRef} style={{ position: 'relative' }}>
        <button
          onClick={() => setOpen((v) => !v)}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '4px 10px',
            background: isFiltered ? 'rgba(124,106,247,0.15)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${isFiltered ? 'rgba(124,106,247,0.5)' : '#3a3a5c'}`,
            borderRadius: 6,
            color: isFiltered ? '#a5b4fc' : '#9090a8',
            fontSize: 11,
            fontWeight: isFiltered ? 600 : 400,
            cursor: 'pointer',
            transition: 'all 0.15s',
          }}
        >
          <span>{isFiltered ? `Filtered: ${appliedTables.size} table${appliedTables.size !== 1 ? 's' : ''}` : 'Filter Tables'}</span>
          <span style={{ fontSize: 9, opacity: 0.7 }}>{open ? '▲' : '▼'}</span>
        </button>

        {/* Dropdown panel */}
        {open && (
          <div
            style={{
              position: 'absolute',
              top: 'calc(100% + 6px)',
              left: 0,
              width: 260,
              background: '#1a1a2e',
              border: '1px solid #3a3a5c',
              borderRadius: 10,
              boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
              zIndex: 100,
              display: 'flex',
              flexDirection: 'column',
              maxHeight: 360,
            }}
          >
            {/* Search within dropdown */}
            <div style={{ padding: '10px 10px 6px', flexShrink: 0 }}>
              <input
                type="text"
                value={innerSearch}
                onChange={(e) => setInnerSearch(e.target.value)}
                placeholder="Search tables…"
                autoFocus
                style={{
                  width: '100%',
                  padding: '5px 8px',
                  background: '#0e0e20',
                  border: '1px solid #3a3a5c',
                  borderRadius: 5,
                  color: '#e0e0f0',
                  fontSize: 11,
                  outline: 'none',
                  boxSizing: 'border-box',
                }}
              />
            </div>

            {/* Select-all / deselect-all */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                padding: '2px 10px 6px',
                flexShrink: 0,
              }}
            >
              <button
                onClick={() => setDraft(new Set(allTableNames))}
                style={smallLinkStyle}
              >
                Select all
              </button>
              <button
                onClick={() => setDraft(new Set())}
                style={smallLinkStyle}
              >
                Deselect all
              </button>
              <span style={{ fontSize: 10, color: '#5a5a7a', alignSelf: 'center' }}>
                {draftCount}/{allTableNames.length}
              </span>
            </div>

            {/* Scrollable checklist */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '2px 6px 6px' }}>
              {filtered.length === 0 ? (
                <div style={{ fontSize: 11, color: '#5a5a7a', padding: '8px 6px' }}>
                  No tables found
                </div>
              ) : (
                filtered.map((name) => (
                  <label
                    key={name}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 7,
                      padding: '4px 6px',
                      cursor: 'pointer',
                      borderRadius: 4,
                      transition: 'background 0.1s',
                    }}
                    onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.1)')}
                    onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = 'transparent')}
                  >
                    <input
                      type="checkbox"
                      checked={draft.has(name)}
                      onChange={() => toggleDraft(name)}
                      style={{ accentColor: '#7c6af7', width: 13, height: 13, cursor: 'pointer' }}
                    />
                    <span style={{ fontSize: 11, color: draft.has(name) ? '#c0c8ff' : '#9090a8', wordBreak: 'break-all' }}>
                      {name}
                    </span>
                  </label>
                ))
              )}
            </div>

            {/* Footer actions */}
            <div
              style={{
                display: 'flex',
                gap: 6,
                padding: '8px 10px',
                borderTop: '1px solid #2a2a3e',
                flexShrink: 0,
              }}
            >
              <button
                onClick={handleApply}
                style={{
                  flex: 1,
                  padding: '5px 0',
                  background: 'rgba(124,106,247,0.2)',
                  border: '1px solid rgba(124,106,247,0.4)',
                  borderRadius: 6,
                  color: '#a5b4fc',
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Apply{draftCount > 0 ? ` (${draftCount})` : ''}
              </button>
              <button
                onClick={handleClear}
                style={{
                  padding: '5px 10px',
                  background: 'none',
                  border: '1px solid #3a3a5c',
                  borderRadius: 6,
                  color: '#6a6a8a',
                  fontSize: 11,
                  cursor: 'pointer',
                }}
              >
                Clear
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Max nodes slider ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 11, color: '#6a6a8a', fontWeight: 500 }}>Max nodes:</span>
        <input
          type="range"
          min={20}
          max={300}
          step={10}
          value={limit}
          onChange={(e) => onLimitChange(Number(e.target.value))}
          style={{ width: 100, accentColor: '#7c6af7' }}
        />
        <span style={{ fontSize: 11, color: '#a0a0c0', minWidth: 30 }}>{limit}</span>
      </div>

      {/* ── Knowledge file toggle ── */}
      <button
        onClick={onToggleKnowledge}
        style={{
          padding: '4px 10px',
          background: showKnowledge ? 'rgba(124,106,247,0.18)' : 'none',
          border: `1px solid ${showKnowledge ? '#7c6af7' : '#3a3a5c'}`,
          borderRadius: 6,
          color: showKnowledge ? '#a5b4fc' : '#6a6a8a',
          fontSize: 11,
          fontWeight: showKnowledge ? 600 : 400,
          cursor: 'pointer',
        }}
      >
        Knowledge File
      </button>

      {/* ── Reset view ── */}
      <button
        onClick={onReset}
        style={{
          padding: '4px 10px',
          background: 'none',
          border: '1px solid #3a3a5c',
          borderRadius: 6,
          color: '#6a6a8a',
          fontSize: 11,
          cursor: 'pointer',
          marginLeft: 'auto',
        }}
      >
        Reset View
      </button>
    </div>
  )
}

const smallLinkStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: '#6a6af8',
  fontSize: 10,
  cursor: 'pointer',
  padding: 0,
  textDecoration: 'underline',
}
