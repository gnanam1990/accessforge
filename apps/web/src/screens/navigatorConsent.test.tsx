import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import { parseNavigatorConsent, parseNavigatorScope, type NavigatorConsent, type NavigatorModelScope } from '../api/navigatorConsent'
import type { Run } from '../api/resources'
import { SessionProvider } from '../session/SessionProvider'
import { NavigatorConsentSection } from './NavigatorConsentSection'
import { PATHS } from '../../../../packages/clients/ts/src/operations'

const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const run: Run = { runId: id(1), status: 'RUNNING', outcome: 'NOT_EVALUATED', revision: 2,
  leaseEpoch: 1, manifestDigest: 'a'.repeat(64), cancellationRequestedAt: null,
  stopAcknowledgedAt: null, ambiguityReason: null, quarantined: false, retryOf: null }
const scope = (): NavigatorModelScope => ({ revision: 2, manifestDigest: run.manifestDigest,
  modelConfigDigest: 'b'.repeat(64), maximumCalls: 500, tokensPerCall: 24000,
  maximumExpiresAt: new Date(Date.now() + 120000).toISOString(), billableCallAcknowledged: false,
  disclosure: 'Approved task intent, safe fixture values and retained reader announcements may be disclosed. Calls and retries may be billable.',
  meaning: 'PREVIEW_NOT_MODEL_CONSENT_OR_INVOCATION', modelProfile: {
    sdk_distribution: 'strands-agents', sdk_version: '1.55.1', provider: 'amazon-bedrock',
    model_id: 'global.anthropic.claude-sonnet-4-6', region_name: 'us-east-1', temperature: 0,
    provider_max_tokens: 512, invocation_turns: 1, invocation_output_tokens: 1024,
    invocation_total_tokens: 12000, max_context_characters: 24000, call_timeout_seconds: 30,
    model_attempts: 2, retry_initial_delay_seconds: 1, retry_max_delay_seconds: 2,
  } })
const consent = (reviewed = scope()): NavigatorConsent => ({ ...reviewed, runId: run.runId,
  consentId: id(5), actorId: id(3), maxCalls: 3, expiresAt: reviewed.maximumExpiresAt,
  revokedAt: null, invocations: [], meaning: 'STORED_MODEL_CONSENT_NOT_INVOCATION_OR_FINANCIAL_CAP' })
const codexScope = (): NavigatorModelScope => ({ ...scope(), tokensPerCall: 12000, modelProfile: {
  provider: 'codex-chatgpt', sdk_version: '0.154.0', model_id: 'gpt-6-astra',
  budget_semantics: 'RESULT_ADMISSION_NOT_SPEND_CAP', invocation_output_tokens: 1024,
  invocation_total_tokens: 12000, max_context_characters: 24000, call_timeout_seconds: 30,
} })
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
const problem = (status: number, code: string) => json({ status, code, title: 'Refused', detail: 'Fixture refusal', requestId: 'fixture' }, status)
const Address = () => <output aria-label="Current address">{useLocation().search}</output>

function fixture(options: { role?: string; reviewed?: NavigatorModelScope; grant?: NavigatorConsent;
  beforeWrite?: () => Promise<Response | void>; address?: string; readOffline?: boolean } = {}) {
  const reviewed = options.reviewed ?? scope(), writes: { path: string; body: Record<string, unknown>; headers: Headers }[] = []
  const reads: string[] = []
  let grant: NavigatorConsent | null = options.grant ?? null
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input), headers = new Headers(init?.headers)
    expect(PATHS).toContain(path.replace(id(4), '{workspace_id}').replace(id(1), '{run_id}'))
    if (path === '/v1/session') return json({ userId: id(3), email: 'operator@example.test', workspaces: [{ workspaceId: id(4), name: 'Fixture', role: options.role ?? 'OWNER' }] })
    if (init?.method !== 'POST') reads.push(path)
    if (path.endsWith('/scope')) return json(reviewed)
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      writes.push({ path, body, headers })
      const override = await options.beforeWrite?.()
      if (override) return override
      if (path.endsWith('/revocation') && grant !== null) grant = { ...grant, revokedAt: new Date().toISOString() }
      else grant = { ...consent(reviewed), maxCalls: Number(body.maxCalls), expiresAt: String(body.expiresAt) }
      return json(grant, path.endsWith('/revocation') ? 200 : 201)
    }
    if (options.readOffline) throw new Error('Synthetic offline read')
    return grant === null ? problem(404, 'RESOURCE_NOT_FOUND') : json(grant)
  }) as typeof fetch
  const client = new ApiClient({ fetchImpl, cookieSource: () => 'accessforge_csrf=fixture-csrf' })
  const view = render(<MemoryRouter initialEntries={[options.address ?? '/run']}><SessionProvider client={client}>
    <NavigatorConsentSection workspaceId={id(4)} run={run} /><Address />
  </SessionProvider></MemoryRouter>)
  return { reviewed, writes, reads, view }
}
async function review() {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Inspect model consent' }))
  await user.click(await screen.findByRole('button', { name: 'Review model disclosure and limits' }))
  await screen.findByRole('checkbox')
  return user
}
async function submit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: 'Store navigator model consent' }))
}

it('reviews and stores Codex OAuth consent with quota semantics and no invented AWS region', async () => {
  const f = fixture({ reviewed: codexScope() }), user = await review()
  expect(screen.getByText('Codex CLI — ChatGPT OAuth')).toBeVisible()
  expect(screen.queryByText('Region')).not.toBeInTheDocument()
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(screen.getByRole('checkbox')).toHaveAccessibleName(/consumes account usage/)
  expect(screen.getByText(/CLI-internal retries are not measured/)).toBeVisible()
  await submit(user)
  await screen.findByRole('heading', { name: 'Stored model consent' })
  expect(f.writes).toHaveLength(1)
  expect(f.writes[0]?.body.modelProfile).toEqual(f.reviewed.modelProfile)
})

it('refuses mixed, unknown and inconsistent Codex profiles before rendering consent', () => {
  const original = codexScope()
  expect(parseNavigatorScope(original)).toEqual(original)
  expect(parseNavigatorConsent(consent(original), run.runId)).not.toBeNull()
  for (const fields of [{ region_name: 'us-east-1' }, { model_attempts: 2 }, { apiKey: 'hidden' },
    { budget_semantics: 'HARD_SPEND_CAP' }, { invocation_total_tokens: 1000, invocation_output_tokens: 2000 },
    { call_timeout_seconds: 0.5 }, { invocation_output_tokens: true }]) {
    expect(parseNavigatorScope({ ...original, modelProfile: { ...original.modelProfile, ...fields } })).toBeNull()
  }
  expect(parseNavigatorScope({ ...original, tokensPerCall: 24000 })).toBeNull()
})

it('reviews an unchecked exact profile and stores only the acknowledged, bounded grant as a maintainer', async () => {
  const f = fixture({ role: 'MAINTAINER' }), user = await review()
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(screen.getByRole('button', { name: 'Store navigator model consent' })).toBeDisabled()
  expect(f.writes).toHaveLength(0)
  await submit(user)
  await screen.findByRole('heading', { name: 'Stored model consent' })
  expect(f.writes).toHaveLength(1)
  expect(f.writes[0]?.body).toEqual({ manifestDigest: run.manifestDigest, modelProfile: f.reviewed.modelProfile,
    maxCalls: 1, expiresAt: f.reviewed.maximumExpiresAt, billableCallAcknowledged: true })
  expect(f.writes[0]?.headers.get('if-match')).toBe('2')
  expect(f.writes[0]?.headers.get('x-csrf-token')).toBe('fixture-csrf')
  expect(screen.getByLabelText('Current address')).toHaveTextContent(f.writes[0]!.headers.get('idempotency-key')!)
  await user.click(screen.getByRole('button', { name: 'Revoke navigator model consent' }))
  expect(f.writes).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: 'Confirm model revocation' }))
  await screen.findByRole('heading', { name: 'Revoked model consent' })
  expect(f.writes[1]?.body).toEqual({ consentId: id(5) })
  expect(screen.queryByRole('button', { name: 'Review model disclosure and limits' })).not.toBeInTheDocument()
})

it('keeps the pending grant mounted and disables close, refresh and duplicate writes', async () => {
  let release: (() => void) | undefined
  const f = fixture({ beforeWrite: () => new Promise<void>(resolve => { release = resolve }) }), user = await review()
  await submit(user)
  const store = screen.getByRole('button', { name: 'Store navigator model consent' })
  const close = screen.getByRole('button', { name: 'Close model consent' })
  const refresh = screen.getByRole('button', { name: 'Read stored model consent again' })
  expect(store).toBeDisabled(); expect(close).toBeDisabled(); expect(refresh).toBeDisabled()
  const count = f.reads.length
  await user.click(store); await user.click(close); await user.click(refresh)
  expect(f.writes).toHaveLength(1); expect(f.reads).toHaveLength(count)
  release?.()
  await screen.findByRole('heading', { name: 'Stored model consent' })
})

it('retains an unknown write marker through close, reopen and a new mount without replacement', async () => {
  const f = fixture({ beforeWrite: async () => { throw new Error('Lost response') } }), user = await review()
  await submit(user)
  await screen.findByRole('heading', { name: 'Reconcile the original consent request' })
  const address = screen.getByLabelText('Current address').textContent!
  await user.click(screen.getByRole('button', { name: 'Close model consent' }))
  await user.click(screen.getByRole('button', { name: 'Inspect model consent' }))
  await screen.findByRole('heading', { name: 'Reconcile the original consent request' })
  expect(f.writes).toHaveLength(1)
  f.view.unmount()
  const next = fixture({ address: `/run${address}` })
  await user.click(screen.getByRole('button', { name: 'Inspect model consent' }))
  await screen.findByRole('heading', { name: 'Reconcile the original consent request' })
  expect(screen.queryByRole('button', { name: 'Review model disclosure and limits' })).not.toBeInTheDocument()
  expect(next.writes).toHaveLength(0)
})

it.each([400, 403])('allows fresh explicit review after a recognised pre-write refusal (%s)', async status => {
  const f = fixture({ address: '/run?panel=cost', beforeWrite: async () => problem(status, status === 400 ? 'INVALID_INPUT' : 'PERMISSION_DENIED') }), user = await review()
  await submit(user)
  await screen.findByText(/server refused this request before storing/)
  await waitFor(() => expect(screen.getByLabelText('Current address')).toHaveTextContent(/^\?panel=cost$/))
  await user.click(await screen.findByRole('button', { name: 'Review model disclosure and limits' }))
  expect(await screen.findByRole('checkbox')).not.toBeChecked()
  expect(f.writes).toHaveLength(1)
})

it.each(['conflict', 'malformed-success'])('requires reconciliation for %s without claiming rollback', async kind => {
  const f = fixture({ beforeWrite: async () => kind === 'conflict' ? problem(409, 'CONFLICT') : json({ invalid: true }, 201) }), user = await review()
  await submit(user)
  await screen.findByRole('heading', { name: 'Reconcile the original consent request' })
  expect(screen.getByLabelText('Current address')).toHaveTextContent('navigatorConsentOperation=')
  expect(f.writes).toHaveLength(1)
})

it('clears acknowledgement on field edits and focuses linked errors without issuing consent', async () => {
  const f = fixture(), user = await review()
  await user.click(screen.getByRole('checkbox'))
  const calls = screen.getByLabelText(/Maximum model calls/), expiry = screen.getByLabelText(/Model consent expiry/)
  await user.clear(calls); await user.type(calls, '501')
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  await user.clear(expiry); await user.type(expiry, '2000-01-01T00:00:00Z')
  await submit(user)
  expect(screen.getByRole('alert')).toHaveFocus()
  expect(calls).toHaveAttribute('aria-invalid', 'true'); expect(expiry).toHaveAttribute('aria-invalid', 'true')
  await user.click(screen.getByRole('link', { name: /Choose 1 to 500/ }))
  expect(calls).toHaveFocus(); expect(f.writes).toHaveLength(0)
})

it('keeps a viewer read-only and displays retained uncertainty without replay controls', async () => {
  const grant = consent()
  const f = fixture({ role: 'VIEWER', grant: { ...grant, invocations: [{ operationId: id(6), afterActionSequence: 0,
    status: 'UNCONFIRMED', reservedTokens: 24000, createdAt: new Date().toISOString(), finishedAt: new Date().toISOString() }] } })
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Inspect model consent' }))
  await screen.findByRole('heading', { name: 'Stored model consent' })
  await user.click(screen.getByText('Retained model invocations (1)'))
  expect(screen.getByText(/Provider or action outcome is uncertain/)).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Revoke navigator model consent' })).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})

it('refuses stale review scope and does not treat an offline history read as missing consent', async () => {
  const f = fixture({ reviewed: { ...scope(), revision: 1 } }), user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Inspect model consent' }))
  await user.click(await screen.findByRole('button', { name: 'Review model disclosure and limits' }))
  await screen.findByText(/The run changed/)
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); expect(f.writes).toHaveLength(0)
  f.view.unmount()
  fixture({ readOffline: true })
  await user.click(screen.getByRole('button', { name: 'Inspect model consent' }))
  await screen.findByRole('heading', { name: 'No connection to the server' })
  expect(screen.queryByRole('button', { name: 'Review model disclosure and limits' })).not.toBeInTheDocument()
})

it('validates closed model fields, exact run identity and invocation states before rendering', () => {
  const reviewed = scope(), grant = consent(reviewed)
  expect(parseNavigatorScope(reviewed)).toEqual(reviewed)
  expect(parseNavigatorScope({ ...reviewed, modelProfile: { ...reviewed.modelProfile, apiKey: 'must-not-render' } })).toBeNull()
  expect(parseNavigatorScope({ ...reviewed, tokensPerCall: 1 })).toBeNull()
  expect(parseNavigatorScope({ ...reviewed, billableCallAcknowledged: true })).toBeNull()
  expect(parseNavigatorConsent(grant, id(99))).toBeNull()
  const call = { operationId: id(6), afterActionSequence: 0, reservedTokens: 24000,
    status: 'STARTED', createdAt: new Date().toISOString(), finishedAt: null }
  expect(parseNavigatorConsent({ ...grant, invocations: [call] }, run.runId)).not.toBeNull()
  expect(parseNavigatorConsent({ ...grant, invocations: [call, call] }, run.runId)).toBeNull()
  expect(parseNavigatorConsent({ ...grant, invocations: [{ ...call, status: 'SUCCEEDED' }] }, run.runId)).toBeNull()
  expect(parseNavigatorConsent({ ...grant, invocations: [{ ...call, status: ['STARTED'], finishedAt: call.createdAt }] }, run.runId)).toBeNull()
  expect(parseNavigatorConsent({ ...grant, invocations: [{ ...call, status: 'UNCONFIRMED' }] }, run.runId)).toBeNull()
})
