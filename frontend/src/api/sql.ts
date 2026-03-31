import { apiFetch } from './client'
import type { ExecuteResult, FormatResult } from '../types'

export function executeSQL(sql: string): Promise<ExecuteResult> {
  return apiFetch<ExecuteResult>('/sql/execute', {
    method: 'POST',
    body: JSON.stringify({ sql }),
  })
}

export function formatSQL(sql: string): Promise<FormatResult> {
  return apiFetch<FormatResult>('/sql/format', {
    method: 'POST',
    body: JSON.stringify({ sql }),
  })
}
