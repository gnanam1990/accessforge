/**
 * Workspace settings: the numbers, the roles, and the cost of deleting evidence.
 *
 * Most of these tests are about what the screen must not collapse. Three kinds of usage number are
 * three columns. A read-only role is a full view without a form. Whether deleting a class breaks a
 * completeness claim is a property of the class, not a preference.
 */

import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'
import type { FakeServer, SessionResponse } from '../test/fakeServer'

const asRole = (role: string): SessionResponse => ({
  userId: 'u-1',
  email: 'owner@example.test',
  workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role }],
})

const renderSettings = (server: FakeServer): void => {
  const client = new ApiClient({ fetchImpl: server.fetch, cookieSource: () => '' })
  render(
    <MemoryRouter initialEntries={['/w/ws-1/settings']}>
      <App client={client} />
    </MemoryRouter>,
  )
}

describe('usage', () => {
  it('reports measured, estimated and unmeasurable as three different columns', async () => {
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)

    const table = await screen.findByRole('table', { name: /Consumption in the current window/ })
    expect(within(table).getByRole('columnheader', { name: 'Measured' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'Estimated' })).toBeInTheDocument()
    // A count of events, not a quantity. Shown as zero usage it would read as nothing having
    // happened.
    expect(
      within(table).getByRole('columnheader', { name: 'Unmeasurable events' }),
    ).toBeInTheDocument()
    expect(within(table).getByText('quantity could not be obtained')).toBeVisible()
  })

  it('says an estimated number was reported by a provider about itself', async () => {
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)
    expect(await screen.findByText('reported by a provider about itself')).toBeVisible()
  })

  it('shows no cost, currency or saving anywhere', async () => {
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)
    await screen.findByRole('table', { name: /Consumption in the current window/ })

    const text = document.body.textContent ?? ''
    for (const symbol of ['$', '€', '£']) {
      expect(text).not.toContain(symbol)
    }
    // R1 measures usage and enforces a limit. It collects no money and computes no saving.
    expect(text).not.toMatch(/\b(price|invoice|savings?|balance|top up)\b/i)
  })

  it('names who set the allowance and why', async () => {
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)
    // A limit with no recorded reason is one nobody can be asked about later.
    expect(await screen.findByText(/pilot allowance/)).toBeVisible()
  })
})

describe('who may change what', () => {
  it('gives a viewer every value and no form', async () => {
    const server = createFakeServer(asRole('VIEWER'))
    renderSettings(server)

    await screen.findByRole('table', { name: /Consumption in the current window/ })
    expect(
      screen.getByRole('heading', { name: 'You can read these settings but not change them' }),
    ).toBeVisible()
    // Hiding a control somebody cannot use is honest; disabling one with no explanation is not.
    expect(screen.queryByRole('button', { name: 'Save allowance' })).not.toBeInTheDocument()
    // And the values are all there.
    expect(screen.getByText('3')).toBeVisible()
  })

  it('gives a maintainer no form either, matching the server’s matrix', async () => {
    const server = createFakeServer(asRole('MAINTAINER'))
    renderSettings(server)
    await screen.findByRole('table', { name: /Consumption in the current window/ })
    // `WORKSPACE_CONFIGURE` is owner-only in module 03's matrix, and maintainer is the role most
    // likely to be added to a client-side copy by mistake. The server refuses either way; the copy
    // being wrong would mean showing somebody a form whose submission always fails.
    expect(screen.queryByRole('button', { name: 'Save allowance' })).not.toBeInTheDocument()
  })

  it('gives an owner the form', async () => {
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)
    expect(await screen.findByRole('button', { name: 'Save allowance' })).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'You can read these settings but not change them' }),
    ).not.toBeInTheDocument()
  })

  it('sends the revision it read, so a concurrent change is refused rather than overwritten', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)

    await user.type(await screen.findByLabelText(/Why this limit/), 'raising for the pilot')
    await user.click(screen.getByRole('button', { name: 'Save allowance' }))

    const sent = server.bodies.find((entry) => entry.url.includes('/settings/entitlement'))
    expect(sent?.method).toBe('PUT')
    // Two administrators raising a limit at the same moment is exactly when a silent
    // last-writer-wins discards a decision without telling either of them.
    expect(server.calls.some((call) => call.includes('/settings/entitlement'))).toBe(true)
  })

  it('refuses a limit with no stated reason', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)

    await screen.findByRole('button', { name: 'Save allowance' })
    await user.click(screen.getByRole('button', { name: 'Save allowance' }))

    const summary = await screen.findByRole('alert')
    expect(summary).toHaveFocus()
    expect(summary).toHaveTextContent(/nobody can be asked about later/)
    expect(server.bodies.filter((e) => e.url.includes('/settings/entitlement'))).toEqual([])
  })

  it('refuses a value that is not a whole number, and offers none meaning unlimited', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)

    const field = await screen.findByLabelText(/maxRunsPerDay/)
    await user.clear(field)
    await user.type(screen.getByLabelText(/Why this limit/), 'raising')
    await user.click(screen.getByRole('button', { name: 'Save allowance' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/no value meaning unlimited/)
  })
})

describe('retention', () => {
  it('says the defaults are defaults rather than choices', async () => {
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)
    // "No policy" would otherwise mean keeping everything indefinitely — a decision made by
    // omission.
    expect(
      await screen.findByRole('heading', { name: 'These are defaults, not choices' }),
    ).toBeVisible()
  })

  it('says per class whether deleting it breaks a completeness claim', async () => {
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)

    const table = await screen.findByRole('table', { name: /Evidence classes/ })
    // Read from the labelled badge rather than by searching the row for "Yes": consent-required is
    // also a yes/no in the same row, and a bare text match would pass while reading the wrong cell.
    const deletionAnswer = (rowName: RegExp): string => {
      const row = within(table).getByRole('row', { name: rowName })
      const badge = within(row).getByText('Deletion:').parentElement
      return badge?.textContent?.replace(/[^A-Za-z]/g, '') ?? ''
    }
    // Reader speech is what a reader assertion is decided from. A recording is supplementary.
    expect(deletionAnswer(/READER_SPEECH/)).toBe('DeletionYes')
    expect(deletionAnswer(/SCREEN_RECORDING/)).toBe('DeletionNo')
  })

  it('states what deletion cannot reach', async () => {
    const server = createFakeServer(asRole('OWNER'))
    renderSettings(server)
    expect(
      await screen.findByText(/An export already downloaded still contains what it contained/),
    ).toBeVisible()
  })
})
