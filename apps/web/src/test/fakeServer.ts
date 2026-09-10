/**
 * A controllable stand-in for the API, for the shell's own unit tests.
 *
 * It is a `fetch` implementation, not a mocked `ApiClient`: the client's own handling of status
 * codes, problem documents, the request epoch and cancellation is part of what these tests exercise,
 * and stubbing the client would skip all of it.
 *
 * What it cannot do is prove the shell works against the real server. That proof is a separate
 * integration test against the live ASGI application — see tests/integration/test_session_routes.py
 * and the handoff — because a fake that agrees with my beliefs about the API proves only that I am
 * self-consistent.
 */

export interface SessionResponse {
  readonly userId: string
  readonly email: string
  readonly workspaces: readonly { workspaceId: string; name: string; role: string }[]
}

export interface FakeServer {
  readonly fetch: typeof fetch
  /** Answer `GET /v1/session` with this. */
  setSession: (response: SessionResponse | null) => void
  /** Make every request fail at the network level. */
  setOffline: (offline: boolean) => void
  /** Hold `GET /v1/session` open until `releaseSession` is called. */
  holdSession: () => void
  releaseSession: () => void
  /** What `POST /v1/sessions` answers. */
  setSignInOutcome: (outcome: 'succeeds' | 'refused' | 'no-provider') => void
  /** Make `DELETE /v1/session` fail without revoking anything, as an unreachable server would. */
  setSignOutFails: (fails: boolean) => void
  readonly calls: readonly string[]
}

const problem = (status: number, code: string, detail: string, title: string): Response =>
  new Response(JSON.stringify({ status, code, detail, title, requestId: 'req-test' }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  })

export const createFakeServer = (initial: SessionResponse | null = null): FakeServer => {
  // `configured` is what a successful sign-in produces; `session` is what is live right now. They
  // are separate so a test can revoke a membership and then sign in again to observe the new answer
  // -- with one variable, signing in would restore the state the test had just changed.
  let configured = initial
  let session = initial
  let offline = false
  let signInOutcome: 'succeeds' | 'refused' | 'no-provider' = 'succeeds'
  let signOutFails = false
  let gate: Promise<void> | null = null
  let open: (() => void) | null = null
  const calls: string[] = []

  const server: FakeServer = {
    calls,
    setSession: (response) => {
      configured = response
      session = response
    },
    setOffline: (value) => {
      offline = value
    },
    holdSession: () => {
      gate = new Promise<void>((resolve) => {
        open = resolve
      })
    },
    releaseSession: () => {
      open?.()
      open = null
      gate = null
    },
    setSignInOutcome: (outcome) => {
      signInOutcome = outcome
    },
    setSignOutFails: (fails) => {
      signOutFails = fails
    },
    fetch: (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = typeof input === 'string' ? input : input.toString()
      const method = init?.method ?? 'GET'
      calls.push(`${method} ${url}`)

      if (offline) throw new TypeError('Failed to fetch')

      if (url.endsWith('/v1/session') && method === 'GET') {
        if (gate !== null) await gate
        if (session === null) {
          return problem(401, 'NOT_AUTHENTICATED', 'session is not valid', 'Not authenticated')
        }
        return new Response(JSON.stringify(session), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }

      if (url.endsWith('/v1/session') && method === 'DELETE') {
        if (signOutFails) {
          // The session deliberately stays live: that is the whole point of the case. The browser
          // was cleared and the server never revoked anything.
          return problem(503, 'DEPENDENCY_UNAVAILABLE', 'the store is unavailable', 'Unavailable')
        }
        session = null
        return new Response(null, { status: 204 })
      }

      if (url.endsWith('/v1/sessions') && method === 'POST') {
        if (signInOutcome === 'no-provider') {
          return problem(
            503,
            'DEPENDENCY_UNAVAILABLE',
            'no identity provider is configured, so this deployment cannot sign anyone in.',
            'Dependency unavailable',
          )
        }
        if (signInOutcome === 'refused') {
          return problem(401, 'NOT_AUTHENTICATED', 'sign-in was refused.', 'Not authenticated')
        }
        session = configured
        return new Response(JSON.stringify({ userId: 'u1', expiresAt: 'later' }), {
          status: 201,
          headers: { 'content-type': 'application/json' },
        })
      }

      return problem(404, 'RESOURCE_NOT_FOUND', 'no such resource', 'Not found')
    }) as unknown as typeof fetch,
  }
  return server
}
