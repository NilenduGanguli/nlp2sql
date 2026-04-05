import React, { useRef, useEffect } from 'react'
import MonacoEditor, { type OnMount } from '@monaco-editor/react'

interface SqlEditorProps {
  value: string
  onChange: (value: string) => void
  onRun?: () => void
  height?: string | number
}

export const SqlEditor: React.FC<SqlEditorProps> = ({
  value,
  onChange,
  onRun,
  height = '100%',
}) => {
  // Keep a stable ref so the Monaco command always calls the latest onRun
  // (avoids stale closure — onMount fires once, but sql changes over time)
  const onRunRef = useRef(onRun)
  useEffect(() => { onRunRef.current = onRun }, [onRun])

  const prefersDark =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches

  const handleMount: OnMount = (editor, monaco) => {
    editor.addCommand(
      monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter,
      () => onRunRef.current?.(),
    )
  }

  return (
    <MonacoEditor
      height={height}
      defaultLanguage="sql"
      language="sql"
      value={value}
      theme={prefersDark ? 'vs-dark' : 'vs'}
      onChange={(val) => onChange(val ?? '')}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        lineNumbers: 'on',
        scrollBeyondLastLine: false,
        wordWrap: 'on',
        automaticLayout: true,
        tabSize: 2,
        renderWhitespace: 'selection',
        quickSuggestions: false,
      }}
      onMount={handleMount}
    />
  )
}
