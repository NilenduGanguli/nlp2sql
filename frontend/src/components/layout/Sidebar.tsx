import React, { useState, useRef, useMemo, useEffect } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useHealth } from '../../hooks/useHealth'
import { useTables } from '../../hooks/useTables'
import { useSchemaStats } from '../../hooks/useSchema'
import { useRebuildGraph } from '../../hooks/useRebuildGraph'
import { useSettingsStore } from '../../store/settingsStore'
import { useKnowledgeFile, useRegenerateKnowledge } from '../../hooks/useKnowledgeFile'
import { StatusPill } from '../common/StatusPill'
import { SearchBox } from '../common/SearchBox'
import type { TableSummary } from '../../types'

interface SidebarProps {
  isOpen: boolean
  onToggle: () => void
  onTableSelect: (fqn: string) => void
}

export const Sidebar: React.FC<SidebarProps> = ({ isOpen, onToggle, onTableSelect }) => {
  const { data: health } = useHealth()
  const { data: stats } = useSchemaStats()
  const { data: tables, isLoading: tablesLoading } = useTables()
  const { mutate: rebuild, isPending: rebuilding } = useRebuildGraph()
  const { data: kf, isLoading: kfLoading, refetch: refetchKf } = useKnowledgeFile()
  const { mutate: regenerate, isPending: regenerating } = useRegenerateKnowledge()
  const [search, setSearch] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [knowledgeOpen, setKnowledgeOpen] = useState(false)
  const [regenMsg, setRegenMsg] = useState<string | null>(null)
  const [applyMsg, setApplyMsg] = useState<string | null>(null)
  const parentRef = useRef<HTMLDivElement>(null)

  const {
    llmProvider, llmModel, llmApiKey,
    isSaving, saveError,
    setProvider, setModel, setApiKey,
    applySettings, syncFromBackend,
  } = useSettingsStore()

  // Load backend config once when sidebar mounts
  useEffect(() => {
    void syncFromBackend()
  }, [syncFromBackend])

  const handleApplySettings = async () => {
    setApplyMsg(null)
    const err = await applySettings()
    setApplyMsg(err ? `Error: ${err}` : 'Settings applied! Pipeline rebuilding…')
    setTimeout(() => setApplyMsg(null), 4000)
  }

  const filtered = useMemo<TableSummary[]>(() => {
    if (!tables) return []
    const q = search.trim().toLowerCase()
    if (!q) return tables
    return tables.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.schema_name.toLowerCase().includes(q) ||
        (t.comments ?? '').toLowerCase().includes(q) ||
        (t.llm_description ?? '').toLowerCase().includes(q),
    )
  }, [tables, search])

  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 60,
    overscan: 10,
  })

  if (!isOpen) {
    return (
      <div
        style={{
          width: 40,
          background: '#2a2a3e',
          borderRight: '1px solid #3a3a5c',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          paddingTop: 12,
          flexShrink: 0,
        }}
      >
        <button
          onClick={onToggle}
          title="Open sidebar"
          style={{
            background: 'none',
            border: 'none',
            color: '#9090a8',
            fontSize: 18,
            padding: 4,
            cursor: 'pointer',
          }}
        >
          ›
        </button>
      </div>
    )
  }

  return (
    <div
      style={{
        width: 280,
        background: '#2a2a3e',
        borderRight: '1px solid #3a3a5c',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        flexShrink: 0,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '12px 12px 8px',
          borderBottom: '1px solid #3a3a5c',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0,
        }}
      >
        <span style={{ fontWeight: 700, fontSize: 13, color: '#7c6af7', letterSpacing: '0.05em' }}>
          KnowledgeQL
        </span>
        <button
          onClick={onToggle}
          style={{ background: 'none', border: 'none', color: '#9090a8', fontSize: 16, padding: 2, cursor: 'pointer' }}
        >
          ‹
        </button>
      </div>

      {/* Health status */}
      <div style={{ padding: '10px 12px', borderBottom: '1px solid #3a3a5c', flexShrink: 0 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          <StatusPill
            status={health?.oracle_connected ? 'ok' : 'error'}
            label="Oracle"
            tooltip={health?.oracle_connected ? 'Oracle DB connected' : 'Oracle DB not connected'}
          />
          <StatusPill
            status={health?.llm_ready ? 'ok' : 'warning'}
            label="LLM"
            tooltip={health?.llm_ready ? 'LLM ready' : 'No LLM credentials'}
          />
          <StatusPill
            status={
              !health
                ? 'unknown'
                : health.graph_loaded
                  ? health.llm_enhanced
                    ? 'ok'
                    : 'warning'
                  : 'error'
            }
            label={health?.llm_enhanced ? 'Graph+AI' : 'Graph'}
            tooltip={
              health?.graph_loaded
                ? `${health.graph_tables} tables, ${health.graph_columns} columns`
                : 'Graph not loaded'
            }
          />
        </div>
      </div>

      {/* Schema stats */}
      {stats && (
        <div
          style={{
            padding: '8px 12px',
            borderBottom: '1px solid #3a3a5c',
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: '4px 16px',
            flexShrink: 0,
          }}
        >
          {[
            ['Tables', stats.table_count],
            ['Columns', stats.column_count],
            ['FKs', stats.fk_count],
            ['Join paths', stats.join_path_count],
          ].map(([label, val]) => (
            <div key={String(label)}>
              <span style={{ color: '#9090a8', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {label}
              </span>
              <div style={{ color: '#e0e0f0', fontWeight: 600, fontSize: 13 }}>{val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Search */}
      <div style={{ padding: '8px 10px', flexShrink: 0 }}>
        <SearchBox
          value={search}
          onChange={setSearch}
          placeholder="Filter tables…"
          debounceMs={100}
        />
      </div>

      {/* Table count */}
      <div style={{ padding: '0 12px 6px', flexShrink: 0, color: '#9090a8', fontSize: 11 }}>
        {tablesLoading
          ? 'Loading tables…'
          : `${filtered.length}${search ? ' matching' : ''} table${filtered.length !== 1 ? 's' : ''}`}
      </div>

      {/* Virtual table list */}
      <div ref={parentRef} style={{ flex: 1, overflowY: 'auto' }}>
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualizer.getVirtualItems().map((vItem) => {
            const table = filtered[vItem.index]
            return (
              <div
                key={vItem.key}
                data-index={vItem.index}
                ref={virtualizer.measureElement}
                style={{ position: 'absolute', top: vItem.start, left: 0, right: 0 }}
              >
                <button
                  onClick={() => onTableSelect(table.fqn)}
                  style={{
                    display: 'block',
                    width: '100%',
                    background: 'none',
                    border: 'none',
                    borderBottom: '1px solid #3a3a5c',
                    padding: '8px 12px',
                    textAlign: 'left',
                    cursor: 'pointer',
                  }}
                  onMouseEnter={(e) =>
                    ((e.currentTarget as HTMLElement).style.background = 'rgba(124,106,247,0.08)')
                  }
                  onMouseLeave={(e) =>
                    ((e.currentTarget as HTMLElement).style.background = 'none')
                  }
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        color: '#e0e0f0',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        flex: 1,
                      }}
                    >
                      {table.name}
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontSize: 10, color: '#9090a8' }}>{table.schema_name}</span>
                    <span style={{ fontSize: 10, color: '#9090a8' }}>{table.column_count} cols</span>
                    {table.row_count != null && (
                      <span style={{ fontSize: 10, color: '#9090a8' }}>
                        {table.row_count.toLocaleString()} rows
                      </span>
                    )}
                  </div>
                </button>
              </div>
            )
          })}
        </div>
      </div>

      {/* Business Knowledge section */}
      <div style={{ borderTop: '1px solid #3a3a5c', flexShrink: 0 }}>
        <button
          onClick={() => { setKnowledgeOpen((v) => !v); if (!knowledgeOpen) void refetchKf() }}
          style={{
            width: '100%',
            padding: '8px 12px',
            background: 'none',
            border: 'none',
            textAlign: 'left',
            color: '#9090a8',
            fontSize: 11,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span>Business Knowledge</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {kf && (
              <span
                style={{
                  fontSize: 9,
                  padding: '1px 5px',
                  borderRadius: 999,
                  background: kf.enricher_enabled ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)',
                  color: kf.enricher_enabled ? '#4ade80' : '#f87171',
                  fontWeight: 600,
                  textTransform: 'none',
                  letterSpacing: 0,
                }}
              >
                {kf.enricher_enabled ? 'ON' : 'OFF'}
              </span>
            )}
            <span style={{ fontSize: 14 }}>{knowledgeOpen ? '▲' : '▼'}</span>
          </div>
        </button>

        {knowledgeOpen && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
            {/* Action bar */}
            <div style={{ padding: '0 12px 6px', display: 'flex', gap: 6, alignItems: 'center' }}>
              {kf && (
                <span style={{ fontSize: 10, color: '#9090a8', flex: 1 }}>
                  {kf.size_bytes === 0 ? 'empty' : `${kf.size_bytes.toLocaleString()} bytes`}
                </span>
              )}
              <button
                onClick={() => void refetchKf()}
                style={{
                  background: 'none',
                  border: '1px solid #3a3a5c',
                  borderRadius: 4,
                  color: '#9090a8',
                  fontSize: 10,
                  padding: '2px 7px',
                  cursor: 'pointer',
                }}
              >
                Refresh
              </button>
              <button
                onClick={() => {
                  setRegenMsg(null)
                  regenerate(undefined, {
                    onSuccess: () => {
                      setRegenMsg('Queued — refresh in ~30s')
                      setTimeout(() => setRegenMsg(null), 15000)
                    },
                    onError: (err) => setRegenMsg((err as Error).message),
                  })
                }}
                disabled={regenerating || !kf}
                style={{
                  background: regenerating ? '#3a3a5c' : 'rgba(124,106,247,0.15)',
                  border: '1px solid #7c6af7',
                  borderRadius: 4,
                  color: regenerating ? '#9090a8' : '#7c6af7',
                  fontSize: 10,
                  fontWeight: 600,
                  padding: '2px 7px',
                  cursor: regenerating ? 'not-allowed' : 'pointer',
                }}
              >
                {regenerating ? '…' : 'Regenerate'}
              </button>
            </div>

            {regenMsg && (
              <div style={{ padding: '0 12px 6px', fontSize: 10, color: regenMsg.startsWith('Queued') ? '#4ade80' : '#f87171' }}>
                {regenMsg}
              </div>
            )}

            {/* Content */}
            <div
              style={{
                maxHeight: 200,
                overflowY: 'auto',
                margin: '0 12px 10px',
                background: '#1e1e2e',
                border: '1px solid #3a3a5c',
                borderRadius: 4,
              }}
            >
              {kfLoading ? (
                <div style={{ padding: 10, color: '#9090a8', fontSize: 11 }}>Loading…</div>
              ) : kf?.content ? (
                <pre
                  style={{
                    margin: 0,
                    padding: '8px 10px',
                    fontFamily: 'ui-monospace, Consolas, monospace',
                    fontSize: 10,
                    color: '#c0c0d8',
                    lineHeight: 1.6,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {kf.content}
                </pre>
              ) : (
                <div style={{ padding: 10, color: '#9090a8', fontSize: 11 }}>
                  File is empty. Click <strong style={{ color: '#7c6af7' }}>Regenerate</strong>.
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Settings section */}
      <div style={{ borderTop: '1px solid #3a3a5c', flexShrink: 0 }}>
        <button
          onClick={() => setSettingsOpen((v) => !v)}
          style={{
            width: '100%',
            padding: '8px 12px',
            background: 'none',
            border: 'none',
            textAlign: 'left',
            color: '#9090a8',
            fontSize: 11,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span>Settings</span>
          <span style={{ fontSize: 14 }}>{settingsOpen ? '▲' : '▼'}</span>
        </button>

        {settingsOpen && (
          <div style={{ padding: '0 12px 10px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            {/* LLM Provider */}
            <div>
              <label style={{ fontSize: 10, color: '#9090a8', display: 'block', marginBottom: 3 }}>
                LLM Provider
              </label>
              <select
                value={llmProvider}
                onChange={(e) => setProvider(e.target.value)}
                style={{
                  width: '100%',
                  padding: '5px 8px',
                  background: '#1e1e2e',
                  border: '1px solid #3a3a5c',
                  borderRadius: 5,
                  color: '#e0e0f0',
                  fontSize: 12,
                }}
              >
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="vertex">Vertex AI</option>
              </select>
            </div>

            {/* Model */}
            <div>
              <label style={{ fontSize: 10, color: '#9090a8', display: 'block', marginBottom: 3 }}>
                Model
              </label>
              <input
                type="text"
                value={llmModel}
                onChange={(e) => setModel(e.target.value)}
                style={{
                  width: '100%',
                  padding: '5px 8px',
                  background: '#1e1e2e',
                  border: '1px solid #3a3a5c',
                  borderRadius: 5,
                  color: '#e0e0f0',
                  fontSize: 12,
                  boxSizing: 'border-box',
                }}
              />
            </div>

            {/* API Key (hidden for vertex) */}
            {llmProvider !== 'vertex' && (
              <div>
                <label style={{ fontSize: 10, color: '#9090a8', display: 'block', marginBottom: 3 }}>
                  API Key
                </label>
                <input
                  type="password"
                  value={llmApiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-…"
                  style={{
                    width: '100%',
                    padding: '5px 8px',
                    background: '#1e1e2e',
                    border: '1px solid #3a3a5c',
                    borderRadius: 5,
                    color: '#e0e0f0',
                    fontSize: 12,
                    boxSizing: 'border-box',
                  }}
                />
              </div>
            )}

            {/* Apply button */}
            <button
              onClick={() => void handleApplySettings()}
              disabled={isSaving}
              style={{
                padding: '6px 0',
                background: isSaving ? '#3a3a5c' : 'rgba(124,106,247,0.15)',
                border: '1px solid #7c6af7',
                borderRadius: 5,
                color: isSaving ? '#9090a8' : '#7c6af7',
                fontSize: 12,
                fontWeight: 600,
                cursor: isSaving ? 'not-allowed' : 'pointer',
              }}
            >
              {isSaving ? 'Applying…' : 'Apply Settings'}
            </button>

            {/* Status message */}
            {applyMsg && (
              <div
                style={{
                  fontSize: 11,
                  color: applyMsg.startsWith('Error') ? '#f87171' : '#4ade80',
                  wordBreak: 'break-word',
                }}
              >
                {applyMsg}
              </div>
            )}
            {saveError && !applyMsg && (
              <div style={{ fontSize: 11, color: '#f87171' }}>{saveError}</div>
            )}
          </div>
        )}
      </div>

      {/* Rebuild button */}
      <div style={{ padding: '10px 12px', borderTop: '1px solid #3a3a5c', flexShrink: 0 }}>
        <button
          onClick={() => rebuild()}
          disabled={rebuilding}
          style={{
            width: '100%',
            padding: '7px 0',
            background: rebuilding ? '#3a3a5c' : 'rgba(124,106,247,0.15)',
            border: '1px solid #7c6af7',
            borderRadius: 6,
            color: rebuilding ? '#9090a8' : '#7c6af7',
            fontSize: 12,
            fontWeight: 600,
            cursor: rebuilding ? 'not-allowed' : 'pointer',
          }}
        >
          {rebuilding ? 'Rebuilding…' : 'Rebuild Graph'}
        </button>
      </div>
    </div>
  )
}
