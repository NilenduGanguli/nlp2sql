import React, { useState, useMemo } from 'react'
import { useGraph } from '../hooks/useGraph'
import { useKnowledgeFile, useRegenerateKnowledge } from '../hooks/useKnowledgeFile'
import { GraphCanvas } from '../components/graph/GraphCanvas'
import { GraphControls } from '../components/graph/GraphControls'

export const GraphPage: React.FC = () => {
  const [limit, setLimit] = useState(100)
  const [appliedTables, setAppliedTables] = useState<Set<string>>(new Set())
  const [resetKey, setResetKey] = useState(0)
  const [showKnowledge, setShowKnowledge] = useState(false)

  const { data, isLoading, error } = useGraph(limit)
  const { data: kf, isLoading: kfLoading, refetch: refetchKf } = useKnowledgeFile()
  const { mutate: regenerate, isPending: regenerating, error: regenError, isSuccess: regenStarted } =
    useRegenerateKnowledge()

  const allTableNames = useMemo(
    () => (data?.nodes ?? []).map((n) => n.name).sort(),
    [data],
  )

  const filteredNodes = useMemo(() => {
    if (!data) return []
    if (appliedTables.size === 0) return data.nodes
    return data.nodes.filter((n) => appliedTables.has(n.name))
  }, [data, appliedTables])

  const filteredEdges = useMemo(() => {
    if (!data) return []
    const nodeIds = new Set(filteredNodes.map((n) => n.id))
    return data.edges.filter((e) => nodeIds.has(e.from_id) && nodeIds.has(e.to_id))
  }, [data, filteredNodes])

  const handleReset = () => {
    setAppliedTables(new Set())
    setResetKey((k) => k + 1)
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <GraphControls
        limit={limit}
        onLimitChange={setLimit}
        allTableNames={allTableNames}
        appliedTables={appliedTables}
        onApplyFilter={setAppliedTables}
        onClearFilter={() => setAppliedTables(new Set())}
        onReset={handleReset}
        showKnowledge={showKnowledge}
        onToggleKnowledge={() => setShowKnowledge((v) => !v)}
      />

      {/* Knowledge file panel */}
      {showKnowledge && (
        <div
          style={{
            flexShrink: 0,
            borderBottom: '1px solid #3a3a5c',
            background: '#1a1a2e',
            maxHeight: 280,
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '6px 14px',
              borderBottom: '1px solid #2a2a3e',
              flexShrink: 0,
            }}
          >
            <span style={{ fontSize: 12, fontWeight: 600, color: '#c0c0d8' }}>
              Business Knowledge File
            </span>
            {kf && (
              <>
                <span style={{ fontSize: 11, color: '#6a6a8a' }}>{kf.path}</span>
                <span style={{ fontSize: 11, color: '#4a4a6a' }}>·</span>
                <span style={{ fontSize: 11, color: '#6a6a8a' }}>
                  {kf.size_bytes === 0 ? 'empty' : `${kf.size_bytes.toLocaleString()} bytes`}
                </span>
                <span style={{ fontSize: 11, color: '#4a4a6a' }}>·</span>
                <span
                  style={{
                    fontSize: 10,
                    padding: '1px 7px',
                    borderRadius: 999,
                    background: kf.enricher_enabled ? 'rgba(74,222,128,0.10)' : 'rgba(248,113,113,0.10)',
                    color: kf.enricher_enabled ? '#4ade80' : '#f87171',
                    fontWeight: 600,
                  }}
                >
                  enricher {kf.enricher_enabled ? 'ON' : 'OFF'}
                </span>
              </>
            )}
            <div style={{ flex: 1 }} />
            <button
              onClick={() => void refetchKf()}
              style={smallBtnStyle}
            >
              Refresh
            </button>
            <button
              onClick={() => regenerate()}
              disabled={regenerating || !kf}
              style={{
                ...smallBtnStyle,
                background: regenerating ? 'none' : 'rgba(124,106,247,0.12)',
                border: '1px solid rgba(124,106,247,0.4)',
                color: regenerating ? '#6a6a8a' : '#a5b4fc',
                fontWeight: 600,
                cursor: regenerating ? 'not-allowed' : 'pointer',
              }}
            >
              {regenerating ? 'Queued…' : 'Regenerate'}
            </button>
            {regenStarted && !regenerating && (
              <span style={{ fontSize: 11, color: '#4ade80' }}>Started — refresh in ~30s</span>
            )}
            {regenError && (
              <span style={{ fontSize: 11, color: '#f87171' }}>
                {(regenError as Error).message}
              </span>
            )}
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: '8px 14px' }}>
            {kfLoading ? (
              <span style={{ color: '#6a6a8a', fontSize: 12 }}>Loading…</span>
            ) : kf?.content ? (
              <pre
                style={{
                  margin: 0,
                  fontFamily: 'ui-monospace, Consolas, monospace',
                  fontSize: 11,
                  color: '#b0b8d8',
                  lineHeight: 1.6,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {kf.content}
              </pre>
            ) : (
              <div style={{ color: '#6a6a8a', fontSize: 12 }}>
                File is empty. Click{' '}
                <strong style={{ color: '#7c6af7' }}>Regenerate</strong> to generate from the knowledge graph.
              </div>
            )}
          </div>
        </div>
      )}

      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {isLoading ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: '#6a6a8a',
              fontSize: 14,
            }}
          >
            Loading knowledge graph…
          </div>
        ) : error ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: '#f87171',
              fontSize: 14,
            }}
          >
            Failed to load graph data
          </div>
        ) : (
          <GraphCanvas key={resetKey} nodes={filteredNodes} edges={filteredEdges} />
        )}
      </div>
    </div>
  )
}

const smallBtnStyle: React.CSSProperties = {
  background: 'none',
  border: '1px solid #3a3a5c',
  borderRadius: 4,
  color: '#8888aa',
  fontSize: 11,
  padding: '2px 8px',
  cursor: 'pointer',
}
