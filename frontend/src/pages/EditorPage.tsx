import React, { useState, useCallback } from 'react'
import { SqlEditor } from '../components/editor/SqlEditor'
import { ResultGrid } from '../components/editor/ResultGrid'
import { useSqlExecute } from '../hooks/useSqlExecute'
import { useSqlFormat } from '../hooks/useSqlFormat'

interface EditorPageProps {
  initialSql?: string
  onSqlChange?: (sql: string) => void
}

export const EditorPage: React.FC<EditorPageProps> = ({ initialSql = '', onSqlChange }) => {
  const [sql, setSql] = useState(initialSql)
  const { mutate: executeSql, data: execResult, isPending: executing, error: execError } = useSqlExecute()
  const { mutate: formatSql, isPending: formatting } = useSqlFormat()

  // Sync if parent passes new initialSql (from "Open in Editor")
  React.useEffect(() => {
    if (initialSql && initialSql !== sql) {
      setSql(initialSql)
    }
    // Only run when initialSql changes from the parent
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSql])

  const handleSqlChange = useCallback(
    (value: string) => {
      setSql(value)
      onSqlChange?.(value)
    },
    [onSqlChange],
  )

  const handleRun = useCallback(() => {
    if (sql.trim()) {
      executeSql(sql)
    }
  }, [sql, executeSql])

  const handleFormat = useCallback(() => {
    if (sql.trim()) {
      formatSql(sql, {
        onSuccess: (result) => setSql(result.formatted_sql),
      })
    }
  }, [sql, formatSql])

  const execErrMsg =
    execError?.message ?? (execResult?.error ? execResult.error : undefined)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 16px',
          borderBottom: '1px solid #3a3a5c',
          background: '#2a2a3e',
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 13, color: '#9090a8', fontWeight: 500 }}>SQL Editor</span>
        <div style={{ flex: 1 }} />
        <button
          onClick={handleFormat}
          disabled={formatting || !sql.trim()}
          style={{
            padding: '5px 14px',
            background: 'none',
            border: '1px solid #3a3a5c',
            borderRadius: 5,
            color: '#9090a8',
            fontSize: 12,
            cursor: sql.trim() ? 'pointer' : 'not-allowed',
          }}
        >
          {formatting ? 'Formatting…' : 'Format'}
        </button>
        <button
          onClick={handleRun}
          disabled={executing || !sql.trim()}
          style={{
            padding: '5px 18px',
            background: sql.trim() && !executing ? '#7c6af7' : '#3a3a5c',
            border: 'none',
            borderRadius: 5,
            color: sql.trim() && !executing ? '#fff' : '#6a6a8a',
            fontSize: 13,
            fontWeight: 600,
            cursor: sql.trim() && !executing ? 'pointer' : 'not-allowed',
          }}
        >
          {executing ? 'Running…' : 'Run (Ctrl+Enter)'}
        </button>
      </div>

      {/* Editor pane */}
      <div style={{ flex: '0 0 45%', borderBottom: '1px solid #3a3a5c', overflow: 'hidden' }}>
        <SqlEditor value={sql} onChange={handleSqlChange} onRun={handleRun} />
      </div>

      {/* Results pane */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <ResultGrid
          columns={execResult?.columns ?? []}
          rows={execResult?.rows ?? []}
          loading={executing}
          error={execErrMsg}
        />
      </div>
    </div>
  )
}
