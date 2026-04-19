import React, { useState, useEffect, useCallback, useRef } from 'react'
import type { PromptFile } from '../types'

const C = {
  bg: '#1e1e2e',
  panel: '#2a2a3e',
  panel2: '#242438',
  border: '#3a3a5c',
  accent: '#7c6af7',
  accentDim: '#4e45a4',
  text: '#e0e0f0',
  muted: '#9090a8',
  success: '#4ade80',
  warn: '#fbbf24',
  error: '#f87171',
  code: '#1a1a2e',
  oracle: '#f472b6',
}

// ── Static metadata ───────────────────────────────────────────────────────────

const PROMPT_LABELS: Record<string, string> = {
  query_enricher_system:       'Query Enricher — System',
  query_enricher_human:        'Query Enricher — Human',
  intent_classifier_system:    'Intent Classifier — System',
  entity_extractor_system:     'Entity Extractor — System',
  clarification_agent_system:  'Clarification Agent — System',
  clarification_agent_human:   'Clarification Agent — Human',
  sql_generator_system:        'SQL Generator — System',
  sql_presenter_system:        'SQL Presenter — System',
  kyc_business_agent_system:   'KYC Business Agent — System',
}

const PROMPT_ORDER = [
  'query_enricher_system',
  'query_enricher_human',
  'intent_classifier_system',
  'entity_extractor_system',
  'clarification_agent_system',
  'clarification_agent_human',
  'sql_generator_system',
  'sql_presenter_system',
  'kyc_business_agent_system',
]

const NODE_DESCRIPTIONS: Record<string, string> = {
  query_enricher_system:       'Enriches the raw user query with domain knowledge before entity extraction.',
  query_enricher_human:        'Human message template (receives {user_input} and {knowledge}).',
  intent_classifier_system:    'Classifies intent as DATA_QUERY / SCHEMA_EXPLORE / QUERY_EXPLAIN / QUERY_REFINE / RESULT_FOLLOWUP. Uses conversation history to detect follow-up references.',
  entity_extractor_system:     'Agentic loop — receives {schemas}, {schema_tree}, {tools_spec}, {max_calls}.',
  clarification_agent_system:  'Decides if query needs clarification and generates a question + options.',
  clarification_agent_human:   'Human message template for the clarification agent.',
  sql_generator_system:        'Main SQL generation prompt — rules + constraints + FQN requirements.',
  sql_presenter_system:        'Presents generated SQL to user for confirmation before execution.',
  kyc_business_agent_system:   'KYC domain business agent — auto-resolves clarifications using domain knowledge.',
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface PromptVersion {
  version_id: string
  saved_at: string
  preview: string
}

interface AgentTool {
  name: string
  color: string
  description: string
}

interface PipelineNode {
  node: string
  label: string
  prompt: string | null
  type: 'llm' | 'agent' | 'graph' | 'rule' | 'oracle'
  description: string
}

interface PipelineEdge {
  from: string
  to: string
  condition: string
}

interface AgentConfig {
  pipeline_nodes: PipelineNode[]
  pipeline_edges: PipelineEdge[]
  entity_extractor: {
    max_tool_calls: number
    oracle_max_rows: number
    tools: AgentTool[]
    protocol: string
    fallback: string
  }
}

// ── Styles ────────────────────────────────────────────────────────────────────

function btnStyle(color: string, disabled = false): React.CSSProperties {
  return {
    padding: '6px 14px',
    borderRadius: 6,
    border: `1px solid ${color}55`,
    background: disabled ? '#2a2a3e' : color + '22',
    color: disabled ? C.muted : color,
    fontSize: 12,
    fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    whiteSpace: 'nowrap',
  }
}

function btnSm(color: string): React.CSSProperties {
  return {
    padding: '3px 9px',
    borderRadius: 4,
    border: `1px solid ${color}44`,
    background: color + '18',
    color,
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  }
}

type NodeType = PipelineNode['type']
const NODE_TYPE_COLORS: Record<NodeType, string> = {
  llm:    '#fbbf24',
  agent:  '#7c6af7',
  graph:  '#38bdf8',
  rule:   '#9090a8',
  oracle: '#f472b6',
}

// ── Agent Config Panel ────────────────────────────────────────────────────────

function AgentConfigPanel({ config }: { config: AgentConfig }) {
  const [expandedNode, setExpandedNode] = useState<string | null>(null)

  return (
    <div style={{ padding: '20px 24px', overflowY: 'auto', height: '100%', boxSizing: 'border-box' }}>

      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: C.text, marginBottom: 4 }}>Agentic Pipeline Configuration</div>
        <div style={{ fontSize: 12, color: C.muted }}>
          Read-only view of the current pipeline structure, node types, and entity extractor tool specifications.
          Edit prompts in the <strong style={{ color: C.accent }}>Prompts</strong> view; rebuild takes effect immediately without restart.
        </div>
      </div>

      {/* Entity Extractor Config */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: C.accent, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: C.accent, display: 'inline-block' }} />
          Entity Extractor Agent
        </div>

        {/* Stats row */}
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
          {[
            { label: 'Max tool calls', value: String(config.entity_extractor.max_tool_calls), color: C.warn },
            { label: 'Oracle max rows', value: String(config.entity_extractor.oracle_max_rows), color: C.oracle },
            { label: 'Protocol', value: 'JSON ReAct', color: C.success },
          ].map((s) => (
            <div key={s.label} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: '10px 16px', minWidth: 120 }}>
              <div style={{ fontSize: 11, color: C.muted, marginBottom: 4 }}>{s.label}</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: s.color }}>{s.value}</div>
            </div>
          ))}
        </div>

        <div style={{ fontSize: 11, color: C.muted, marginBottom: 12, fontStyle: 'italic' }}>
          {config.entity_extractor.protocol}
        </div>

        {/* Tools grid */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {config.entity_extractor.tools.map((tool) => (
            <div
              key={tool.name}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 12,
                background: C.panel, border: `1px solid ${tool.color}22`,
                borderRadius: 8, padding: '10px 14px',
              }}
            >
              <span style={{
                flexShrink: 0,
                fontFamily: 'monospace', fontWeight: 700, fontSize: 12,
                color: tool.color, background: tool.color + '15',
                border: `1px solid ${tool.color}44`, borderRadius: 5,
                padding: '3px 10px',
              }}>
                {tool.name}
              </span>
              <span style={{ fontSize: 12, color: C.text, lineHeight: 1.5 }}>{tool.description}</span>
            </div>
          ))}
        </div>

        <div style={{ marginTop: 10, fontSize: 11, color: C.muted, fontStyle: 'italic' }}>
          Fallback: {config.entity_extractor.fallback}
        </div>
      </div>

      {/* Pipeline DAG */}
      <div>
        <div style={{ fontSize: 14, fontWeight: 700, color: C.text, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: C.text, display: 'inline-block' }} />
          Pipeline Nodes
        </div>

        {/* Legend */}
        <div style={{ display: 'flex', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
          {(Object.entries(NODE_TYPE_COLORS) as [NodeType, string][]).map(([type, color]) => (
            <span key={type} style={{ fontSize: 11, color, background: color + '15', border: `1px solid ${color}44`, borderRadius: 4, padding: '2px 8px' }}>
              {type}
            </span>
          ))}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {config.pipeline_nodes.map((node, idx) => {
            const color = NODE_TYPE_COLORS[node.type]
            const outEdges = config.pipeline_edges.filter((e) => e.from === node.node)
            const isOpen = expandedNode === node.node
            return (
              <div key={node.node}>
                <div
                  onClick={() => setExpandedNode(isOpen ? null : node.node)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    background: C.panel, border: `1px solid ${color}33`,
                    borderRadius: 8, padding: '10px 14px', cursor: 'pointer',
                  }}
                >
                  <span style={{
                    flexShrink: 0, fontSize: 11, fontWeight: 700, color,
                    background: color + '15', border: `1px solid ${color}44`,
                    borderRadius: 4, padding: '2px 7px', textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                  }}>
                    {node.type}
                  </span>
                  <span style={{ fontWeight: 600, fontSize: 13, color: C.text }}>{node.label}</span>
                  {node.prompt && (
                    <span style={{ fontSize: 11, color: C.muted, fontStyle: 'italic' }}>
                      → {node.prompt}.txt
                    </span>
                  )}
                  <div style={{ flex: 1 }} />
                  <span style={{ fontSize: 11, color: C.muted }}>{isOpen ? '▾' : '▸'}</span>
                </div>

                {isOpen && (
                  <div style={{
                    marginLeft: 16, padding: '10px 14px',
                    background: C.code, border: `1px solid ${color}22`,
                    borderRadius: '0 0 6px 6px', borderTop: 'none',
                  }}>
                    <div style={{ fontSize: 12, color: C.text, marginBottom: outEdges.length ? 8 : 0 }}>
                      {node.description}
                    </div>
                    {outEdges.length > 0 && (
                      <div>
                        <div style={{ fontSize: 11, color: C.muted, marginBottom: 4, marginTop: 6 }}>EDGES:</div>
                        {outEdges.map((e, i) => (
                          <div key={i} style={{ fontSize: 11, color: C.text, display: 'flex', gap: 6, marginBottom: 3 }}>
                            <span style={{ color: C.muted }}>→</span>
                            <span style={{ color: e.to === 'END' ? C.success : C.accent, fontWeight: 600 }}>{e.to}</span>
                            <span style={{ color: C.muted }}>when</span>
                            <span style={{ color: '#cfe0ff', fontStyle: 'italic' }}>{e.condition}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {idx < config.pipeline_nodes.length - 1 && (
                  <div style={{ textAlign: 'center', color: C.muted, fontSize: 16, lineHeight: '20px', marginLeft: 20 }}>↓</div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ── Version history panel ─────────────────────────────────────────────────────

interface VersionHistoryProps {
  promptName: string
  onRestore: (content: string) => void
}

function VersionHistory({ promptName, onRestore }: VersionHistoryProps) {
  const [versions, setVersions] = useState<PromptVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [previewVersion, setPreviewVersion] = useState<{id: string; content: string} | null>(null)
  const [restoring, setRestoring] = useState<string | null>(null)
  const [open, setOpen] = useState(false)

  const loadVersions = useCallback(() => {
    if (!open) return
    setLoading(true)
    fetch(`/api/prompts/${promptName}/history`)
      .then((r) => r.json())
      .then((data: { versions: PromptVersion[] }) => {
        setVersions(data.versions || [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [promptName, open])

  useEffect(() => {
    loadVersions()
    setPreviewVersion(null)
  }, [promptName, loadVersions])

  const handlePreview = async (versionId: string) => {
    if (previewVersion?.id === versionId) { setPreviewVersion(null); return }
    const r = await fetch(`/api/prompts/${promptName}/history/${versionId}`)
    if (!r.ok) return
    const data = await r.json() as { content: string }
    setPreviewVersion({ id: versionId, content: data.content })
  }

  const handleRestore = async (versionId: string) => {
    setRestoring(versionId)
    const r = await fetch(`/api/prompts/${promptName}/restore/${versionId}`, { method: 'POST' })
    if (r.ok) {
      const data = await r.json() as { name: string }
      if (previewVersion?.id === versionId) {
        onRestore(previewVersion.content)
      } else {
        // Fetch content and restore
        const cr = await fetch(`/api/prompts/${promptName}/history/${versionId}`)
        if (cr.ok) {
          const cd = await cr.json() as { content: string }
          onRestore(cd.content)
        }
      }
      loadVersions()
    }
    setRestoring(null)
  }

  return (
    <div style={{ borderTop: `1px solid ${C.border}`, marginTop: 8 }}>
      <div
        onClick={() => { setOpen((v) => !v); }}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '8px 18px', cursor: 'pointer',
          color: C.muted, fontSize: 12,
        }}
      >
        <span style={{ color: C.muted }}>{open ? '▾' : '▸'}</span>
        <span>Version History</span>
        {versions.length > 0 && (
          <span style={{ fontSize: 11, color: C.accent }}>{versions.length} save{versions.length !== 1 ? 's' : ''}</span>
        )}
        <div style={{ flex: 1 }} />
        {!open && versions.length === 0 && (
          <span style={{ fontSize: 11, color: C.muted, fontStyle: 'italic' }}>
            {loading ? 'loading…' : 'set PROMPTS_PERSIST_PATH to enable'}
          </span>
        )}
      </div>

      {open && (
        <div style={{ padding: '0 18px 14px' }}>
          {loading && <div style={{ fontSize: 12, color: C.muted }}>Loading history…</div>}

          {!loading && versions.length === 0 && (
            <div style={{ fontSize: 12, color: C.muted, fontStyle: 'italic', padding: '4px 0' }}>
              No version history. Set the <code style={{ color: C.text }}>PROMPTS_PERSIST_PATH</code> env var
              to enable versioned saves that survive container rebuilds.
            </div>
          )}

          {versions.map((v) => {
            const isPreviewed = previewVersion?.id === v.version_id
            const isRestoring = restoring === v.version_id
            return (
              <div key={v.version_id}>
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 0', borderBottom: `1px solid ${C.border}22`,
                }}>
                  <span style={{ fontSize: 12, color: C.text, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {v.saved_at}
                  </span>
                  <span style={{ fontSize: 11, color: C.muted, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontStyle: 'italic' }}>
                    {v.preview}
                  </span>
                  <button onClick={() => handlePreview(v.version_id)} style={btnSm(isPreviewed ? C.accent : C.muted)}>
                    {isPreviewed ? 'hide' : 'preview'}
                  </button>
                  <button
                    onClick={() => handleRestore(v.version_id)}
                    disabled={isRestoring}
                    style={btnSm(isRestoring ? C.muted : C.warn)}
                  >
                    {isRestoring ? 'restoring…' : 'restore'}
                  </button>
                </div>

                {isPreviewed && (
                  <div style={{ margin: '6px 0 10px' }}>
                    <pre style={{
                      background: C.code, border: `1px solid ${C.accent}44`,
                      borderRadius: 6, padding: '10px 14px',
                      maxHeight: 200, overflowY: 'auto',
                      fontSize: 11, color: '#cfe0ff',
                      whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0,
                    }}>
                      {previewVersion!.content}
                    </pre>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

type PageMode = 'prompts' | 'agent'
type RebuildStatus = 'idle' | 'rebuilding' | 'done' | 'error'

export const PromptStudioPage: React.FC = () => {
  const [mode, setMode] = useState<PageMode>('prompts')
  const [prompts, setPrompts] = useState<Record<string, string>>({})
  const [selectedName, setSelectedName] = useState<string>(PROMPT_ORDER[0])
  const [draft, setDraft] = useState<string>('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')
  const [rebuildStatus, setRebuildStatus] = useState<RebuildStatus>('idle')
  const [rebuildMsg, setRebuildMsg] = useState('')
  const [agentConfig, setAgentConfig] = useState<AgentConfig | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastSavedRef = useRef<Record<string, string>>({})

  // ── Load all prompts ───────────────────────────────────────────────────────
  const loadPrompts = useCallback(() => {
    fetch('/api/prompts')
      .then((r) => r.json())
      .then((data: { prompts: PromptFile[] }) => {
        const map: Record<string, string> = {}
        for (const p of data.prompts) map[p.name] = p.content
        setPrompts(map)
        lastSavedRef.current = { ...map }
      })
      .catch(() => {})
  }, [])

  // ── Load agent config ──────────────────────────────────────────────────────
  const loadAgentConfig = useCallback(() => {
    fetch('/api/admin/agent-config')
      .then((r) => r.json())
      .then((data: AgentConfig) => setAgentConfig(data))
      .catch(() => {})
  }, [])

  useEffect(() => { loadPrompts() }, [loadPrompts])
  useEffect(() => { loadAgentConfig() }, [loadAgentConfig])

  // When selected prompt changes, load its draft
  useEffect(() => {
    const content = prompts[selectedName] ?? ''
    setDraft(content)
    setDirty(false)
  }, [selectedName, prompts])

  // ── Save current prompt ────────────────────────────────────────────────────
  const handleSave = async () => {
    setSaving(true)
    try {
      const r = await fetch(`/api/prompts/${selectedName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: draft }),
      })
      if (r.ok) {
        setPrompts((prev) => ({ ...prev, [selectedName]: draft }))
        lastSavedRef.current = { ...lastSavedRef.current, [selectedName]: draft }
        setDirty(false)
        setSavedMsg('Saved!')
        setTimeout(() => setSavedMsg(''), 2000)
      } else {
        setSavedMsg('Save failed')
        setTimeout(() => setSavedMsg(''), 3000)
      }
    } catch {
      setSavedMsg('Save failed')
      setTimeout(() => setSavedMsg(''), 3000)
    }
    setSaving(false)
  }

  // ── Discard changes ────────────────────────────────────────────────────────
  const handleDiscard = () => {
    setDraft(prompts[selectedName] ?? '')
    setDirty(false)
  }

  // ── Version restore callback ───────────────────────────────────────────────
  const handleVersionRestore = useCallback((content: string) => {
    setDraft(content)
    setPrompts((prev) => ({ ...prev, [selectedName]: content }))
    lastSavedRef.current = { ...lastSavedRef.current, [selectedName]: content }
    setDirty(false)
    setSavedMsg('✓ Version restored')
    setTimeout(() => setSavedMsg(''), 3000)
  }, [selectedName])

  // ── Rebuild pipeline ───────────────────────────────────────────────────────
  const handleRebuild = async () => {
    setRebuildStatus('rebuilding')
    setRebuildMsg('Rebuilding pipeline…')
    try {
      const r = await fetch('/api/admin/rebuild-pipeline', { method: 'POST' })
      const data = await r.json() as { status: string; message: string }
      if (r.ok) {
        setRebuildMsg(data.message ?? 'Pipeline rebuilding…')
        if (pollRef.current) clearTimeout(pollRef.current)
        pollRef.current = setTimeout(() => {
          setRebuildStatus('done')
          setRebuildMsg('Pipeline rebuilt — new prompts are now active.')
          setTimeout(() => { setRebuildStatus('idle'); setRebuildMsg('') }, 4000)
        }, 2000)
      } else {
        setRebuildStatus('error')
        setRebuildMsg('Rebuild failed — check backend logs.')
        setTimeout(() => { setRebuildStatus('idle'); setRebuildMsg('') }, 5000)
      }
    } catch {
      setRebuildStatus('error')
      setRebuildMsg('Rebuild request failed — is the backend running?')
      setTimeout(() => { setRebuildStatus('idle'); setRebuildMsg('') }, 5000)
    }
  }

  // ── Export ZIP ─────────────────────────────────────────────────────────────
  const handleExport = async () => {
    const r = await fetch('/api/prompts/export')
    if (!r.ok) return
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'prompts.zip'
    a.click()
    URL.revokeObjectURL(url)
  }

  const rebuildColor = rebuildStatus === 'rebuilding' ? C.warn
    : rebuildStatus === 'done' ? C.success
    : rebuildStatus === 'error' ? C.error
    : C.accent

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: C.bg, color: C.text }}>

      {/* ── LEFT: Prompt list ─────────────────────────────────────────────── */}
      <div style={{ width: 240, flexShrink: 0, borderRight: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '12px 14px', borderBottom: `1px solid ${C.border}` }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: C.text, marginBottom: 8 }}>Prompt Studio</div>
          {/* Mode toggle */}
          <div style={{ display: 'flex', gap: 4, background: C.code, borderRadius: 6, padding: 3 }}>
            {(['prompts', 'agent'] as PageMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                style={{
                  flex: 1, padding: '5px 0', borderRadius: 5,
                  border: 'none',
                  background: mode === m ? C.accent : 'transparent',
                  color: mode === m ? '#fff' : C.muted,
                  fontSize: 12, fontWeight: 600, cursor: 'pointer',
                }}
              >
                {m === 'prompts' ? 'Prompts' : 'Agent Behavior'}
              </button>
            ))}
          </div>
        </div>

        {mode === 'prompts' && (
          <>
            <div style={{ flex: 1, overflowY: 'auto' }}>
              {PROMPT_ORDER.map((name) => {
                const isSelected = name === selectedName
                const isCurrent = prompts[name] !== undefined
                const isCurrentDirty = name === selectedName && dirty
                return (
                  <div
                    key={name}
                    onClick={() => setSelectedName(name)}
                    style={{
                      padding: '9px 14px', cursor: 'pointer',
                      borderLeft: isSelected ? `3px solid ${C.accent}` : '3px solid transparent',
                      background: isSelected ? C.accent + '18' : 'transparent',
                      borderBottom: `1px solid ${C.border}22`,
                    }}
                  >
                    <div style={{
                      fontSize: 12,
                      color: isSelected ? C.accent : isCurrent ? C.text : C.muted,
                      fontWeight: isSelected ? 600 : 400,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {isCurrentDirty && <span style={{ color: C.warn, marginRight: 5 }}>●</span>}
                      {PROMPT_LABELS[name] ?? name}
                    </div>
                    <div style={{ fontSize: 11, color: C.muted, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {NODE_DESCRIPTIONS[name]?.split('.')[0]}
                    </div>
                  </div>
                )
              })}
            </div>
            <div style={{ padding: 12, borderTop: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button onClick={handleExport} style={{ ...btnStyle(C.accent), textAlign: 'center', width: '100%' }}>
                ⬇ Export ZIP
              </button>
            </div>
          </>
        )}

        {mode === 'agent' && (
          <div style={{ padding: '10px 14px', color: C.muted, fontSize: 12 }}>
            <div style={{ marginBottom: 8 }}>Shows pipeline DAG, node types, and entity extractor tool configuration.</div>
            {agentConfig && (
              <div style={{ fontSize: 11, color: C.muted }}>
                {agentConfig.pipeline_nodes.length} nodes · {agentConfig.entity_extractor.tools.length} agent tools
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── RIGHT: Editor or Agent Config ───────────────────────────────────── */}
      {mode === 'agent' ? (
        <div style={{ flex: 1, overflow: 'hidden' }}>
          {agentConfig
            ? <AgentConfigPanel config={agentConfig} />
            : <div style={{ padding: 40, color: C.muted, fontSize: 13 }}>Loading agent configuration…</div>
          }
        </div>
      ) : (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

          {/* Toolbar */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '10px 20px', borderBottom: `1px solid ${C.border}`,
            background: C.panel2, flexShrink: 0, flexWrap: 'wrap',
          }}>
            {/* Prompt title */}
            <div style={{ flex: 1, minWidth: 200 }}>
              <div style={{ fontWeight: 700, fontSize: 14, color: C.text }}>
                {PROMPT_LABELS[selectedName] ?? selectedName}
                {dirty && <span style={{ color: C.warn, marginLeft: 8, fontSize: 12, fontWeight: 400 }}>● unsaved changes</span>}
              </div>
              <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>
                {NODE_DESCRIPTIONS[selectedName] ?? ''}
              </div>
            </div>

            {/* Save / Discard */}
            {dirty && (
              <>
                <button onClick={handleDiscard} style={btnStyle(C.muted)}>Discard</button>
                <button onClick={handleSave} disabled={saving} style={btnStyle(C.success)}>
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </>
            )}
            {savedMsg && (
              <span style={{ fontSize: 12, color: savedMsg.includes('fail') ? C.error : C.success, fontWeight: 600 }}>
                {savedMsg}
              </span>
            )}

            {/* Divider */}
            <div style={{ width: 1, height: 24, background: C.border, flexShrink: 0 }} />

            {/* Rebuild */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {rebuildMsg && (
                <span style={{ fontSize: 12, color: rebuildColor, maxWidth: 300 }}>{rebuildMsg}</span>
              )}
              <button
                onClick={handleRebuild}
                disabled={rebuildStatus === 'rebuilding'}
                style={btnStyle(rebuildColor, rebuildStatus === 'rebuilding')}
                title="Rebuild the LLM pipeline so all saved prompt changes take effect immediately"
              >
                {rebuildStatus === 'rebuilding' ? '⏳ Rebuilding…'
                  : rebuildStatus === 'done' ? '✓ Rebuilt'
                  : '⚡ Rebuild Pipeline'}
              </button>
            </div>
          </div>

          {/* Tip bar */}
          <div style={{
            padding: '6px 20px',
            background: '#1a1a28',
            borderBottom: `1px solid ${C.border}22`,
            fontSize: 11, color: C.muted, flexShrink: 0,
          }}>
            Save edits with <strong style={{ color: C.text }}>Save</strong>, then{' '}
            <strong style={{ color: C.text }}>Rebuild Pipeline</strong> — changes take effect immediately, no restart needed.
            {' '}Saves are versioned and persist across container rebuilds (requires <code style={{ color: C.text }}>PROMPTS_PERSIST_PATH</code> env var).
          </div>

          {/* Editor */}
          <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', padding: '16px 20px 0' }}>
            <textarea
              value={draft}
              onChange={(e) => { setDraft(e.target.value); setDirty(e.target.value !== (prompts[selectedName] ?? '')) }}
              spellCheck={false}
              style={{
                flex: 1,
                background: C.code,
                border: `1px solid ${dirty ? C.accent + '88' : C.border}`,
                borderRadius: 8,
                padding: '14px 18px',
                color: '#cfe0ff',
                fontSize: 13,
                fontFamily: '"Fira Code", "Fira Mono", "Cascadia Code", Consolas, monospace',
                lineHeight: 1.6,
                resize: 'none',
                outline: 'none',
                width: '100%',
                boxSizing: 'border-box',
                transition: 'border-color 0.15s',
              }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8, padding: '0 0 4px' }}>
              <span style={{ fontSize: 11, color: C.muted }}>
                {draft.length} chars · {draft.split('\n').length} lines
              </span>
              {dirty && (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={handleDiscard} style={btnStyle(C.muted)}>Discard</button>
                  <button onClick={handleSave} disabled={saving} style={btnStyle(C.success)}>
                    {saving ? 'Saving…' : '💾 Save'}
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* Version history (collapsible, below editor) */}
          <VersionHistory
            promptName={selectedName}
            onRestore={handleVersionRestore}
          />
        </div>
      )}
    </div>
  )
}
