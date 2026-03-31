import React from 'react'
import MonacoEditor from '@monaco-editor/react'

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
  const prefersDark =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches

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
      onMount={(editor, monaco) => {
        if (onRun) {
          editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, onRun)
        }
      }}
    />
  )
}
