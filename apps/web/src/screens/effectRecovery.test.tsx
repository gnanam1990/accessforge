import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import { parseEffectRecovery, type EffectDelivery, type EffectRecovery } from '../api/effectRecovery'
import { SessionProvider } from '../session/SessionProvider'
import { EffectRecoverySection } from './EffectRecoverySection'

// Synthetic display/network contracts for CI, not actual-reader or application-effect proof.
const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const at = '2026-09-13T12:00:00Z'
const digest = 'a'.repeat(64)
const item = (n: number): EffectDelivery => ({
  permitId: id(n), actionId: id(n + 100), attemptId: id(200), actionSequence: n, action: 'ACTIVATE',
  actionResult: null, phase: 'UNCONFIRMED', grantedAt: at, expiresAt: '2026-09-13T12:00:05Z',
  consumedAt: at, responseRecordedAt: null, requestDigest: digest, responseDigest: null, responseStatus: null,
  actionResultAt: null, leaseId: id(300), leaseReleasedAt: null, stopAcknowledgedAt: null,
  runnerQuarantined: true, endpointState: null, endpointCleanupConfirmed: null, requiresInvestigation: true,
})
const report = (items: readonly EffectDelivery[], nextCursor: string | null = null, runId = id(500)): EffectRecovery => ({
  runId, observedAt: at, runStatus: 'INTERRUPTED', runOutcome: 'INCONCLUSIVE', runQuarantined: true,
  items, nextCursor, providesRetryAuthority: false, providesResetAuthority: false,
  meaning: 'FORM_TRANSPORT_HISTORY_NOT_EFFECT_PROOF',
})
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'content-type': 'application/json' },
})
function fixture(read: (url: URL) => Response | Promise<Response>) {
  const reads: URL[] = []
  const client = new ApiClient({ fetchImpl: (async (input, init) => {
    expect(init?.method).toBe('GET')
    expect(init?.body).toBeUndefined()
    const url = new URL(String(input), 'https://fixture.example.test')
    if (url.pathname === '/v1/session') return json({ userId: id(600), email: 'viewer@example.test',
      workspaces: [{ workspaceId: id(400), name: 'Fixture', role: 'VIEWER' }] })
    expect(url.pathname).toMatch(/\/effect-deliveries$/)
    expect(url.searchParams.get('limit')).toBe('20')
    reads.push(url)
    return read(url)
  }) as typeof fetch })
  const view = (runId = id(500)) => <SessionProvider client={client}>
    <EffectRecoverySection workspaceId={id(400)} runId={runId} />
  </SessionProvider>
  return { ...render(view()), view, reads }
}

it('refuses foreign, inconsistent or repeating history instead of inferring recovery authority', () => {
  const valid = report([item(1)])
  expect(parseEffectRecovery(valid, id(500), null)).toEqual(valid)
  const malformed: unknown[] = [
    { ...valid, runId: id(501) }, { ...valid, providesRetryAuthority: true },
    { ...valid, providesResetAuthority: true }, { ...valid, nextCursor: id(1) },
    { ...valid, items: [item(1), item(1)] }, { ...valid, items: [{ ...item(1), phase: 'RESPONSE_RETAINED' }] },
    { ...valid, items: [{ ...item(1), requiresInvestigation: false }] },
  ]
  for (const value of malformed) expect(parseEffectRecovery(value, id(500), null)).toBeNull()
  expect(parseEffectRecovery(valid, id(500), id(1))).toBeNull()
})

it('reads bounded pages explicitly with GET only and keeps unknown cleanup separate', async () => {
  const f = fixture((url) => json(url.searchParams.has('after') ? report([item(21)])
    : report(Array.from({ length: 20 }, (_, i) => item(i + 1)), id(20))))
  const user = userEvent.setup()
  await screen.findByRole('heading', { name: 'Action 1: ACTIVATE' })
  expect(screen.getAllByText(/Endpoint cleanup confirmed: Unknown/)).toHaveLength(20)
  expect(screen.getAllByText('Investigation required.')).toHaveLength(20)
  expect(f.reads).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: 'Read next history page' }))
  await screen.findByRole('heading', { name: 'Action 21: ACTIVATE' })
  expect(f.reads[1]?.searchParams.get('after')).toBe(id(20))
  expect(screen.queryByRole('heading', { name: 'Action 1: ACTIVATE' })).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Transport history page 2' })).toHaveFocus()
  expect(screen.queryByRole('button', { name: 'Read next history page' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Start history again' }))
  await screen.findByRole('heading', { name: 'Action 1: ACTIVATE' })
  expect(f.reads[2]?.searchParams.has('after')).toBe(false)
  expect(f.reads).toHaveLength(3)
})

it('does not turn a retained HTTP response into success or reset permission', async () => {
  fixture(() => json(report([{ ...item(1), phase: 'RESPONSE_RETAINED', responseDigest: digest,
    responseStatus: 201, responseRecordedAt: at, requiresInvestigation: false }])))
  await screen.findByRole('heading', { name: 'Action 1: ACTIVATE' })
  expect(screen.getByText(/does not establish application success or journey completion/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /reset|resend|dispatch/i })).not.toBeInTheDocument()
})

it('keeps denied reads distinct from empty history and supports an explicit fresh read', async () => {
  let available = false
  const f = fixture(() => available ? json(report([])) : json({ code: 'CONFLICT', status: 409,
    title: 'History unavailable', detail: 'Original transport history is unavailable.', requestId: 'fixture' }, 409))
  await screen.findByText('Original transport history is unavailable.')
  expect(screen.queryByText(/No permission records on this page/)).not.toBeInTheDocument()
  available = true
  await userEvent.setup().click(screen.getByRole('button', { name: 'Refresh this history page' }))
  await screen.findByText(/No permission records on this page/)
  expect(f.reads).toHaveLength(2)
})

it('discards a late response after switching runs', async () => {
  let resolve: (r: Response) => void = () => { throw new Error('No read started') }
  const pending = new Promise<Response>((done) => { resolve = done })
  const f = fixture((url) => url.pathname.includes(id(500)) ? pending : json(report([item(2)], null, id(501))))
  await screen.findByText(/Loading read-only form transport history/i)
  f.rerender(f.view(id(501)))
  await screen.findByRole('heading', { name: 'Action 2: ACTIVATE' })
  await act(async () => { resolve(json(report([item(1)]))); await pending })
  expect(screen.queryByRole('heading', { name: 'Action 1: ACTIVATE' })).not.toBeInTheDocument()
})
