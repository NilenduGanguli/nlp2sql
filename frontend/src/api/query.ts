import type { ConversationMessage, QueryResult, QueryStep, TraceStep } from '../types'

interface ClarificationPair {
  question: string
  answer: string
}

export interface AcceptedCandidatePayload {
  id: string
  sql: string
  explanation: string
  interpretation: string
}

export interface RejectedCandidatePayload extends AcceptedCandidatePayload {
  rejection_reason?: string
}

export interface SessionMatchEvent {
  matched_entry_id: string
  candidates: Array<{ id: string; interpretation: string; sql: string; explanation: string }>
  original_query: string
}

/**
 * Send accept feedback for a generated query, including all candidate
 * interpretations the user accepted/rejected and a session digest used
 * for comprehensive session learning.
 */
export async function acceptGeneratedQuery(
  userInput: string,
  acceptedCandidates: AcceptedCandidatePayload[],
  rejectedCandidates: RejectedCandidatePayload[],
  executedCandidateId: string | null,
  clarificationPairs: ClarificationPair[],
  sessionDigest: Record<string, unknown>,
): Promise<{ status: string }> {
  const res = await fetch('/api/query/accept-query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_input: userInput,
      accepted_candidates: acceptedCandidates,
      rejected_candidates: rejectedCandidates,
      executed_candidate_id: executedCandidateId,
      clarification_pairs: clarificationPairs,
      session_digest: sessionDigest,
      // Legacy fields for backward-compat with any older server:
      sql: acceptedCandidates[0]?.sql ?? '',
      explanation: acceptedCandidates[0]?.explanation ?? '',
      accepted: acceptedCandidates.length > 0,
    }),
  })
  return res.json()
}

interface SSEEvent {
  type: string
  data: Record<string, unknown>
}

function parseSSEBlock(block: string): SSEEvent | null {
  const lines = block.split('\n')
  let eventType = 'message'
  let dataStr = ''

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      eventType = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      dataStr = line.slice(6).trim()
    }
  }

  if (!dataStr) return null
  try {
    return { type: eventType, data: JSON.parse(dataStr) as Record<string, unknown> }
  } catch {
    return null
  }
}

/**
 * Stream a natural-language query via SSE (POST + fetch + ReadableStream).
 * Returns an AbortController — call .abort() to cancel the request.
 */
export function streamQuery(
  userInput: string,
  history: ConversationMessage[],
  onStep: (step: QueryStep) => void,
  onSql: (sql: string) => void,
  onResult: (result: QueryResult & { _trace?: TraceStep[] }) => void,
  onError: (msg: string) => void,
  onClarification?: (question: string, options: string[], context?: string, multiSelect?: boolean) => void,
  onTrace?: (step: TraceStep) => void,
  onSqlReady?: (data: { sql: string; explanation: string; validation_passed: boolean; validation_errors: string[] }) => void,
  onSqlCandidates?: (candidates: Array<{ id: string; interpretation: string; sql: string; explanation: string }>) => void,
  onKycAutoAnswer?: (data: { question: string; auto_answer: string; source: string }) => void,
  onSessionMatch?: (data: SessionMatchEvent) => void,
  previousSqlContext?: { sql: string; explanation: string } | null,
): AbortController {
  const controller = new AbortController()

  ;(async () => {
    try {
      const body: Record<string, unknown> = { user_input: userInput, conversation_history: history }
      if (previousSqlContext) body.previous_sql_context = previousSqlContext
      const response = await fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      })

      if (!response.ok) {
        let msg = `Request failed (${response.status})`
        try {
          const body = await response.json()
          msg = (body as { detail?: string; message?: string }).detail ?? msg
        } catch {
          // ignore
        }
        onError(msg)
        return
      }

      if (!response.body) {
        onError('No response body from server')
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

        // Extract complete SSE blocks (delimited by \n\n)
        let boundary = buffer.indexOf('\n\n')
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)

          const event = parseSSEBlock(block)
          if (event) {
            switch (event.type) {
              case 'step':
                onStep((event.data.step as QueryStep) ?? 'enriching')
                break
              case 'sql':
                onSql((event.data.sql as string) ?? '')
                break
              case 'result':
                onResult(event.data as unknown as QueryResult)
                break
              case 'clarification':
                onClarification?.(
                  (event.data.question as string) ?? '',
                  (event.data.options as string[]) ?? [],
                  (event.data.context as string | undefined) ?? undefined,
                  (event.data.multi_select as boolean | undefined) ?? false,
                )
                break
              case 'trace':
                onTrace?.(event.data as unknown as TraceStep)
                break
              case 'sql_ready':
                onSqlReady?.(event.data as { sql: string; explanation: string; validation_passed: boolean; validation_errors: string[] })
                break
              case 'sql_candidates':
                onSqlCandidates?.(((event.data as { candidates?: Array<{ id: string; interpretation: string; sql: string; explanation: string }> }).candidates) ?? [])
                break
              case 'kyc_auto_answer':
                onKycAutoAnswer?.(event.data as { question: string; auto_answer: string; source: string })
                break
              case 'session_match':
                onSessionMatch?.(event.data as unknown as SessionMatchEvent)
                break
              case 'error':
                onError((event.data.message as string) ?? 'Unknown error')
                break
            }
          }

          boundary = buffer.indexOf('\n\n')
        }
      }

      // Flush any remaining buffered event (stream closed without trailing \n\n)
      const remaining = buffer.replace(/\r\n/g, '\n').trim()
      if (remaining) {
        const event = parseSSEBlock(remaining)
        if (event) {
          if (event.type === 'result') onResult(event.data as unknown as QueryResult)
          else if (event.type === 'clarification')
            onClarification?.(
              (event.data.question as string) ?? '',
              (event.data.options as string[]) ?? [],
              (event.data.context as string | undefined) ?? undefined,
              (event.data.multi_select as boolean | undefined) ?? false,
            )
          else if (event.type === 'error') onError((event.data.message as string) ?? 'Unknown error')
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        onError((err as Error).message ?? 'Streaming error')
      }
    }
  })()

  return controller
}

/**
 * Execute a selected SQL candidate via SSE (POST + fetch + ReadableStream).
 * Called after user picks a candidate from SqlCandidatesPicker.
 * The backend validates, optimizes, and returns sql_ready or result events.
 * Returns an AbortController — call .abort() to cancel the request.
 */
export function executeCandidateSql(
  sql: string,
  explanation: string,
  userInput: string,
  history: ConversationMessage[],
  onStep: (step: QueryStep) => void,
  onSqlReady: (data: { sql: string; explanation: string; validation_passed: boolean; validation_errors: string[] }) => void,
  onError: (msg: string) => void,
  onTrace?: (step: TraceStep) => void,
): AbortController {
  const controller = new AbortController()

  ;(async () => {
    try {
      const response = await fetch('/api/query/execute-candidate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql, explanation, user_input: userInput, conversation_history: history }),
        signal: controller.signal,
      })

      if (!response.ok) {
        let msg = `Request failed (${response.status})`
        try {
          const body = await response.json()
          msg = (body as { detail?: string; message?: string }).detail ?? msg
        } catch {
          // ignore
        }
        onError(msg)
        return
      }

      if (!response.body) {
        onError('No response body from server')
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

        let boundary = buffer.indexOf('\n\n')
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)

          const event = parseSSEBlock(block)
          if (event) {
            switch (event.type) {
              case 'step':
                onStep((event.data.step as QueryStep) ?? 'validating')
                break
              case 'sql_ready':
                onSqlReady(event.data as { sql: string; explanation: string; validation_passed: boolean; validation_errors: string[] })
                break
              case 'trace':
                onTrace?.(event.data as unknown as TraceStep)
                break
              case 'error':
                onError((event.data.message as string) ?? 'Unknown error')
                break
            }
          }

          boundary = buffer.indexOf('\n\n')
        }
      }

      // Flush remaining
      const remaining = buffer.replace(/\r\n/g, '\n').trim()
      if (remaining) {
        const event = parseSSEBlock(remaining)
        if (event) {
          if (event.type === 'sql_ready') onSqlReady(event.data as { sql: string; explanation: string; validation_passed: boolean; validation_errors: string[] })
          else if (event.type === 'error') onError((event.data.message as string) ?? 'Unknown error')
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        onError((err as Error).message ?? 'Streaming error')
      }
    }
  })()

  return controller
}

/**
 * Execute a confirmed SQL query via SSE (POST + fetch + ReadableStream).
 * Called after user clicks "Run Query" on an sql_preview card.
 * Returns an AbortController — call .abort() to cancel the request.
 */
export function executeConfirmedSql(
  sql: string,
  userInput: string,
  history: ConversationMessage[],
  onStep: (step: QueryStep) => void,
  onResult: (result: QueryResult & { _trace?: TraceStep[] }) => void,
  onError: (msg: string) => void,
  onTrace?: (step: TraceStep) => void,
): AbortController {
  const controller = new AbortController()

  ;(async () => {
    try {
      const response = await fetch('/api/query/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql, user_input: userInput, conversation_history: history }),
        signal: controller.signal,
      })

      if (!response.ok) {
        let msg = `Request failed (${response.status})`
        try {
          const body = await response.json()
          msg = (body as { detail?: string; message?: string }).detail ?? msg
        } catch {
          // ignore
        }
        onError(msg)
        return
      }

      if (!response.body) {
        onError('No response body from server')
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')

        let boundary = buffer.indexOf('\n\n')
        while (boundary !== -1) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)

          const event = parseSSEBlock(block)
          if (event) {
            switch (event.type) {
              case 'step':
                onStep((event.data.step as QueryStep) ?? 'executing')
                break
              case 'result':
                onResult(event.data as unknown as QueryResult)
                break
              case 'trace':
                onTrace?.(event.data as unknown as TraceStep)
                break
              case 'error':
                onError((event.data.message as string) ?? 'Unknown error')
                break
            }
          }

          boundary = buffer.indexOf('\n\n')
        }
      }

      // Flush remaining
      const remaining = buffer.replace(/\r\n/g, '\n').trim()
      if (remaining) {
        const event = parseSSEBlock(remaining)
        if (event) {
          if (event.type === 'result') onResult(event.data as unknown as QueryResult)
          else if (event.type === 'error') onError((event.data.message as string) ?? 'Unknown error')
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        onError((err as Error).message ?? 'Streaming error')
      }
    }
  })()

  return controller
}
