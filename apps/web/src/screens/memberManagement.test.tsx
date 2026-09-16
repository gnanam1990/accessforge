import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'

const setup = (mode = 'normal', role = 'OWNER') => {
  const server = createFakeServer({ userId: 'u-owner', email: 'owner@example.test',
    workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role }] })
  let record = { userId: 'u-target', role: 'MAINTAINER', revoked: false, revision: 1 }
  const writes: { body: unknown; revision: string | null }[] = []
  const json = (value: unknown) => new Response(JSON.stringify(value),
    { headers: { 'content-type': 'application/json' } })
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = String(input)
    if (url.endsWith('/members/u-target')) {
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { role: string | null; reason: string }
        writes.push({ body, revision: new Headers(init.headers).get('if-match') })
        record = { ...record, role: body.role ?? record.role, revoked: body.role === null, revision: record.revision + 1 }
        if (mode === 'offline') throw new TypeError('response lost after write')
        return json(record)
      }
      return json(mode === 'malformed' ? { ...record, revision: '1' } : record)
    }
    if (url.endsWith('/members')) return json({ items: record.revoked ? [] : [
      { userId: 'u-target', email: 'member@example.test', role: record.role },
    ] })
    return server.fetch(input, init)
  }
  render(<MemoryRouter initialEntries={['/w/ws-1/settings']}>
    <App client={new ApiClient({ fetchImpl, cookieSource: () => '' })} />
  </MemoryRouter>)
  return writes
}

const open = async () => {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'Manage member@example.test' }))
  await screen.findByLabelText('New membership role')
  return user
}

describe('owner existing-member controls', () => {
  it('requires an explicit change/reason/confirmation and sends the read revision', async () => {
    const writes = setup()
    const user = await open()
    await user.click(screen.getByRole('button', { name: 'Save membership change' }))
    expect(writes).toHaveLength(0)
    expect(screen.getByRole('alert', { name: 'This form could not be submitted' })).toHaveFocus()
    await user.selectOptions(screen.getByLabelText('New membership role'), 'REVIEWER')
    await user.type(screen.getByLabelText(/Reason for access change/), 'Review only')
    await user.click(screen.getByLabelText('Confirm this access change'))
    await user.click(screen.getByRole('button', { name: 'Save membership change' }))
    await screen.findByText('Current state: REVIEWER; revision 2.')
    expect(writes[0]?.body).toEqual({ role: 'REVIEWER', reason: 'Review only' })
    expect(writes[0]?.revision?.replaceAll('"', '')).toBe('1')
  })

  it('keeps a revoked member selected for explicit restoration after inventory reload', async () => {
    const writes = setup()
    const user = await open()
    await user.selectOptions(screen.getByLabelText('New membership role'), 'REVOKE')
    await user.type(screen.getByLabelText(/Reason for access change/), 'Access no longer needed')
    await user.click(screen.getByLabelText('Confirm this access change'))
    await user.click(screen.getByRole('button', { name: 'Save membership change' }))
    await screen.findByText('Current state: REVOKED; revision 2.')
    expect(writes[0]?.body).toMatchObject({ role: null })
    await user.selectOptions(screen.getByLabelText('New membership role'), 'VIEWER')
    await user.type(screen.getByLabelText(/Reason for access change/), 'Restore read access')
    await user.click(screen.getByLabelText('Confirm this access change'))
    await user.click(screen.getByRole('button', { name: 'Save membership change' }))
    await screen.findByText('Current state: VIEWER; revision 3.')
    expect(writes[1]?.revision?.replaceAll('"', '')).toBe('2')
  })

  it('locks an uncertain write until explicit readback without replaying it', async () => {
    const writes = setup('offline')
    const user = await open()
    await user.selectOptions(screen.getByLabelText('New membership role'), 'VIEWER')
    await user.type(screen.getByLabelText(/Reason for access change/), 'Read only')
    await user.click(screen.getByLabelText('Confirm this access change'))
    await user.click(screen.getByRole('button', { name: 'Save membership change' }))
    await screen.findByText(/Whether access changed is unknown/)
    expect(screen.getByRole('button', { name: 'Save membership change' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Read current membership and discard draft' }))
    await screen.findByText('Current state: VIEWER; revision 2.')
    expect(writes).toHaveLength(1)
  })

  it('does not offer writes for malformed readback', async () => {
    setup('malformed')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Manage member@example.test' }))
    await screen.findByText(/membership record is malformed/)
    expect(screen.queryByRole('button', { name: 'Save membership change' })).not.toBeInTheDocument()
  })

  it.each(['MAINTAINER', 'REVIEWER', 'VIEWER'])('does not give %s owner controls', async role => {
    setup('normal', role)
    await screen.findByText('member@example.test')
    expect(screen.queryByRole('button', { name: 'Manage member@example.test' })).not.toBeInTheDocument()
  })
})
