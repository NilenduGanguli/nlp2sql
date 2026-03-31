import React, { useState, useEffect, useRef, useCallback } from 'react'

interface SearchBoxProps {
  value: string
  onChange: (val: string) => void
  placeholder?: string
  debounceMs?: number
  isLoading?: boolean
  style?: React.CSSProperties
}

export const SearchBox: React.FC<SearchBoxProps> = ({
  value,
  onChange,
  placeholder = 'Search…',
  debounceMs = 200,
  isLoading = false,
  style,
}) => {
  const [local, setLocal] = useState(value)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Sync external value resets (e.g. clearing from parent)
  useEffect(() => {
    if (value === '') setLocal('')
  }, [value])

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const v = e.target.value
      setLocal(v)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => onChange(v), debounceMs)
    },
    [onChange, debounceMs],
  )

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setLocal('')
      if (timerRef.current) clearTimeout(timerRef.current)
      onChange('')
    }
  }

  return (
    <div style={{ position: 'relative', ...style }}>
      <span
        style={{
          position: 'absolute',
          left: 10,
          top: '50%',
          transform: 'translateY(-50%)',
          color: '#9090a8',
          fontSize: 13,
          pointerEvents: 'none',
        }}
      >
        {isLoading ? '⏳' : '🔍'}
      </span>
      <input
        type="text"
        value={local}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        style={{
          width: '100%',
          background: '#1e1e2e',
          border: '1px solid #3a3a5c',
          borderRadius: 6,
          padding: '6px 10px 6px 30px',
          color: '#e0e0f0',
          fontSize: 13,
          outline: 'none',
          transition: 'border-color 0.15s',
        }}
        onFocus={(e) => (e.target.style.borderColor = '#7c6af7')}
        onBlur={(e) => (e.target.style.borderColor = '#3a3a5c')}
      />
      {local && (
        <button
          onClick={() => {
            setLocal('')
            onChange('')
          }}
          style={{
            position: 'absolute',
            right: 6,
            top: '50%',
            transform: 'translateY(-50%)',
            background: 'none',
            border: 'none',
            color: '#9090a8',
            fontSize: 14,
            lineHeight: 1,
            padding: 2,
          }}
        >
          ✕
        </button>
      )}
    </div>
  )
}
