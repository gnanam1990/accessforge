import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { ApiClient } from '../api/client'
import { AcceptInvitation } from './AcceptInvitation'

const session = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))
vi.mock('../session/SessionProvider', () => ({ useSession: () => session.current }))
const workspaceId = '11111111-1111-4111-8111-111111111111'
const invitationId = '22222222-2222-4222-8222-222222222222'
const offer = { workspaceId, invitationId, workspaceName: 'Team', role: 'REVIEWER',
  reason: 'Review access', expiresAt: '2026-12-01T00:00:00Z', state: 'PENDING', revision: 1 }
const refresh = vi.fn(async () => undefined)
beforeEach(() => { refresh.mockClear() })

async function setup(acceptResponse: unknown, readOffer: unknown = offer) {
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST') {
      if (acceptResponse === 'offline') throw new Error('network')
      return Response.json(acceptResponse)
    }
    return Response.json(readOffer)
  })
  session.current = { state: { status: 'authenticated', userId: 'user-1' }, refresh,
    client: new ApiClient({ fetchImpl: fetcher, cookieSource: () => 'accessforge_csrf=csrf' }) }
  render(<AcceptInvitation />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText(/Workspace ID/), workspaceId)
  await user.type(screen.getByLabelText(/Invitation ID/), invitationId)
  await user.click(screen.getByRole('button', { name: 'Read invitation' }))
  return { user, fetcher }
}

it('requires confirmation, posts no identity, then refreshes only a matching receipt', async () => {
  const { user, fetcher } = await setup({ workspaceId, invitationId, sessionRotated: true,
    membership: { userId: 'user-1', role: 'REVIEWER', revoked: false, revision: 1 } })
  expect(await screen.findByRole('button', { name: 'Accept invitation' })).toBeDisabled()
  await user.click(screen.getByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: 'Accept invitation' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Invitation accepted')
  expect(refresh).toHaveBeenCalledOnce()
  const options = fetcher.mock.calls[1]?.[1]
  expect(options?.body).toBe('{"accept":true}')
  expect(new Headers(options?.headers).get('if-match')).toBe('1')
  expect(new Headers(options?.headers).get('x-csrf-token')).toBe('csrf')
})

it.each(['offline', { sessionRotated: true }])('locks unconfirmed acceptance without automatic retry: %j', async (response) => {
  const { user, fetcher } = await setup(response)
  await user.click(await screen.findByRole('checkbox'))
  await user.click(screen.getByRole('button', { name: 'Accept invitation' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Acceptance is not confirmed')
  expect(screen.getByRole('button', { name: 'Accept invitation' })).toBeDisabled()
  expect(screen.getByLabelText(/Workspace ID/)).toBeDisabled()
  expect(refresh).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Read invitation' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Review the offer')
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(fetcher.mock.calls.filter((call) => call[1]?.method === 'POST')).toHaveLength(1)
})

it('does not offer acceptance for another reference or a terminal offer', async () => {
  await setup({}, { ...offer, invitationId: workspaceId })
  expect(await screen.findByRole('status')).toHaveTextContent('could not be verified')
  expect(screen.queryByRole('button', { name: 'Accept invitation' })).not.toBeInTheDocument()
})

it.each(['MAINTAINER', 'ENGINEER'])('validates the offered role against the server role vocabulary: %s', async (role) => {
  await setup({}, { ...offer, role })
  if (role === 'MAINTAINER') {
    expect(await screen.findByRole('button', { name: 'Accept invitation' })).toBeDisabled()
    expect(screen.getByText(/Offered role: MAINTAINER/)).toBeVisible()
  } else {
    expect(await screen.findByRole('status')).toHaveTextContent('could not be verified')
    expect(screen.queryByRole('button', { name: 'Accept invitation' })).not.toBeInTheDocument()
  }
})
