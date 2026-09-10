/**
 * The single path by which this application talks to its server.
 *
 * Seven decisions live here, and each one is a way the UI could otherwise tell a comfortable lie.
 *
 * **A failed request is never an empty result.** `request` returns a discriminated union. There is
 * no `T | null`, because `null` is exactly what a caller renders as "nothing here yet" — and
 * "nothing here yet" is the wrong thing to show a person whose request was refused.
 *
 * **Mutations are never retried implicitly.** A retried approval is a second approval. The client
 * retries nothing at all, and the one place a retry would be safe — a read — is left to the caller,
 * who knows whether the answer is still wanted.
 *
 * **202 is not completion.** `accepted` is its own outcome. A caller that treated 2xx uniformly
 * would report a queued run as a finished one, which is the specific failure UI-UX section 5 names.
 *
 * **Every request carries a session epoch.** The epoch increments on sign-out and on an
 * authentication failure. A response whose epoch is stale is discarded rather than rendered, so a
 * slow response cannot paint the previous tenant's data over the screen of the next one.
 *
 * **Cancellation is a first-class outcome, not an error.** An aborted request produces `cancelled`,
 * which callers ignore. Reporting it as a failure would fill the screen with problem notices every
 * time a person navigated away from a loading page.
 *
 * **Offline is distinguished from server failure.** They ask the reader to do different things.
 *
 * **The CSRF token comes from a cookie, and is required for mutations.** Not from `localStorage`:
 * a token there survives sign-out, and SECURITY-PRIVACY section 3 is explicit that long-lived
 * tokens do not belong in front-end storage.
 */

import { type Problem, parseProblem } from './problem'

export type ApiOutcome<T> =
  | { readonly kind: 'ok'; readonly value: T; readonly status: number }
  /** 202. The server accepted the request; nothing has finished. */
  | { readonly kind: 'accepted'; readonly value: T }
  /** The request was superseded or the caller navigated away. Callers render nothing. */
  | { readonly kind: 'cancelled' }
  /** The browser could not reach the server at all. */
  | { readonly kind: 'offline' }
  /** The session is gone: expired, revoked, or signed out in another tab. */
  | { readonly kind: 'unauthenticated'; readonly problem: Problem }
  /** A response arrived for a session that has since ended. Discarded, never rendered. */
  | { readonly kind: 'stale' }
  | { readonly kind: 'problem'; readonly problem: Problem }

export interface RequestOptions {
  readonly method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  readonly body?: unknown
  readonly signal?: AbortSignal
  /** The revision the caller last read. Required by the server for revisioned mutations. */
  readonly ifMatch?: number
  readonly idempotencyKey?: string
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

export const CSRF_COOKIE = 'accessforge_csrf'
export const CSRF_HEADER = 'x-csrf-token'

/** Read one cookie. Returns null rather than an empty string, so "absent" and "empty" differ. */
export const readCookie = (name: string, cookieString: string): string | null => {
  for (const part of cookieString.split(';')) {
    const [key, ...rest] = part.trim().split('=')
    if (key === name) return decodeURIComponent(rest.join('='))
  }
  return null
}

export interface ApiClientOptions {
  readonly baseUrl?: string
  readonly fetchImpl?: typeof fetch
  readonly cookieSource?: () => string
}

export class ApiClient {
  readonly #baseUrl: string
  readonly #fetch: typeof fetch
  readonly #cookies: () => string
  /**
   * Incremented whenever the authenticated context ends. Captured at the start of each request and
   * compared when it resolves; a mismatch discards the response.
   */
  #epoch = 0

  constructor(options: ApiClientOptions = {}) {
    this.#baseUrl = options.baseUrl ?? ''
    this.#fetch = options.fetchImpl ?? globalThis.fetch.bind(globalThis)
    this.#cookies = options.cookieSource ?? (() => document.cookie)
  }

  /**
   * Discard every response still in flight.
   *
   * Used wherever the thing those responses would be rendered into no longer exists: a sign-out, or
   * a switch to a different workspace. The responses still arrive — nothing can stop that — but they
   * resolve as `stale` and callers render nothing, so a listing requested a moment before the switch
   * cannot paint the previous tenant's rows into the next tenant's screen.
   */
  invalidateInFlight(): void {
    this.#epoch += 1
  }

  /**
   * End the current authenticated context.
   *
   * The same mechanism, named for the case that matters most: a listing requested a moment before
   * sign-out must not arrive afterwards and be rendered into the signed-out shell.
   */
  endSession(): void {
    this.invalidateInFlight()
  }

  get epoch(): number {
    return this.#epoch
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<ApiOutcome<T>> {
    const method = options.method ?? 'GET'
    const epochAtStart = this.#epoch
    const headers = new Headers({ accept: 'application/json' })

    if (options.body !== undefined) headers.set('content-type', 'application/json')
    if (options.ifMatch !== undefined) headers.set('if-match', String(options.ifMatch))
    if (options.idempotencyKey !== undefined) {
      headers.set('idempotency-key', options.idempotencyKey)
    }
    if (MUTATING.has(method)) {
      const csrf = readCookie(CSRF_COOKIE, this.#cookies())
      // Sent when present. Absent means the session is already gone, and the server will say so far
      // more authoritatively than a guess made here.
      if (csrf !== null) headers.set(CSRF_HEADER, csrf)
    }

    let response: Response
    try {
      response = await this.#fetch(`${this.#baseUrl}${path}`, {
        method,
        headers,
        // Same-origin, so the session cookie travels and nothing else does. `include` would send
        // credentials cross-origin, which is how a misconfigured deployment leaks a session.
        credentials: 'same-origin',
        redirect: 'error',
        ...(options.body === undefined ? {} : { body: JSON.stringify(options.body) }),
        ...(options.signal === undefined ? {} : { signal: options.signal }),
      })
    } catch (error) {
      if (options.signal?.aborted === true) return { kind: 'cancelled' }
      if (epochAtStart !== this.#epoch) return { kind: 'stale' }
      // A network-level failure. Not reported as a server error: the two ask the reader to do
      // different things, and "check your connection" is useless advice when the server is down.
      void error
      return { kind: 'offline' }
    }

    if (epochAtStart !== this.#epoch) return { kind: 'stale' }

    if (response.status === 204) {
      return { kind: 'ok', value: undefined as T, status: 204 }
    }

    const payload = await this.#readJson(response)

    if (response.ok) {
      if (response.status === 202) return { kind: 'accepted', value: payload as T }
      return { kind: 'ok', value: payload as T, status: response.status }
    }

    const problem = parseProblem(response.status, payload)
    if (problem.code === 'NOT_AUTHENTICATED') {
      // The context is over. Ending it here rather than leaving it to each caller means a single
      // 401 anywhere invalidates every other request in flight.
      this.endSession()
      return { kind: 'unauthenticated', problem }
    }
    return { kind: 'problem', problem }
  }

  async #readJson(response: Response): Promise<unknown> {
    try {
      const text = await response.text()
      return text.length === 0 ? null : (JSON.parse(text) as unknown)
    } catch {
      // An unparseable body from a proxy or a crashed worker. Returning null lets `parseProblem`
      // produce an honest document instead of this throwing inside the error path.
      return null
    }
  }
}
