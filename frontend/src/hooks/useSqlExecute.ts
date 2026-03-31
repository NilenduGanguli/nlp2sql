import { useMutation } from '@tanstack/react-query'
import { executeSQL } from '../api/sql'
import type { ExecuteResult } from '../types'

export function useSqlExecute() {
  return useMutation<ExecuteResult, Error, string>({
    mutationFn: (sql: string) => executeSQL(sql),
  })
}
