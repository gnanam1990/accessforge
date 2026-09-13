import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import { parseReaderConsentScope, type ReaderConsent, type ReaderConsentScope } from '../api/readerConsent'
import type { Run } from '../api/resources'
import { SessionProvider } from '../session/SessionProvider'
import { ReaderStartupSection } from './ReaderStartupSection'
import { PATHS } from '../../../../packages/clients/ts/src/operations'

const id = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`
const run: Run = { runId: id(1), status: 'RUNNING', outcome: 'NOT_EVALUATED', revision: 2,
  leaseEpoch: 1, manifestDigest: 'a'.repeat(64), cancellationRequestedAt: null,
  stopAcknowledgedAt: null, ambiguityReason: null, quarantined: false, retryOf: null }
const scope = (): ReaderConsentScope => ({ runId: run.runId, runnerId: id(2), revision: 2,
  manifestDigest: run.manifestDigest, desktopSessionKey: 'b'.repeat(64), runnerProfileDigest: 'c'.repeat(64),
  effectsDigest: 'd'.repeat(64), maximumExpiresAt: new Date(Date.now() + 120000).toISOString(),
  meaning: 'REVIEW_SCOPE_ONLY_NOT_STARTUP_CONSENT', effects: { sdk: '@guidepup/guidepup', sdkVersion: '0.34.0',
    reader: 'VoiceOver', effects: ['TERMINATE_AND_RESTART_VOICEOVER', 'MOUNT_GUIDEPUP_READER_PREFERENCES'],
    requiresDedicatedDesktop: true, doesNotAuthorize: ['GRANT_TCC_PERMISSIONS', 'INITIAL_APPLESCRIPT_CONFIGURATION'] } })

function fixture(role = 'OWNER') {
  const reviewed = scope(), writes: { path: string; body: Record<string, unknown>; headers: Headers }[] = []
  let grant: ReaderConsent | null = null
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input), headers = new Headers(init?.headers)
    expect(PATHS).toContain(path.split('?')[0]?.replace(id(4), '{workspace_id}').replace(id(1), '{run_id}'))
    if (path === '/v1/session') return json({ userId: id(3), email: 'operator@example.test', workspaces: [{ workspaceId: id(4), name: 'Fixture', role }] })
    if (path.endsWith('/runners')) return json({ items: [{ runnerId: id(2), name: 'Dedicated desktop', platform: 'darwin', revoked: false }], nextCursor: null, readinessMeaning: 'REGISTRATIONS_ONLY' })
    if (path.includes('/scope?')) return json(reviewed)
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>
      writes.push({ path, body, headers })
      if (path.endsWith('/revocation') && grant !== null) grant = { ...grant, revokedAt: new Date().toISOString() }
      else grant = { ...reviewed, consentId: id(5), actorId: id(3), expiresAt: String(body.expiresAt), revokedAt: null,
        boundSessionId: null, meaning: 'STORED_OPERATOR_STARTUP_CONSENT_NOT_PHYSICAL_PROOF' }
      return json(grant, path.endsWith('/revocation') ? 200 : 201)
    }
    return grant === null ? json({ code: 'RESOURCE_NOT_FOUND', status: 404, title: 'Not found', detail: 'No consent', requestId: 'fixture' }, 404) : json(grant)
  }) as typeof fetch
  const client = new ApiClient({ fetchImpl, cookieSource: () => 'accessforge_csrf=fixture-csrf' })
  render(<SessionProvider client={client}><ReaderStartupSection workspaceId={id(4)} run={run} /></SessionProvider>)
  return { reviewed, writes }
}

it('requires explicit scope review and acknowledgement, then confirms exact permanent revocation', async () => {
  const f = fixture(), user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Inspect reader consent' }))
  await user.selectOptions(await screen.findByLabelText(/Runner/), id(2))
  await user.click(screen.getByRole('button', { name: 'Review startup scope' }))
  const ack = await screen.findByRole('checkbox'), store = screen.getByRole('button', { name: 'Store reader startup consent' })
  expect(ack).not.toBeChecked(); expect(store).toBeDisabled(); expect(f.writes).toHaveLength(0)
  await user.click(ack); await user.click(store)
  await screen.findByRole('heading', { name: 'Stored operator decision' })
  expect(f.writes).toHaveLength(1)
  expect(f.writes[0]?.body).toEqual({ runnerId: id(2), manifestDigest: run.manifestDigest,
    desktopSessionKey: f.reviewed.desktopSessionKey, runnerProfileDigest: f.reviewed.runnerProfileDigest,
    effectsDigest: f.reviewed.effectsDigest, expiresAt: f.reviewed.maximumExpiresAt, dedicatedDesktopAcknowledged: true })
  expect(f.writes[0]?.headers.get('if-match')).toBe('2')
  expect(f.writes[0]?.headers.get('x-csrf-token')).toBe('fixture-csrf')
  expect(f.writes[0]?.headers.get('idempotency-key')).toBeTruthy()
  await user.click(screen.getByRole('button', { name: 'Revoke reader startup consent' }))
  expect(f.writes).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: 'Confirm permanent revocation' }))
  await screen.findByRole('heading', { name: 'Revoked operator decision' })
  expect(f.writes[1]?.body).toEqual({ consentId: id(5) })
})

it('does not offer a consent mutation to a non-owner', async () => {
  const f = fixture('VIEWER'), user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Inspect reader consent' }))
  await screen.findByText(/Only a workspace owner/)
  expect(screen.queryByRole('button', { name: 'Review startup scope' })).not.toBeInTheDocument()
  expect(f.writes).toHaveLength(0)
})

it('changing expiry clears acknowledgement and an invalid expiry cannot submit', async () => {
  const f = fixture(), user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: 'Inspect reader consent' }))
  await user.selectOptions(await screen.findByLabelText(/Runner/), id(2))
  await user.click(screen.getByRole('button', { name: 'Review startup scope' }))
  const ack = await screen.findByRole('checkbox')
  await user.click(ack)
  const expiry = screen.getByLabelText(/Expiry \(UTC\)/)
  await user.clear(expiry); await user.type(expiry, '2000-01-01T00:00:00Z')
  expect(ack).not.toBeChecked()
  await user.click(ack); await user.click(screen.getByRole('button', { name: 'Store reader startup consent' }))
  await waitFor(() => expect(expiry).toHaveAttribute('aria-invalid', 'true'))
  expect(expiry).toHaveFocus(); expect(f.writes).toHaveLength(0)
})

it('refuses mismatched or malformed review scopes', () => {
  const value = scope()
  expect(parseReaderConsentScope(value, run.runId, id(2))).toEqual(value)
  expect(parseReaderConsentScope(value, id(9), id(2))).toBeNull()
  expect(parseReaderConsentScope(value, run.runId, id(9))).toBeNull()
  expect(parseReaderConsentScope({ ...value, revision: true }, run.runId, id(2))).toBeNull()
  expect(parseReaderConsentScope({ ...value, effects: { ...value.effects, requiresDedicatedDesktop: false } }, run.runId, id(2))).toBeNull()
})
