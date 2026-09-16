import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'

const setup = (mode: 'save' | 'offline' | 'conflict' = 'save', role = 'OWNER') => {
  const server = createFakeServer({ userId: 'u-1', email: 'owner@example.test',
    workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role }] })
  const writes: { body: unknown; revision: string | null }[] = []
  let reads = 0
  const fetchImpl: typeof fetch = async (input, init) => {
    if (String(input).endsWith('/settings/retention')) {
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { classes: { evidenceClass: string; retainDays: number; consentRequired: boolean }[] }
        writes.push({ body, revision: new Headers(init.headers).get('if-match') })
        if (mode === 'offline') throw new TypeError('connection lost')
        if (mode === 'conflict') return new Response(JSON.stringify({ status: 409,
          title: 'Changed', code: 'STALE_REVISION', detail: 'Read again.' }),
        { status: 409, headers: { 'content-type': 'application/problem+json' } })
        server.data.retention = { ...server.data.retention, revision: 1,
          classes: body.classes.map((entry) => ({ ...entry, invalidatesCompleteness: true, meaning: 'Evidence' })) }
        return new Response(JSON.stringify({ revision: 1 }),
          { status: 201, headers: { 'content-type': 'application/json' } })
      }
      reads += 1
    }
    return server.fetch(input, init)
  }
  render(<MemoryRouter initialEntries={['/w/ws-1/settings']}>
    <App client={new ApiClient({ fetchImpl, cookieSource: () => '' })} />
  </MemoryRouter>)
  return { writes, reads: () => reads }
}

const openForm = async () => {
  const user = userEvent.setup()
  await user.click(await screen.findByText('Change retention policy'))
  return user
}

describe('owner retention decisions', () => {
  it('requires confirmation and submits every class against the read revision', async () => {
    const { writes } = setup()
    const user = await openForm()
    await user.click(screen.getByRole('button', { name: 'Save retention policy' }))
    expect(writes).toHaveLength(0)
    expect(screen.getByRole('alert', { name: 'This form could not be submitted' })).toHaveFocus()
    await user.click(screen.getByLabelText('Confirm policy consequences'))
    await user.click(screen.getByRole('button', { name: 'Save retention policy' }))
    await screen.findByText('Policy revision 1.')
    expect(writes).toHaveLength(1)
    expect(writes[0]?.revision?.replaceAll('"', '')).toBe('0')
    expect(writes[0]?.body).toEqual({ classes: [
      { evidenceClass: 'READER_SPEECH', retainDays: 30, consentRequired: true },
      { evidenceClass: 'SCREEN_RECORDING', retainDays: 14, consentRequired: true },
    ] })
  })

  it('rejects out-of-range periods and invalidates confirmation when a draft changes', async () => {
    const { writes } = setup()
    const user = await openForm()
    await user.click(screen.getByLabelText('Confirm policy consequences'))
    await user.clear(screen.getByLabelText(/READER_SPEECH retention days/))
    await user.type(screen.getByLabelText(/READER_SPEECH retention days/), '36501')
    expect(screen.getByLabelText('Confirm policy consequences')).not.toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Save retention policy' }))
    expect(screen.getByLabelText(/READER_SPEECH retention days/)).toHaveAttribute('aria-invalid', 'true')
    expect(writes).toHaveLength(0)
  })

  it.each(['offline', 'conflict'] as const)('locks %s writes until explicit current-policy read', async (mode) => {
    const state = setup(mode)
    const user = await openForm()
    await user.click(screen.getByLabelText('Confirm policy consequences'))
    await user.click(screen.getByRole('button', { name: 'Save retention policy' }))
    await screen.findByRole('heading', { name: 'Retention needs attention' })
    expect(screen.getByRole('button', { name: 'Save retention policy' })).toBeDisabled()
    expect(screen.getByLabelText(/READER_SPEECH retention days/)).toBeDisabled()
    const before = state.reads()
    await user.click(screen.getByRole('button', { name: 'Read current policy and discard draft' }))
    await waitFor(() => expect(state.reads()).toBeGreaterThan(before))
    await screen.findByText('Change retention policy')
    expect(state.writes).toHaveLength(1)
  })

  it.each(['VIEWER', 'MAINTAINER'])('keeps %s read-only', async (role) => {
    setup('save', role)
    await screen.findByRole('table', { name: /Evidence classes/ })
    expect(screen.queryByText('Change retention policy')).not.toBeInTheDocument()
  })
})
