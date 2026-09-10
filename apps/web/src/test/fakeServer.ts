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

import type {
  Environment,
  JourneyVersion,
  Project,
  Run,
  Runner,
  SealedManifest,
} from '../api/resources'

export interface SessionResponse {
  readonly userId: string
  readonly email: string
  readonly workspaces: readonly { workspaceId: string; name: string; role: string }[]
}

/**
 * What the workspace endpoints answer. Every field defaults to an empty, successful answer.
 *
 * Typed against the resource contracts the screens consume, not `Record<string, unknown>`. A loose
 * type here lets a test build a fixture that is missing a field the screen reads, which then fails
 * for a reason that has nothing to do with what the test was about.
 */
export interface WorkspaceData {
  projects: Project[]
  environments: Environment[]
  journeyVersions: JourneyVersion[]
  runners: Runner[]
  runs: Run[]
  sealedManifests: SealedManifest[]
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
  /** The workspace data the read endpoints serve. Mutated in place by tests. */
  readonly data: WorkspaceData
  /** Refuse the next write to this path fragment with the given status and code. */
  refuseWrite: (fragment: string, status: number, code: string, detail: string) => void
  /** The parsed bodies of every write, with the idempotency key each carried. */
  readonly bodies: {
    method: string
    url: string
    body: unknown
    idempotencyKey: string | undefined
  }[]
  /** What `POST /v1/sessions` answers. */
  setSignInOutcome: (outcome: 'succeeds' | 'refused' | 'no-provider') => void
  /** Make `DELETE /v1/session` fail without revoking anything, as an unreachable server would. */
  setSignOutFails: (fails: boolean) => void
  /**
   * Serve the runner inventory one row per page.
   *
   * `endless` never stops offering a cursor, which is how a client that follows them without a
   * bound is made to reveal that it has one.
   */
  setRunnerPaging: (mode: 'single' | 'paged' | 'endless') => void
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
  let runnerPaging: 'single' | 'paged' | 'endless' = 'single'
  const data: WorkspaceData = {
    projects: [],
    environments: [],
    journeyVersions: [],
    runners: [],
    runs: [],
    sealedManifests: [],
  }
  const refusals = new Map<string, { status: number; code: string; detail: string }>()
  const bodies: {
    method: string
    url: string
    body: unknown
    idempotencyKey: string | undefined
  }[] = []
  let gate: Promise<void> | null = null
  let open: (() => void) | null = null
  const calls: string[] = []

  const json = (body: unknown, status = 200): Response =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'content-type': 'application/json' },
    })

  const server: FakeServer = {
    calls,
    data,
    bodies,
    refuseWrite: (fragment, status, code, detail) => {
      refusals.set(fragment, { status, code, detail })
    },
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
    setRunnerPaging: (mode) => {
      runnerPaging = mode
    },
    fetch: (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = typeof input === 'string' ? input : input.toString()
      const method = init?.method ?? 'GET'
      calls.push(`${method} ${url}`)
      if (init?.body !== undefined && init.body !== null) {
        const headers = new Headers(init.headers)
        bodies.push({
          method,
          url,
          body: JSON.parse(String(init.body)) as unknown,
          idempotencyKey: headers.get('idempotency-key') ?? undefined,
        })
      }

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

      // ---- workspace endpoints -------------------------------------------------------------
      const refusal = [...refusals.entries()].find(([fragment]) => url.includes(fragment))
      if (refusal !== undefined && method !== 'GET') {
        const [fragment, answer] = refusal
        refusals.delete(fragment)
        return problem(answer.status, answer.code, answer.detail, 'Refused')
      }

      if (url.endsWith('/journey-capabilities')) {
        return json({
          allowedActions: ['NEXT', 'ACTIVATE', 'TYPE_TEXT', 'KEY_CHORD', 'READ_CURRENT'],
          allowedKeyChordsByPlatform: {
            darwin: ['TAB', 'ENTER', 'CTRL+OPT+RIGHT'],
            win32: ['TAB', 'ENTER', 'DOWN'],
          },
          allowedEffects: ['FIXTURE_SUBMIT', 'FIXTURE_RESET'],
          assertionKinds: ['TASK_COMPLETION', 'REQUIRED_ANNOUNCEMENT'],
          unknownReasons: ['READER_UNAVAILABLE', 'OBSERVER_UNREACHABLE'],
          maxActions: 500,
          maxWallTimeSeconds: 1800,
          effectsMeaning: 'Effects are confined to owned test fixtures.',
        })
      }

      if (url.includes('/runners') && method === 'GET') {
        if (runnerPaging !== 'single') {
          const cursor = new URL(url, 'http://test.invalid').searchParams.get('after')
          const at = cursor === null ? 0 : Number(cursor)
          const item = data.runners[at]
          const more = runnerPaging === 'endless' || at + 1 < data.runners.length
          return json({
            items: item === undefined ? [] : [item],
            nextCursor: more ? String(at + 1) : null,
            readinessMeaning: 'READY means this runner passed a preflight.',
          })
        }
        return json({
          items: data.runners,
          nextCursor: null,
          readinessMeaning:
            'READY means this runner passed a preflight that read something back from a real ' +
            'desktop. It is not inferred from the runner process being reachable, and no status ' +
            'here is evidence that a journey will pass.',
        })
      }

      if (url.includes('/runs') && method === 'GET') {
        return json({ items: data.runs, nextCursor: null })
      }

      if (url.endsWith('/runs') && method === 'POST') {
        const runId = `run-${data.runs.length + 1}`
        data.runs.push({
          runId,
          status: 'QUEUED',
          outcome: 'NOT_EVALUATED',
          revision: 1,
          leaseEpoch: 0,
          manifestDigest: 'f'.repeat(64),
          cancellationRequestedAt: null,
          stopAcknowledgedAt: null,
          ambiguityReason: null,
          quarantined: false,
          retryOf: null,
        })
        return json({ runId, status: 'QUEUED', outcome: 'NOT_EVALUATED' }, 202)
      }

      if (url.includes('/members') && method === 'GET') {
        return json({
          items: [{ userId: 'u-1', email: 'engineer@example.test', role: 'MAINTAINER' }],
        })
      }

      if (url.includes('/projects') && method === 'GET' && !url.includes('/projects/')) {
        return json({ items: data.projects, nextCursor: null })
      }

      if (url.endsWith('/projects') && method === 'POST') {
        const parsed = JSON.parse(String(init?.body ?? '{}')) as { name?: string }
        const projectId = `project-${data.projects.length + 1}`
        data.projects.push({
          projectId,
          name: String(parsed.name ?? ''),
          repositoryUrl: null,
          createdAt: '2026-09-10T00:00:00Z',
        })
        return json({ projectId }, 201)
      }

      if (url.includes('/environments') && method === 'GET') {
        return json({ items: data.environments, nextCursor: null })
      }
      if (url.includes('/manifests') && method === 'GET') {
        return json({ items: data.sealedManifests, nextCursor: null, meaning: 'A run is requested against one of these digests.' })
      }
      if (url.includes('/environments') && method === 'POST') {
        return json({ environmentId: 'env-1', configDigest: 'a'.repeat(64) }, 201)
      }

      if (url.includes('/journeys') && method === 'GET' && url.includes('/projects/') && !url.includes('/journeys/')) {
        return json({ items: data.journeyVersions, nextCursor: null })
      }
      if (url.includes('/journeys/') && url.endsWith('/policy')) {
        return json({
          navigatorPolicy: { taskSummary: 'Submit the form', fixtureValues: { name: 'Rowan' } },
          navigatorPolicyDigest: 'b'.repeat(64),
        })
      }
      if (url.includes('/journeys/') && method === 'GET') {
        const id = url.split('/journeys/')[1] ?? ''
        const found = data.journeyVersions.find((v) => v['journeyVersionId'] === id)
        if (found === undefined) {
          return problem(404, 'RESOURCE_NOT_FOUND', 'no such resource', 'Not found')
        }
        return json(found)
      }
      if (url.endsWith('/journeys') && method === 'POST') {
        return json(
          {
            journeyVersionId: 'journey-1',
            journeyDigest: 'c'.repeat(64),
            assertionSetDigest: 'd'.repeat(64),
            fixtureDigest: 'e'.repeat(64),
            navigatorPolicyDigest: 'f'.repeat(64),
            reviewerSummary: {},
            supersedes: null,
            meaning: 'This version is frozen and authorizes nothing to run.',
          },
          201,
        )
      }

      if (url.includes('/projects/') && method === 'GET') {
        const id = url.split('/projects/')[1] ?? ''
        const found = data.projects.find((project) => project.projectId === id)
        if (found === undefined) {
          return problem(404, 'RESOURCE_NOT_FOUND', 'no such resource', 'Not found')
        }
        return json(found)
      }

      return problem(404, 'RESOURCE_NOT_FOUND', 'no such resource', 'Not found')
    }) as unknown as typeof fetch,
  }
  return server
}
