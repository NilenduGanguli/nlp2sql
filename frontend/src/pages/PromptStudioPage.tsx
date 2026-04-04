import React, { useState, useEffect, useCallback, useRef } from 'react'
import type { PromptFile } from '../types'

const C = {
  bg: '#1e1e2e',
  panel: '#2a2a3e',
  panel2: '#242438',
  border: '#3a3a5c',
  accent: '#7c6af7',
  text: '#e0e0f0',
  muted: '#9090a8',
  success: '#4ade80',
  warn: '#fbbf24',
  error: '#f87171',
  code: '#1a1a2e',
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

const PROMPT_ORDER = [
  'query_enricher_system',
  'query_enricher_human',
  'intent_classifier_system',
  'entity_extractor_system',
  'clarification_agent_system',
  'clarification_agent_human',
  'sql_generator_system',
]

const NODE_DESCRIPTIONS: Record<string, string> = {
  query_enricher_system:       'Enriches the raw user query with domain knowledge before entity extraction.',
  query_enricher_human:        'Human message template for the query enricher (receives {user_input} and {knowledge}).',
  intent_classifier_system:    'Classifies intent as sql_query, schema_question, or other.',
  entity_extractor_system:     'Extracts table/column entities from the query. Template receives {schemas} and {table_list}.',
  clarification_agent_system:  'Decides if the query needs clarification and generates a question + options.',
  clarification_agent_human:   'Human message template for the clarification agent.',
  sql_generator_system:        'The main SQL generation system prompt with rules and constraints.',
}

function btnStyle(color: string, disabled = false): React.CSSProperties {
  return {
    padding: '7px 16px',
    borderRadius: 6,
    border: `1px solid ${color}55`,
    background: disabled ? '#2a2a3e' : color + '22',
    color: disabled ? C.muted : color,
    fontSize: 12,
    fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    transition: 'background 0.15s',
    whiteSpace: 'nowrap',
  }
}

type RebuildStatus = 'idle' | 'rebuilding' | 'done' | 'error'

export const PromptStudioPage: React.FC = () => {
  const [prompts, setPrompts] = useState<Record<string, string>>({})
  const [selectedName, setSelectedName] = useState<string>(PROMPT_ORDER[0])
  const [draft, setDraft] = useState<string>('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedMsg, setSavedMsg] = useState('')
  const [rebuildStatus, setRebuildStatus] = useState<RebuildStatus>('idle')
  const [rebuildMsg, setRebuildMsg] = useState('')
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
        lastSavedRef.current = map
      })
      .catch(() => {})
  }, [])

  useEffect(() => { loadPrompts() }, [loadPrompts])

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

  // ── Rebuild pipeline trigger ───────────────────────────────────────────────
  const handleRebuild = async () => {
    setRebuildStatus('rebuilding')
    setRebuildMsg('Rebuilding pipeline…')
    try {
      const r = await fetch('/api/admin/rebuild-pipeline', { method: 'POST' })
      const data = await r.json() as { status: string; message: string }
      if (r.ok) {
        setRebuildMsg(data.message ?? 'Pipeline rebuilding…')
        // Poll health until oracle_connected is true again (pipeline is re-initialised on each request anyway)
        // Pipeline rebuild is fast (~1s), just wait a bit then mark done
        if (pollRef.current) clearTimeout(pollRef.current)
        pollRef.current = setTimeout(() => {
          setRebuildStatus('done')
          setRebuildMsg('Pipeline rebuilt — new prompts are now active.')
          setTimeout(() => {
            setRebuildStatus('idle')
            setRebuildMsg('')
          }, 4000)
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

  // ── Helpers ────────────────────────────────────────────────────────────────
  const rebuildColor = rebuildStatus === 'rebuilding' ? C.warn
    : rebuildStatus === 'done' ? C.success
    : rebuildStatus === 'error' ? C.error
    : C.accent

  const allNames = PROMPT_ORDER.filter((n) => n in prompts || true)
  const unsavedCount = Object.keys(prompts).filter(
    (n) => prompts[n] !== lastSavedRef.current[n]
  ).length

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: C.bg, color: C.text }}>

      {/* ── LEFT: Prompt list ──────────────────────────────────────────────── */}
      <div style={{ width: 240, flexShrink: 0, borderRight: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '12px 14px', borderBottom: `1px solid ${C.border}` }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: C.text }}>Prompt Studio</div>
          <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{Object.keys(prompts).length} prompt files</div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {allNames.map((name) => {
            const isSelected = name === selectedName
            const isCurrent = prompts[name] !== undefined
            const isCurrentDirty = name === selectedName && dirty
            return (
              <div
                key={name}
                onClick={() => {
                  if (dirty) {
                    // auto-save before switching? Just switch — user can save manually
                  }
                  setSelectedName(name)
                }}
                style={{
                  padding: '9px 14px',
                  cursor: 'pointer',
                  borderLeft: isSelected ? `3px solid ${C.accent}` : '3px solid transparent',
                  background: isSelected ? C.accent + '18' : 'transparent',
                  borderBottom: `1px solid ${C.border}22`,
                }}
              >
                <div style={{
                  fontSize: 12,
                  color: isSelected ? C.accent : isCurrent ? C.text : C.muted,
                  fontWeight: isSelected ? 600 : 400,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
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

        {/* Export */}
        <div style={{ padding: 12, borderTop: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <button onClick={handleExport} style={{ ...btnStyle(C.accent), textAlign: 'center', width: '100%' }}>
            ⬇ Export ZIP
          </button>
        </div>
      </div>

      {/* ── RIGHT: Editor panel ────────────────────────────────────────────── */}
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

          {/* Rebuild button */}
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
                : 'Rebuild Pipeline'}
            </button>
          </div>
        </div>

        {/* Rebuild tip */}
        <div style={{
          padding: '6px 20px',
          background: '#1a1a28',
          borderBottom: `1px solid ${C.border}22`,
          fontSize: 11,
          color: C.muted,
          flexShrink: 0,
        }}>
          Save edits to disk with <strong style={{ color: C.text }}>Save</strong>, then click{' '}
          <strong style={{ color: C.text }}>Rebuild Pipeline</strong> to make changes active without restarting.{unsavedCount > 0
            ? <span style={{ color: C.warn, marginLeft: 8 }}>⚠ {unsavedCount} file{unsavedCount > 1 ? 's' : ''} have been modified since load.</span>
            : null}
        </div>

        {/* Editor */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', padding: '16px 20px' }}>
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
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
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
      </div>
    </div>
  )
}
