/**
 * RFC7807 problem documents, as the client sees them.
 *
 * The server's `ProblemCode` is a closed set precisely so a client can branch on it. This module
 * keeps that set in one place and refuses to guess: an unrecognised code is reported as unknown
 * rather than folded into the nearest familiar one, because folding it would make a new server
 * condition silently behave like an old one.
 *
 * Nothing here reads `detail` to make a decision. `detail` is prose written for a person, it is
 * allowed to change, and a client that matched on it would break on a wording fix.
 */

/** The server's closed set, mirrored. Kept as a const array so it can also be iterated in tests. */
export const PROBLEM_CODES = [
  'INVALID_INPUT',
  'UNEXPECTED_FIELD',
  'MALFORMED_DIGEST',
  'AMBIGUOUS_TIMESTAMP',
  'NOT_AUTHENTICATED',
  'CSRF_REQUIRED',
  'PERMISSION_DENIED',
  'RESOURCE_NOT_FOUND',
  'STALE_REVISION',
  'IF_MATCH_REQUIRED',
  'IDEMPOTENCY_KEY_REUSED',
  'CONFLICT',
  'UNSUPPORTED_CAPABILITY',
  'QUOTA_EXHAUSTED',
  'DEPENDENCY_UNAVAILABLE',
] as const

export type ProblemCode = (typeof PROBLEM_CODES)[number]

export interface Problem {
  readonly code: ProblemCode | 'UNRECOGNISED'
  readonly title: string
  readonly detail: string
  readonly status: number
  readonly requestId: string | null
}

const isProblemCode = (value: unknown): value is ProblemCode =>
  typeof value === 'string' && (PROBLEM_CODES as readonly string[]).includes(value)

/**
 * Build a `Problem` from a response body that may be anything at all.
 *
 * A proxy, a load balancer or a crash can produce an HTML error page with a JSON content type, and
 * a client that assumed the shape would throw inside its own error handler — turning a server error
 * into an unhandled exception with no message for the user.
 */
export const parseProblem = (status: number, body: unknown): Problem => {
  const record = (typeof body === 'object' && body !== null ? body : {}) as Record<string, unknown>
  const code = record['code']
  const detail = record['detail']
  const title = record['title']
  const requestId = record['requestId']
  return {
    code: isProblemCode(code) ? code : 'UNRECOGNISED',
    title: typeof title === 'string' ? title : 'Request failed',
    detail:
      typeof detail === 'string' && detail.length > 0
        ? detail
        : 'The server refused the request and did not explain why in a form this client recognises.',
    status,
    requestId: typeof requestId === 'string' ? requestId : null,
  }
}
