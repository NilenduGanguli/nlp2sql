import { useMutation } from '@tanstack/react-query'
import { formatSQL } from '../api/sql'
import type { FormatResult } from '../types'

export function useSqlFormat() {
  return useMutation<FormatResult, Error, string>({
    mutationFn: (sql: string) => formatSQL(sql),
  })
}
