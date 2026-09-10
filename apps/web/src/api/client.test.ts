/**
 * What the client does with each kind of answer.
 *
 * Every test here corresponds to a way the UI could mislead someone. The useful ones are the
 * negative cases: a failed request becoming an empty list, a response arriving after sign-out, a
 * mutation being retried, and 202 being read as completion.
 */

import { describe, expect, it, vi } from 'vitest'

import { ApiClient, CSRF_HEADER, readCookie } from './client'

const jsonResponse = (status: number, body: unknown): Response =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })

const clientWith = (
  fetchImpl: typeof fetch,
  cookies = '',
): ApiClient => new ApiClient({ fetchImpl, cookieSource: () => cookies })

/**
 * A `fetch` mock whose recorded calls keep their argument types.
 *
 * `vi.fn(async () => …)` infers a zero-argument function, so `mock.calls[0][1]` is typed as never
 * and every assertion about the request options becomes unwriteable. Declaring the signature keeps
 * the assertions about headers, credentials and redirect handling type-checked rather than cast.
 */
const fetchMock = (
  implementation: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
) => vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(implementation)

/** A promise plus its resolver, so a test can hold a response open and release it deliberately. */
const deferred = (): { readonly wait: Promise<void>; readonly release: () => void } => {
  let release = (): void => undefined
  const wait = new Promise<void>((resolve) => {
    release = resolve
  })
  return { wait, release: () => release() }
}

describe('readCookie', () => {
  it('distinguishes an absent cookie from an empty one', () => {
    expect(readCookie('a', 'b=1')).toBeNull()
    expect(readCookie('a', 'a=; b=1')).toBe('')
  })

  it('does not match a cookie whose name merely ends with the one asked for', () => {
    // `xsrf_csrf` must not satisfy a request for `csrf`. A suffix match here would let a cookie set
    // by an unrelated application supply this one's CSRF token.
    expect(readCookie('csrf', 'xsrf_csrf=value')).toBeNull()
  })
})

describe('outcomes', () => {
  it('reports 200 as ok with the parsed body', async () => {
    const client = clientWith(vi.fn(async () => jsonResponse(200, { a: 1 })) as unknown as typeof fetch)
    const outcome = await client.request<{ a: number }>('/v1/thing')
    expect(outcome).toEqual({ kind: 'ok', value: { a: 1 }, status: 200 })
  })

  it('reports 202 as accepted rather than ok', async () => {
    // The specific failure this prevents: a queued run rendered as a finished one. UI-UX section 5
    // forbids a provisional success badge, and a caller treating every 2xx alike would produce one.
    const client = clientWith(
      vi.fn(async () => jsonResponse(202, { status: 'QUEUED' })) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/runs', { method: 'POST' })
    expect(outcome.kind).toBe('accepted')
  })

  it('reports 204 as ok with no body', async () => {
    const client = clientWith(vi.fn(async () => jsonResponse(204, null)) as unknown as typeof fetch)
    const outcome = await client.request('/v1/session', { method: 'DELETE' })
    expect(outcome.kind).toBe('ok')
  })

  it('turns a network failure into offline, not into an empty result', async () => {
    const client = clientWith(
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects')
    expect(outcome.kind).toBe('offline')
    // There is no value to read. A caller cannot accidentally render this as "no projects".
    expect('value' in outcome).toBe(false)
  })

  it('turns a refusal into a problem, not into an empty result', async () => {
    const client = clientWith(
      vi.fn(async () =>
        jsonResponse(403, { code: 'PERMISSION_DENIED', detail: 'no', title: 'Denied' }),
      ) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects')
    expect(outcome.kind).toBe('problem')
    expect('value' in outcome).toBe(false)
  })

  it('reports an unrecognised problem code as unrecognised rather than guessing', async () => {
    const client = clientWith(
      vi.fn(async () => jsonResponse(418, { code: 'SOMETHING_NEW' })) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects')
    expect(outcome.kind === 'problem' && outcome.problem.code).toBe('UNRECOGNISED')
  })

  it('survives an error body that is not JSON at all', async () => {
    // A proxy's HTML error page with a JSON content type. The client must not throw inside its own
    // error path, which would turn a server error into an unhandled exception and no message.
    const client = clientWith(
      vi.fn(
        async () =>
          new Response('<html>502</html>', {
            status: 502,
            headers: { 'content-type': 'application/json' },
          }),
      ) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects')
    expect(outcome.kind).toBe('problem')
    expect(outcome.kind === 'problem' && outcome.problem.status).toBe(502)
  })
})

describe('cancellation', () => {
  it('reports an aborted request as cancelled, not as a failure', async () => {
    const controller = new AbortController()
    const client = clientWith(
      vi.fn(async () => {
        controller.abort()
        throw new DOMException('aborted', 'AbortError')
      }) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects', { signal: controller.signal })
    // Reported as a failure this would fill the screen with problem notices every time someone
    // navigated away from a loading page.
    expect(outcome.kind).toBe('cancelled')
  })

  it('passes the signal through to fetch so the request is actually abandoned', async () => {
    const fetchImpl = fetchMock(async () => jsonResponse(200, {}))
    const client = clientWith(fetchImpl as unknown as typeof fetch)
    const controller = new AbortController()
    await client.request('/v1/projects', { signal: controller.signal })
    expect(fetchImpl.mock.calls[0]?.[1]?.signal).toBe(controller.signal)
  })
})

describe('the session epoch', () => {
  it('discards a response that arrives after the session ended', async () => {
    const gate = deferred()
    const client = clientWith(
      fetchMock(async () => {
        await gate.wait
        return jsonResponse(200, { secret: 'previous tenant' })
      }) as unknown as typeof fetch,
    )

    const pending = client.request<{ secret: string }>('/v1/projects')
    client.endSession()
    gate.release()
    const outcome = await pending

    // Rendered, this would be the previous tenant's data painted onto the signed-out screen. The
    // prompt names it: do not show cached tenant data after logout.
    expect(outcome.kind).toBe('stale')
    expect('value' in outcome).toBe(false)
  })

  it('discards a response that arrives after a workspace switch', async () => {
    const gate = deferred()
    const client = clientWith(
      fetchMock(async () => {
        await gate.wait
        return jsonResponse(200, { items: ['workspace A row'] })
      }) as unknown as typeof fetch,
    )
    const pending = client.request('/v1/projects')
    client.invalidateInFlight()
    gate.release()
    expect((await pending).kind).toBe('stale')
  })

  it('ends the session itself on a 401, so every other request in flight is discarded too', async () => {
    const client = clientWith(
      vi.fn(async () => jsonResponse(401, { code: 'NOT_AUTHENTICATED' })) as unknown as typeof fetch,
    )
    const before = client.epoch
    const outcome = await client.request('/v1/projects')
    expect(outcome.kind).toBe('unauthenticated')
    expect(client.epoch).toBe(before + 1)
  })
})

describe('the window between the headers and the body', () => {
  /** A response whose headers have arrived and whose body never finishes. */
  const responseWithBody = (body: () => Promise<string>): Response =>
    ({
      status: 200,
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      text: body,
    }) as unknown as Response

  it('reports an abort during the body read as cancelled, not as an empty success', async () => {
    // `Response.text()` rejects with an AbortError when the signal fires after the headers arrive.
    // Swallowing that produced `{ kind: 'ok', value: null }` — a successful-looking response
    // carrying nothing, which is the empty result this whole type exists to make impossible.
    const controller = new AbortController()
    const client = clientWith(
      fetchMock(async () =>
        responseWithBody(async () => {
          controller.abort()
          throw new DOMException('aborted', 'AbortError')
        }),
      ) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects', { signal: controller.signal })
    expect(outcome.kind).toBe('cancelled')
  })

  it('reports a session that ended during the body read as stale', async () => {
    let client: ApiClient
    client = clientWith(
      fetchMock(async () =>
        responseWithBody(async () => {
          client.endSession()
          return JSON.stringify({ secret: 'previous tenant' })
        }),
      ) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects')
    expect(outcome.kind).toBe('stale')
    expect('value' in outcome).toBe(false)
  })

  it('reports a body that never finished transferring as offline', async () => {
    const client = clientWith(
      fetchMock(async () =>
        responseWithBody(async () => {
          throw new TypeError('network error while reading body')
        }),
      ) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects')
    // The headers arrived, so this is the one case that reaches `response.ok` and still has no
    // result. It is a network failure, not an empty document.
    expect(outcome.kind).toBe('offline')
  })

  it('still treats a body that arrived but is not JSON as a document, not a transfer failure', async () => {
    const client = clientWith(
      fetchMock(
        async () =>
          new Response('<html>502</html>', {
            status: 502,
            headers: { 'content-type': 'application/json' },
          }),
      ) as unknown as typeof fetch,
    )
    const outcome = await client.request('/v1/projects')
    expect(outcome.kind).toBe('problem')
  })
})

describe('request construction', () => {
  it('sends the CSRF token from the cookie on a mutation', async () => {
    const fetchImpl = fetchMock(async () => jsonResponse(200, {}))
    const client = clientWith(fetchImpl as unknown as typeof fetch, 'accessforge_csrf=tok-123')
    await client.request('/v1/projects', { method: 'POST', body: {} })
    const headers = fetchImpl.mock.calls[0]?.[1]?.headers as Headers
    expect(headers.get(CSRF_HEADER)).toBe('tok-123')
  })

  it('does not send a CSRF token on a read', async () => {
    const fetchImpl = fetchMock(async () => jsonResponse(200, {}))
    const client = clientWith(fetchImpl as unknown as typeof fetch, 'accessforge_csrf=tok-123')
    await client.request('/v1/projects')
    const headers = fetchImpl.mock.calls[0]?.[1]?.headers as Headers
    expect(headers.get(CSRF_HEADER)).toBeNull()
  })

  it('sends If-Match when the caller supplies a revision', async () => {
    const fetchImpl = fetchMock(async () => jsonResponse(200, {}))
    const client = clientWith(fetchImpl as unknown as typeof fetch)
    await client.request('/v1/thing', { method: 'PATCH', body: {}, ifMatch: 7 })
    const headers = fetchImpl.mock.calls[0]?.[1]?.headers as Headers
    expect(headers.get('if-match')).toBe('7')
  })

  it('uses same-origin credentials, never cross-origin', async () => {
    const fetchImpl = fetchMock(async () => jsonResponse(200, {}))
    const client = clientWith(fetchImpl as unknown as typeof fetch)
    await client.request('/v1/projects')
    // `include` would send the session cookie cross-origin, which is how a misconfigured deployment
    // leaks a session to whatever the base URL happens to point at.
    expect(fetchImpl.mock.calls[0]?.[1]?.credentials).toBe('same-origin')
  })

  it('refuses to follow a redirect', async () => {
    const fetchImpl = fetchMock(async () => jsonResponse(200, {}))
    const client = clientWith(fetchImpl as unknown as typeof fetch)
    await client.request('/v1/projects')
    // A followed redirect would send the session cookie to wherever the redirect pointed.
    expect(fetchImpl.mock.calls[0]?.[1]?.redirect).toBe('error')
  })
})

describe('retries', () => {
  it('never retries anything, including a failed mutation', async () => {
    const fetchImpl = fetchMock(async () => jsonResponse(503, { code: 'DEPENDENCY_UNAVAILABLE' }))
    const client = clientWith(fetchImpl as unknown as typeof fetch)
    await client.request('/v1/patches/p1/approvals', { method: 'POST', body: {} })
    // A retried approval is a second approval. The count is the assertion.
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })

  it('never retries a read either, leaving that to the caller', async () => {
    const fetchImpl = fetchMock(async () => {
      throw new TypeError('Failed to fetch')
    })
    const client = clientWith(fetchImpl as unknown as typeof fetch)
    await client.request('/v1/projects')
    expect(fetchImpl).toHaveBeenCalledTimes(1)
  })
})
