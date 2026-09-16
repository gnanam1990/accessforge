import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { ApiClient } from '../api/client'
import type { MembershipInvitation } from '../api/resources'
import { createFakeServer } from '../test/fakeServer'

const setup = (mode = 'normal', role = 'OWNER') => {
  const server = createFakeServer({ userId: 'u-owner', email: 'owner@example.test',
    workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role }] })
  const records = new Map<string, MembershipInvitation>()
  if (mode === 'pages') {
    for (const suffix of ['1', '2']) {
      const id = `00000000-0000-4000-8000-00000000000${suffix}`
      records.set(id, { invitationId: id, githubSubject: `100${suffix}`, role: 'VIEWER',
        reason: 'Seeded offer', createdBy: 'u-owner', revision: 1, state: 'PENDING',
        createdAt: '2026-09-16T12:00:00Z', expiresAt: '2026-09-16T13:00:00Z' })
    }
  }
  const writes: { method: string; body: unknown; revision: string | null }[] = []
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value),
    { status, headers: { 'content-type': 'application/json' } })
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    if (url.pathname.endsWith('/membership-invitations')) {
      if (mode === 'bad-history') return json({ items: [null], nextCursor: null })
      if (mode === 'pages') {
        const rows = [...records.values()]
        const row = url.searchParams.has('after') ? rows[1]! : rows[0]!
        return json({ items: [row], nextCursor: row === rows[0] ? row.invitationId : null })
      }
      return json({ items: [...records.values()], nextCursor: null })
    }
    if (url.pathname.includes('/membership-invitations/')) {
      const id = url.pathname.split('/').at(-1)!
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body))
        writes.push({ method: 'PUT', body, revision: new Headers(init.headers).get('if-match') })
        const record: MembershipInvitation = { invitationId: id, githubSubject: body.githubSubject,
          role: body.role, reason: body.reason, createdBy: 'u-owner', revision: 1, state: 'PENDING',
          createdAt: '2026-09-16T12:00:00Z', expiresAt: '2026-09-16T13:00:00Z' }
        records.set(id, record)
        if (mode === 'offline') throw new TypeError('response lost after commit')
        return json(mode === 'malformed' ? { ...record, revision: '1' } : record, 201)
      }
      const record = records.get(id)
      if (init?.method === 'DELETE' && record !== undefined) {
        writes.push({ method: 'DELETE', body: null, revision: new Headers(init.headers).get('if-match') })
        const revoked: MembershipInvitation = { ...record, revision: 2, state: 'REVOKED' }
        records.set(id, revoked)
        if (mode === 'revoke-offline') throw new TypeError('revocation reply lost')
        return json(revoked)
      }
      return record === undefined ? json({ code: 'NOT_FOUND', detail: 'Not found' }, 404) : json(record)
    }
    return server.fetch(input, init)
  }
  render(<MemoryRouter initialEntries={['/w/ws-1/settings']}>
    <App client={new ApiClient({ fetchImpl, cookieSource: () => '' })} />
  </MemoryRouter>)
  return writes
}

const fill = async () => {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText(/GitHub numeric account ID/), '9223372036854775806')
  await user.selectOptions(screen.getByLabelText('Invitation role'), 'REVIEWER')
  await user.type(screen.getByLabelText(/Reason for invitation/), 'Review evidence')
  await user.click(screen.getByLabelText('Confirm numeric identity and offered role'))
  return user
}

describe('owner invitation offers', () => {
  it('pages history explicitly and keeps a selected offer available across pages', async () => {
    setup('pages')
    const user = userEvent.setup()
    const first = '00000000-0000-4000-8000-000000000001'
    const second = '00000000-0000-4000-8000-000000000002'
    await user.click(await screen.findByRole('button', { name: `Inspect ${first}` }))
    await screen.findByText(/GitHub ID 1001; VIEWER; state PENDING/)
    await user.click(screen.getByRole('button', { name: 'Next invitation page' }))
    await screen.findByRole('button', { name: `Inspect ${second}` })
    expect(screen.queryByRole('button', { name: `Inspect ${first}` })).not.toBeInTheDocument()
    expect(screen.getByText(/GitHub ID 1001; VIEWER; state PENDING/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'First invitation page' }))
    await screen.findByRole('button', { name: `Inspect ${first}` })
  })

  it('rejects malformed history instead of offering row actions', async () => {
    setup('bad-history')
    await screen.findByText(/Invitation history is malformed/)
    expect(screen.queryByRole('button', { name: /^Inspect / })).not.toBeInTheDocument()
  })

  it('requires confirmation and preserves the exact numeric identity on creation', async () => {
    const writes = setup()
    const user = await fill()
    await user.click(screen.getByLabelText('Confirm numeric identity and offered role'))
    await user.click(screen.getByRole('button', { name: 'Create invitation offer' }))
    expect(writes).toHaveLength(0)
    expect(screen.getByRole('alert', { name: 'This form could not be submitted' })).toHaveFocus()
    await user.click(screen.getByLabelText('Confirm numeric identity and offered role'))
    await user.click(screen.getByRole('button', { name: 'Create invitation offer' }))
    await screen.findByText(/recorded as PENDING. No email was sent/)
    expect(writes[0]?.body).toEqual({ githubSubject: '9223372036854775806', role: 'REVIEWER',
      ttlSeconds: 3600, reason: 'Review evidence' })
    expect(writes[0]?.revision?.replaceAll('"', '')).toBe('0')
    expect(screen.getByLabelText(/GitHub numeric account ID/)).toHaveValue('')
  })

  it.each(['offline', 'malformed'])('reads %s save outcome without another PUT', async mode => {
    const writes = setup(mode)
    const user = await fill()
    await user.click(screen.getByRole('button', { name: 'Create invitation offer' }))
    await screen.findByText(/invitation save is not confirmed/)
    expect(screen.getByRole('button', { name: 'Create invitation offer' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Read invitation save outcome' }))
    await screen.findByText(/recorded as PENDING. No email was sent/)
    expect(writes).toHaveLength(1)
  })

  it('confirms revocation and reconciles a lost response without repeating DELETE', async () => {
    const writes = setup('revoke-offline')
    const user = await fill()
    await user.click(screen.getByRole('button', { name: 'Create invitation offer' }))
    const revoke = await screen.findByRole('button', { name: 'Revoke invitation offer' })
    expect(revoke).toBeDisabled()
    await user.click(screen.getByLabelText('Confirm invitation revocation'))
    await user.click(revoke)
    await screen.findByText(/Revocation is not confirmed/)
    expect(revoke).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Read current invitation' }))
    await screen.findByText(/state REVOKED; revision 2/)
    expect(writes.map(write => write.method)).toEqual(['PUT', 'DELETE'])
    expect(writes[1]?.revision?.replaceAll('"', '')).toBe('1')
  })

  it.each(['VIEWER', 'REVIEWER', 'MAINTAINER'])('never gives %s an invitation form', async role => {
    setup('normal', role)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Workspace settings' })).toBeVisible())
    expect(screen.queryByRole('button', { name: 'Create invitation offer' })).not.toBeInTheDocument()
  })
})
