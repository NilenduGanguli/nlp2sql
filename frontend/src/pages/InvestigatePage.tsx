import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useTraceStore } from '../store/traceStore'
import type { QueryTrace, TraceStep, PromptFile } from '../types'

// ── Colours consistent with app theme ──────────────────────────────────────
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
}

// ── Node → editable prompt file mapping ───────────────────────────────────
const NODE_PROMPTS: Record<string, string[]> = {
  enrich_query:        ['query_enricher_system', 'query_enricher_human'],
  classify_intent:     ['intent_classifier_system'],
  extract_entities:    ['entity_extractor_system'],
  check_clarification: ['clarification_agent_system', 'clarification_agent_human'],
  generate_sql:        ['sql_generator_system'],
}

const PROMPT_LABELS: Record<string, string> = {
  query_enricher_system:       'Query Enricher — System',
  query_enricher_human:        'Query Enricher — Human Template',
  intent_classifier_system:    'Intent Classifier — System',
  entity_extractor_system:     'Entity Extractor — System Template',
  clarification_agent_system:  'Clarification Agent — System',
  clarification_agent_human:   'Clarification Agent — Human Template',
  sql_generator_system:        'SQL Generator — System',
}

const NODE_LABELS: Record<string, string> = {
  enrich_query:        'Query Enricher',
  classify_intent:     'Intent Classifier',
  extract_entities:    'Entity Extractor (Agentic)',
  retrieve_schema:     'Schema Retrieval',
  check_clarification: 'Clarification Check',
  generate_sql:        'SQL Generator',
  validate_sql:        'SQL Validator',
  optimize_sql:        'SQL Optimizer',
  execute_query:       'Query Executor',
  format_result:       'Result Formatter',
}

// ── Op type colours ──────────────────────────────────────────────────────────
const OP_COLORS: Record<string, string> = {
  search_schema:         '#38bdf8',
  get_table_detail:      '#a78bfa',
  find_join_path:        '#fb923c',
  resolve_business_term: '#34d399',
  list_related_tables:   '#60a5fa',
  submit_entities:       '#4ade80',
  use_preresolved_fqns:  '#4ade80',
  expand_fk_neighbors:   '#fb923c',
}

const OP_HINTS: Record<string, string> = {
  search_schema:         'Text search across tables and columns',
  get_table_detail:      'Full column list + FK refs for one table',
  find_join_path:        'FK-based join path between two tables',
  resolve_business_term: 'Mapped business term → schema object',
  list_related_tables:   'FK-reachable tables from a seed table',
  submit_entities:       'Final extracted entities + confirmed FQNs',
  use_preresolved_fqns:  'Used FQNs pre-resolved by entity agent (resolution skipped)',
  expand_fk_neighbors:   '1-hop FK neighbour expansion',
}

// ── Agent-loop iteration parser ───────────────────────────────────────────────

interface AgentIteration {
  label: string        // "Iteration 1", "Force submit"
  thought: string
  action: string
  args: Record<string, unknown>
  isFinal: boolean
  rawText: string
}

function parseAgentIterations(raw: string): AgentIteration[] {
  const results: AgentIteration[] = []
  // Match [Iteration N] or [Force submit] markers
  const delimRe = /\[(Iteration \d+|Force submit)\]\s*/g
  const positions: Array<{ label: string; end: number }> = []
  let m: RegExpExecArray | null
  while ((m = delimRe.exec(raw)) !== null) {
    positions.push({ label: m[1], end: m.index + m[0].length })
  }
  for (let i = 0; i < positions.length; i++) {
    const { label, end } = positions[i]
    const nextEnd = i + 1 < positions.length
      ? positions[i + 1].end - positions[i + 1].label.length - 2
      : raw.length
    const text = raw.slice(end, nextEnd).trim()
    let thought = '', action = '', args: Record<string, unknown> = {}
    try {
      const jm = text.match(/\{[\s\S]*\}/)
      if (jm) {
        const parsed = JSON.parse(jm[0])
        thought = String(parsed.thought || '')
        action  = String(parsed.action || '')
        args    = (parsed.args as Record<string, unknown>) || {}
      }
    } catch { /* unparseable — show raw text */ }
    results.push({ label, thought, action, args, isFinal: action === 'submit_entities', rawText: text })
  }
  return results
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtMs(ms: number) {
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function usePrompts() {
  const [prompts, setPrompts] = useState<Record<string, string>>({})

  const reload = useCallback(() => {
    fetch('/api/prompts')
      .then((r) => r.json())
      .then((data: { prompts: PromptFile[] }) => {
        const map: Record<string, string> = {}
        for (const p of data.prompts) map[p.name] = p.content
        setPrompts(map)
      })
      .catch(() => {})
  }, [])

  useEffect(() => { reload() }, [reload])

  const save = useCallback(async (name: string, content: string): Promise<boolean> => {
    try {
      const r = await fetch(`/api/prompts/${name}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })
      if (r.ok) {
        setPrompts((prev) => ({ ...prev, [name]: content }))
        return true
      }
      return false
    } catch {
      return false
    }
  }, [])

  const exportZip = useCallback(async () => {
    const r = await fetch('/api/prompts/export')
    if (!r.ok) return
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'prompts.zip'
    a.click()
    URL.revokeObjectURL(url)
  }, [])

  return { prompts, reload, save, exportZip }
}

// ── Sub-components ──────────────────────────────────────────────────────────

function Pre({ text, maxH = 260 }: { text: string; maxH?: number }) {
  return (
    <pre
      style={{
        background: C.code,
        border: `1px solid ${C.border}`,
        borderRadius: 6,
        padding: '10px 14px',
        maxHeight: maxH,
        overflowY: 'auto',
        fontSize: 12,
        lineHeight: 1.55,
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        color: '#cfe0ff',
        margin: 0,
      }}
    >
      {text || '(empty)'}
    </pre>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 11, fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6, marginTop: 14 }}>
      {children}
    </div>
  )
}

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{ background: color + '22', color, border: `1px solid ${color}44`, borderRadius: 4, padding: '1px 7px', fontSize: 11, fontWeight: 600 }}>
      {label}
    </span>
  )
}

// ── Agent loop viewer (for extract_entities) ──────────────────────────────────

function AgentLoopViewer({ rawResponse }: { rawResponse: string }) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null)
  const iterations = parseAgentIterations(rawResponse)
  if (iterations.length === 0) {
    return <Pre text={rawResponse} maxH={240} />
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {iterations.map((it, idx) => {
        const color = it.isFinal ? C.success
          : OP_COLORS[it.action] || C.muted
        const isOpen = expandedIdx === idx
        return (
          <div
            key={idx}
            style={{
              border: `1px solid ${color}33`,
              borderRadius: 6,
              overflow: 'hidden',
              background: it.isFinal ? C.success + '08' : C.code,
            }}
          >
            {/* Row header */}
            <div
              onClick={() => setExpandedIdx(isOpen ? null : idx)}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 10,
                padding: '7px 12px', cursor: 'pointer', userSelect: 'none',
              }}
            >
              {/* Iteration badge */}
              <span style={{
                flexShrink: 0, marginTop: 1,
                fontSize: 10, fontWeight: 700, color, background: color + '18',
                border: `1px solid ${color}44`, borderRadius: 4,
                padding: '1px 6px', whiteSpace: 'nowrap',
              }}>
                {it.isFinal ? '✓ FINAL' : it.label}
              </span>
              {/* Action badge */}
              {it.action && (
                <span style={{
                  flexShrink: 0, marginTop: 1,
                  fontSize: 10, fontWeight: 700,
                  color: OP_COLORS[it.action] || C.muted,
                  background: (OP_COLORS[it.action] || C.muted) + '18',
                  border: `1px solid ${(OP_COLORS[it.action] || C.muted)}44`,
                  borderRadius: 4, padding: '1px 6px', fontFamily: 'monospace',
                }}>
                  {it.action}
                </span>
              )}
              {/* Thought */}
              <span style={{
                flex: 1, fontSize: 12, color: it.isFinal ? C.success : C.text,
                overflow: 'hidden', display: '-webkit-box',
                WebkitLineClamp: isOpen ? undefined : 2,
                WebkitBoxOrient: 'vertical',
                lineHeight: 1.45,
              }}>
                {it.thought || it.rawText.slice(0, 200)}
              </span>
              <span style={{ color: C.muted, fontSize: 12, flexShrink: 0, marginTop: 1 }}>
                {isOpen ? '▾' : '▸'}
              </span>
            </div>

            {/* Expanded: args */}
            {isOpen && (
              <div style={{ borderTop: `1px solid ${color}22`, padding: '8px 12px', paddingLeft: 12 }}>
                {it.isFinal ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {/* Tables + FQNs */}
                    {Array.isArray(it.args.tables) && (it.args.tables as string[]).length > 0 && (
                      <div>
                        <span style={{ fontSize: 11, color: C.muted, marginRight: 8 }}>Tables:</span>
                        {(it.args.tables as string[]).map((t) => (
                          <span key={t} style={{ fontSize: 11, color: C.text, background: C.panel, border: `1px solid ${C.border}`, borderRadius: 4, padding: '1px 6px', marginRight: 4 }}>{t}</span>
                        ))}
                      </div>
                    )}
                    {Array.isArray(it.args.table_fqns) && (it.args.table_fqns as string[]).length > 0 && (
                      <div>
                        <span style={{ fontSize: 11, color: C.muted, marginRight: 8 }}>Confirmed FQNs:</span>
                        {(it.args.table_fqns as string[]).map((fqn) => (
                          <span key={fqn} style={{ fontSize: 11, color: C.success, background: C.success + '10', border: `1px solid ${C.success}33`, borderRadius: 4, padding: '1px 6px', marginRight: 4 }}>{fqn}</span>
                        ))}
                      </div>
                    )}
                    {Array.isArray(it.args.conditions) && (it.args.conditions as string[]).length > 0 && (
                      <div>
                        <span style={{ fontSize: 11, color: C.muted, marginRight: 8 }}>Conditions:</span>
                        <code style={{ fontSize: 11, color: '#cfe0ff' }}>{(it.args.conditions as string[]).join(', ')}</code>
                      </div>
                    )}
                  </div>
                ) : (
                  <pre style={{ margin: 0, fontSize: 11, color: '#cfe0ff', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {JSON.stringify(it.args, null, 2)}
                  </pre>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Confirmed FQN list (for extract_entities output) ─────────────────────────

function ConfirmedFqnList({ fqns }: { fqns: string[] }) {
  if (!fqns.length) return null
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
      {fqns.map((fqn) => (
        <span
          key={fqn}
          style={{
            fontSize: 12, fontFamily: 'monospace', fontWeight: 600,
            color: C.success, background: C.success + '12',
            border: `1px solid ${C.success}44`,
            borderRadius: 5, padding: '3px 10px',
          }}
        >
          {fqn}
        </span>
      ))}
    </div>
  )
}

interface PromptEditorProps {
  name: string
  label: string
  liveContent: string        // what was actually sent in THIS query (from llm_call)
  savedContent: string       // current file content
  onSave: (name: string, content: string) => Promise<boolean>
}

function PromptEditor({ name, label, liveContent, savedContent, onSave }: PromptEditorProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(savedContent)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => { setDraft(savedContent) }, [savedContent])

  const handleSave = async () => {
    setSaving(true)
    const ok = await onSave(name, draft)
    setSaving(false)
    if (ok) {
      setSaved(true)
      setEditing(false)
      setTimeout(() => setSaved(false), 2000)
    }
  }

  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <SectionLabel>{label}</SectionLabel>
        <div style={{ flex: 1 }} />
        {!editing && (
          <button
            onClick={() => { setEditing(true); setDraft(savedContent) }}
            style={btnSm(C.accent)}
          >
            Edit
          </button>
        )}
        {editing && (
          <>
            <button onClick={handleSave} disabled={saving} style={btnSm(C.success)}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button onClick={() => setEditing(false)} style={btnSm(C.muted)}>Cancel</button>
          </>
        )}
        {saved && <span style={{ color: C.success, fontSize: 12 }}>✓ Saved</span>}
      </div>

      {/* Show what was actually sent (if differs from saved) */}
      {liveContent && liveContent !== savedContent && !editing && (
        <>
          <div style={{ fontSize: 11, color: C.warn, marginBottom: 4 }}>
            ⚠ This query used a different version (file was edited after the query):
          </div>
          <Pre text={liveContent} maxH={180} />
          <div style={{ fontSize: 11, color: C.muted, marginTop: 6, marginBottom: 4 }}>Current file content (editable below):</div>
          <Pre text={savedContent} maxH={120} />
        </>
      )}

      {!liveContent && !editing && <Pre text={savedContent} maxH={200} />}
      {liveContent && liveContent === savedContent && !editing && <Pre text={liveContent} maxH={200} />}

      {editing && (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          style={{
            width: '100%',
            minHeight: 220,
            background: C.code,
            border: `1px solid ${C.accent}`,
            borderRadius: 6,
            padding: '10px 14px',
            color: '#cfe0ff',
            fontSize: 12,
            fontFamily: 'monospace',
            lineHeight: 1.55,
            resize: 'vertical',
            boxSizing: 'border-box',
          }}
        />
      )}
    </div>
  )
}

function btnSm(color: string) {
  return {
    padding: '3px 10px',
    borderRadius: 4,
    border: `1px solid ${color}55`,
    background: color + '18',
    color,
    fontSize: 11,
    cursor: 'pointer',
    fontWeight: 600,
  } as React.CSSProperties
}

function GraphOpsTable({ ops }: { ops: import('../types').TraceGraphOp[] }) {
  if (!ops.length) return null
  return (
    <div>
      <SectionLabel>Graph Operations</SectionLabel>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              {['Operation', 'Params', 'Results'].map((h) => (
                <th key={h} style={{ textAlign: 'left', padding: '5px 10px', background: C.panel, color: C.muted, borderBottom: `1px solid ${C.border}`, fontWeight: 600 }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ops.map((op, i) => {
              const opColor = OP_COLORS[op.op] || C.accent
              const opHint  = OP_HINTS[op.op]
              return (
                <tr key={i} style={{ borderBottom: `1px solid ${C.border}22` }}>
                  <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>
                    <span style={{
                      fontFamily: 'monospace', fontWeight: 700, fontSize: 11,
                      color: opColor, background: opColor + '15',
                      border: `1px solid ${opColor}33`, borderRadius: 4,
                      padding: '2px 7px',
                    }}>
                      {op.op}
                    </span>
                    {opHint && (
                      <div style={{ fontSize: 10, color: C.muted, marginTop: 2, fontStyle: 'italic' }}>{opHint}</div>
                    )}
                  </td>
                  <td style={{ padding: '6px 10px', color: C.text, fontFamily: 'monospace', maxWidth: 300, wordBreak: 'break-word', fontSize: 11 }}>
                    {JSON.stringify(op.params)}
                  </td>
                  <td style={{ padding: '6px 10px', color: op.result_count > 0 ? C.success : C.muted }}>
                    {op.result_count} result{op.result_count !== 1 ? 's' : ''}
                    {op.result_sample.length > 0 && (
                      <details style={{ display: 'inline', marginLeft: 8 }}>
                        <summary style={{ cursor: 'pointer', color: C.muted, fontSize: 11 }}>sample</summary>
                        <pre style={{ margin: '4px 0 0', fontSize: 11, color: '#cfe0ff', background: C.code, padding: 6, borderRadius: 4, maxHeight: 120, overflowY: 'auto' }}>
                          {JSON.stringify(op.result_sample, null, 2)}
                        </pre>
                      </details>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

interface StepCardProps {
  step: TraceStep
  prompts: Record<string, string>
  onSavePrompt: (name: string, content: string) => Promise<boolean>
  defaultOpen?: boolean
}

function StepCard({ step, prompts, onSavePrompt, defaultOpen = false }: StepCardProps) {
  const [open, setOpen] = useState(defaultOpen)
  const nodeLabel = NODE_LABELS[step.node] ?? step.node
  const hasError  = !!step.error
  const hasLlm    = !!step.llm_call
  const promptKeys = NODE_PROMPTS[step.node] ?? []

  const isAgentExtractor = step.node === 'extract_entities'
  const isSchemaRetrieval = step.node === 'retrieve_schema'

  // Summary values for entity extractor
  const iterations  = isAgentExtractor ? (step.output_summary?.iterations as number | undefined) : undefined
  const confirmedFqns: string[] = isAgentExtractor
    ? ((step.output_summary?.entity_table_fqns as string[]) || [])
    : []

  // Fast-path detection for retrieve_schema
  const usedPreresolved = isSchemaRetrieval &&
    step.graph_ops.some((op) => op.op === 'use_preresolved_fqns')

  return (
    <div style={{ border: `1px solid ${hasError ? C.error + '55' : isAgentExtractor ? C.accent + '44' : C.border}`, borderRadius: 8, marginBottom: 8, overflow: 'hidden' }}>
      {/* Header */}
      <div
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
          background: isAgentExtractor ? C.accent + '0a' : C.panel2,
          cursor: 'pointer', userSelect: 'none',
        }}
      >
        <span style={{ color: open ? C.accent : C.muted, fontSize: 14, width: 16 }}>{open ? '▾' : '▸'}</span>
        <span style={{ fontWeight: 600, color: C.text, fontSize: 13 }}>{nodeLabel}</span>
        <Badge label={step.step_label} color={hasError ? C.error : C.accent} />
        <span style={{ color: C.muted, fontSize: 12 }}>{fmtMs(step.duration_ms)}</span>
        {hasError && <Badge label="ERROR" color={C.error} />}
        {hasLlm && !isAgentExtractor && <Badge label="LLM" color={C.warn} />}
        {isAgentExtractor && iterations != null && (
          <Badge label={`${iterations} iteration${iterations !== 1 ? 's' : ''}`} color={C.warn} />
        )}
        {isAgentExtractor && confirmedFqns.length > 0 && (
          <Badge label={`${confirmedFqns.length} table${confirmedFqns.length !== 1 ? 's' : ''} confirmed`} color={C.success} />
        )}
        {!isAgentExtractor && step.graph_ops.length > 0 && (
          <Badge label={`${step.graph_ops.length} graph ops`} color="#38bdf8" />
        )}
        {isAgentExtractor && step.graph_ops.length > 0 && (
          <Badge label={`${step.graph_ops.length} tool calls`} color="#38bdf8" />
        )}
        {usedPreresolved && <Badge label="fast path" color={C.success} />}
        <div style={{ flex: 1 }} />
        {/* Quick summary — different content per node type */}
        {isAgentExtractor && confirmedFqns.length > 0 ? (
          <span style={{ color: C.success, fontSize: 11, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {confirmedFqns.join(', ')}
          </span>
        ) : (
          <span style={{ color: C.muted, fontSize: 11, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {Object.entries(step.output_summary ?? {})
              .filter(([k]) => !['entity_table_fqns', 'iterations'].includes(k))
              .slice(0, 3)
              .map(([k, v]) => `${k}: ${Array.isArray(v) ? (v as unknown[]).join(', ') : String(v)}`)
              .join(' · ')}
          </span>
        )}
      </div>

      {open && (
        <div style={{ padding: '14px 16px', background: C.panel }}>

          {/* Error */}
          {hasError && (
            <div style={{ background: C.error + '18', border: `1px solid ${C.error}55`, borderRadius: 6, padding: '8px 12px', color: C.error, fontSize: 12, marginBottom: 12 }}>
              <strong>Error:</strong> {step.error}
            </div>
          )}

          {/* ── ENTITY EXTRACTOR: agentic investigation ───────────────── */}
          {isAgentExtractor ? (
            <>
              {/* Editable system prompt */}
              {promptKeys.map((promptKey) => (
                <PromptEditor
                  key={promptKey}
                  name={promptKey}
                  label={PROMPT_LABELS[promptKey] ?? promptKey}
                  liveContent={hasLlm ? step.llm_call!.system_prompt : ''}
                  savedContent={prompts[promptKey] ?? '(not loaded)'}
                  onSave={onSavePrompt}
                />
              ))}

              {/* Initial query sent */}
              {hasLlm && (
                <>
                  <SectionLabel>Query Sent to Agent</SectionLabel>
                  <Pre text={step.llm_call!.user_prompt} maxH={80} />
                </>
              )}

              {/* Agent investigation loop */}
              {hasLlm && step.llm_call!.raw_response && (
                <>
                  <SectionLabel>
                    Investigation Loop
                    {iterations != null && (
                      <span style={{ marginLeft: 8, color: C.warn, fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
                        ({iterations} iteration{iterations !== 1 ? 's' : ''})
                      </span>
                    )}
                  </SectionLabel>
                  <AgentLoopViewer rawResponse={step.llm_call!.raw_response} />
                </>
              )}

              {/* Confirmed FQNs */}
              {confirmedFqns.length > 0 && (
                <>
                  <SectionLabel>Confirmed Table FQNs</SectionLabel>
                  <ConfirmedFqnList fqns={confirmedFqns} />
                </>
              )}

              {/* Tool calls table */}
              {step.graph_ops.length > 0 && (
                <>
                  <SectionLabel>Tool Call Summary</SectionLabel>
                  <GraphOpsTable ops={step.graph_ops} />
                </>
              )}

              {/* Parsed entity output */}
              {hasLlm && step.llm_call!.parsed_output != null && (
                <>
                  <SectionLabel>Extracted Entity Dict</SectionLabel>
                  <Pre
                    text={typeof step.llm_call!.parsed_output === 'string'
                      ? step.llm_call!.parsed_output
                      : JSON.stringify(step.llm_call!.parsed_output, null, 2)}
                    maxH={160}
                  />
                </>
              )}
            </>
          ) : (
            <>
              {/* ── RETRIEVE SCHEMA: fast-path note ───────────────────── */}
              {usedPreresolved && (
                <div style={{
                  background: C.success + '10', border: `1px solid ${C.success}44`,
                  borderRadius: 6, padding: '8px 14px', fontSize: 12,
                  color: C.success, marginBottom: 12,
                  display: 'flex', alignItems: 'flex-start', gap: 8,
                }}>
                  <span style={{ fontWeight: 700 }}>✓ Fast path</span>
                  <span style={{ color: C.text }}>
                    Table FQNs were pre-resolved by the entity extraction agent.
                    Name resolution steps skipped — schema context built directly from confirmed FQNs.
                  </span>
                </div>
              )}

              {/* Editable prompts for this node */}
              {promptKeys.map((promptKey) => {
                const isSystem = promptKey.endsWith('_system')
                const liveContent = hasLlm
                  ? (isSystem ? step.llm_call!.system_prompt : step.llm_call!.user_prompt)
                  : ''
                return (
                  <PromptEditor
                    key={promptKey}
                    name={promptKey}
                    label={PROMPT_LABELS[promptKey] ?? promptKey}
                    liveContent={liveContent}
                    savedContent={prompts[promptKey] ?? '(not loaded)'}
                    onSave={onSavePrompt}
                  />
                )
              })}

              {/* For nodes with LLM but no editable prompts show raw */}
              {hasLlm && promptKeys.length === 0 && (
                <>
                  <SectionLabel>System Prompt</SectionLabel>
                  <Pre text={step.llm_call!.system_prompt} />
                  <SectionLabel>User Prompt</SectionLabel>
                  <Pre text={step.llm_call!.user_prompt} />
                </>
              )}

              {/* User prompt (read-only, when only system prompt is editable) */}
              {hasLlm && promptKeys.length > 0 && promptKeys.every((k) => k.endsWith('_system')) && (
                <>
                  <SectionLabel>Actual User Message Sent</SectionLabel>
                  <Pre text={step.llm_call!.user_prompt} maxH={180} />
                </>
              )}

              {/* LLM response */}
              {hasLlm && step.llm_call!.raw_response && (
                <>
                  <SectionLabel>LLM Raw Response</SectionLabel>
                  <Pre text={step.llm_call!.raw_response} maxH={240} />
                </>
              )}

              {/* Parsed output */}
              {hasLlm && step.llm_call!.parsed_output != null && (
                <>
                  <SectionLabel>Parsed Output</SectionLabel>
                  <Pre
                    text={typeof step.llm_call!.parsed_output === 'string'
                      ? step.llm_call!.parsed_output
                      : JSON.stringify(step.llm_call!.parsed_output, null, 2)}
                    maxH={160}
                  />
                </>
              )}

              {/* Graph ops */}
              <GraphOpsTable ops={step.graph_ops} />

              {/* Output summary */}
              {Object.keys(step.output_summary ?? {}).length > 0 && (
                <>
                  <SectionLabel>Output Summary</SectionLabel>
                  <Pre text={JSON.stringify(step.output_summary, null, 2)} maxH={140} />
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export const InvestigatePage: React.FC = () => {
  const { traces, activeTraceId, pendingSteps, setActiveTrace } = useTraceStore()
  const { prompts, save: savePrompt, exportZip } = usePrompts()
  const [rebuildStatus, setRebuildStatus] = useState<'idle' | 'rebuilding' | 'done' | 'error'>('idle')
  const rebuildTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleRebuild = useCallback(async () => {
    setRebuildStatus('rebuilding')
    try {
      const r = await fetch('/api/admin/rebuild-pipeline', { method: 'POST' })
      if (r.ok) {
        if (rebuildTimerRef.current) clearTimeout(rebuildTimerRef.current)
        rebuildTimerRef.current = setTimeout(() => {
          setRebuildStatus('done')
          rebuildTimerRef.current = setTimeout(() => setRebuildStatus('idle'), 3000)
        }, 2000)
      } else {
        setRebuildStatus('error')
        rebuildTimerRef.current = setTimeout(() => setRebuildStatus('idle'), 5000)
      }
    } catch {
      setRebuildStatus('error')
      rebuildTimerRef.current = setTimeout(() => setRebuildStatus('idle'), 5000)
    }
  }, [])

  const activeTrace: QueryTrace | null = traces.find((t) => t.id === activeTraceId) ?? (traces[0] ?? null)
  const displaySteps: TraceStep[] = activeTrace
    ? (activeTrace.steps.length > 0 ? activeTrace.steps : pendingSteps)
    : pendingSteps

  const isLive = activeTrace != null && activeTrace.steps.length === 0 && pendingSteps.length > 0

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: C.bg, color: C.text }}>

      {/* ── Left: query history list ── */}
      <div style={{ width: 260, flexShrink: 0, borderRight: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '12px 14px', borderBottom: `1px solid ${C.border}`, fontWeight: 700, fontSize: 13, color: C.text }}>
          Query Traces
          <span style={{ float: 'right', color: C.muted, fontSize: 11, fontWeight: 400 }}>{traces.length} saved</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {traces.length === 0 && (
            <div style={{ padding: 20, color: C.muted, fontSize: 12 }}>Run a query in the Chat tab to capture its trace here.</div>
          )}
          {traces.map((t) => {
            const isActive = t.id === (activeTrace?.id ?? '')
            return (
              <div
                key={t.id}
                onClick={() => setActiveTrace(t.id)}
                style={{
                  padding: '10px 14px', cursor: 'pointer',
                  borderLeft: isActive ? `3px solid ${C.accent}` : '3px solid transparent',
                  background: isActive ? C.accent + '18' : 'transparent',
                  borderBottom: `1px solid ${C.border}22`,
                }}
              >
                <div style={{ fontSize: 12, color: isActive ? C.accent : C.text, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {t.query}
                </div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 3 }}>
                  {t.timestamp instanceof Date
                    ? t.timestamp.toLocaleTimeString()
                    : new Date(t.timestamp as unknown as string).toLocaleTimeString()}
                  {' · '}
                  {t.steps.length > 0 ? `${t.steps.length} steps` : 'live…'}
                </div>
              </div>
            )
          })}
        </div>

        {/* Buttons */}
        <div style={{ padding: 12, borderTop: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button
            onClick={handleRebuild}
            disabled={rebuildStatus === 'rebuilding'}
            style={{
              ...btnSm(rebuildStatus === 'done' ? C.success : rebuildStatus === 'error' ? C.error : C.warn),
              width: '100%', textAlign: 'center', padding: '7px 0',
              cursor: rebuildStatus === 'rebuilding' ? 'not-allowed' : 'pointer',
            }}
            title="Rebuild pipeline so saved prompt edits take effect"
          >
            {rebuildStatus === 'rebuilding' ? '⏳ Rebuilding…'
              : rebuildStatus === 'done' ? '✓ Pipeline Rebuilt'
              : rebuildStatus === 'error' ? '✗ Rebuild Failed'
              : 'Rebuild Pipeline'}
          </button>
          <button
            onClick={exportZip}
            style={{ ...btnSm(C.accent), width: '100%', textAlign: 'center', padding: '7px 0' }}
          >
            ⬇ Export Prompts (ZIP)
          </button>
        </div>
      </div>

      {/* ── Right: trace details ── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
        {!activeTrace && traces.length === 0 && (
          <div style={{ paddingTop: 60, textAlign: 'center', color: C.muted }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🔬</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: C.text, marginBottom: 8 }}>Investigate Query</div>
            <div style={{ fontSize: 13 }}>
              Every query you run will have its full processing lifecycle recorded here:<br />
              prompts, LLM calls, graph searches, SQL generation, validation, and execution.
            </div>
          </div>
        )}

        {(activeTrace || isLive) && (
          <>
            {/* Query header */}
            <div style={{ marginBottom: 16, padding: '12px 16px', background: C.panel, borderRadius: 8, border: `1px solid ${C.border}` }}>
              <div style={{ fontSize: 11, color: C.muted, marginBottom: 4 }}>
                Query
                {isLive && <span style={{ marginLeft: 8, color: C.warn, fontWeight: 600 }}>● LIVE</span>}
              </div>
              <div style={{ fontSize: 14, color: C.text, fontWeight: 500 }}>
                {activeTrace?.query ?? '(loading…)'}
              </div>
              {activeTrace && activeTrace.steps.length > 0 && (
                <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                  <span style={{ fontSize: 11, color: C.muted }}>
                    {activeTrace.steps.length} steps ·{' '}
                    {fmtMs(activeTrace.steps.reduce((sum, s) => sum + s.duration_ms, 0))} total
                  </span>
                  {activeTrace.steps.filter((s) => s.error).length > 0 && (
                    <Badge label={`${activeTrace.steps.filter((s) => s.error).length} error(s)`} color={C.error} />
                  )}
                  {activeTrace.steps.filter((s) => s.llm_call).length > 0 && (
                    <Badge label={`${activeTrace.steps.filter((s) => s.llm_call).length} LLM nodes`} color={C.warn} />
                  )}
                  {(() => {
                    const totalOps = activeTrace.steps.reduce((sum, s) => sum + s.graph_ops.length, 0)
                    const agentStep = activeTrace.steps.find((s) => s.node === 'extract_entities')
                    const iterations = agentStep?.output_summary?.iterations as number | undefined
                    return (
                      <>
                        {totalOps > 0 && <Badge label={`${totalOps} graph ops`} color="#38bdf8" />}
                        {iterations != null && <Badge label={`entity agent: ${iterations} iterations`} color={C.accent} />}
                      </>
                    )
                  })()}
                </div>
              )}
            </div>

            {/* Step cards */}
            {displaySteps.map((step, i) => (
              <StepCard
                key={`${step.node}-${i}`}
                step={step}
                prompts={prompts}
                onSavePrompt={savePrompt}
                defaultOpen={
                  step.node === 'generate_sql' ||
                  step.node === 'extract_entities' ||
                  step.node === 'retrieve_schema' ||
                  !!(step.error)
                }
              />
            ))}

            {isLive && (
              <div style={{ padding: '12px 16px', color: C.muted, fontSize: 12, textAlign: 'center' }}>
                Pipeline is running — steps will appear as they complete…
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
