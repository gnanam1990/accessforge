/**
 * Standing authorizations and schedules, as a settings section.
 *
 * These tests are about what the screen must not collapse or do for you.
 *
 * The central one is `recovering from a restore`. Reconciliation marks every restored grant as
 * needing revalidation and moves its revision, which also stops every schedule bound to it.
 * Confirming the grant must **not** restart the schedules: "this standing authorization is still
 * valid" and "this recurring job should start running again" are different decisions, and one button
 * that did both would restart work nobody asked to restart — overnight, against a real desktop, on
 * one click during an incident.
 *
 * The rest guard distinctions a convenient screen would flatten: "awaiting revalidation" is neither
 * usable nor revoked, a revoked grant stays in the list, a paused schedule is not a deleted one, and
 * a schedule whose grant has moved is stopped for a reason a reader can see.
 */

import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'
import type { FakeServer, SessionResponse } from '../test/fakeServer'
import type { ExecutionGrant, Schedule } from '../api/resources'

const OWNER: SessionResponse = {
  userId: 'u-1',
  email: 'owner@example.test',
  workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role: 'OWNER' }],
}

const grant = (overrides: Partial<ExecutionGrant> = {}): ExecutionGrant => ({
  grantId: 'grant-0001-aaaa',
  projectId: 'p-1',
  environment: 'staging',
  allowedJourneyVersionIds: ['j-1'],
  allowedPolicyVersionIds: ['pol-1'],
  permittedEffects: [],
  actionBudget: 100,
  wallTimeBudgetSeconds: 600,
  expiresAt: '2027-01-01T00:00:00Z',
  revision: 1,
  revoked: false,
  revokedAt: null,
  createdAt: '2026-09-01T00:00:00Z',
  revalidationRequired: false,
  revalidatedAt: null,
  revalidatedBy: null,
  usable: true,
  unusableBecause: null,
  authorizes: 'Runs of the listed journey versions in the named environment, and nothing else.',
  ...overrides,
})

const schedule = (overrides: Partial<Schedule> = {}): Schedule => ({
  scheduleId: 'sched-0001',
  name: 'nightly',
  grantId: 'grant-0001-aaaa',
  grantRevisionAtApproval: 1,
  journeyVersionId: 'j-1',
  sourceRef: 'main',
  cronExpression: '0 2 * * *',
  timezone: 'Europe/London',
  expiresAt: '2027-01-01T00:00:00Z',
  pausedAt: null,
  pausedBy: null,
  revision: 1,
  createdBy: 'u-1',
  createdAt: '2026-09-01T00:00:00Z',
  reapprovedAt: null,
  reapprovedBy: null,
  meaning: 'A schedule requests runs; it does not run anything.',
  // `...overrides` last. It was missing from the first version of this helper, so every override a
  // test passed was silently discarded -- two tests failed for reasons that had nothing to do with
  // the screen, and a third would have passed while exercising the default fixture instead of the
  // case it named.
  ...overrides,
})

const renderSettings = (server: FakeServer): void => {
  const client = new ApiClient({ fetchImpl: server.fetch, cookieSource: () => '' })
  render(
    <MemoryRouter initialEntries={['/w/ws-1/settings']}>
      <App client={client} />
    </MemoryRouter>,
  )
}

const grantsTable = async (): Promise<HTMLElement> =>
  screen.findByRole('table', { name: /Standing execution grants/ })

const schedulesTable = async (): Promise<HTMLElement> =>
  screen.findByRole('table', { name: /Schedules, the grant each draws on/ })

describe('standing authorizations', () => {
  it('appears under settings, where the route table puts it', async () => {
    // Not a route of its own. UI-UX section 3 lists schedules under /settings, and the first version
    // of this screen added a /schedules path -- which the route-table test caught, because inventing
    // a path the specification does not list is what module 21's gate forbids.
    const server = createFakeServer(OWNER)
    server.data.grants = [grant()]
    renderSettings(server)
    expect(await screen.findByRole('heading', { name: /Schedules and standing authorizations/ })).toBeVisible()
  })

  it('says what a grant does and does not authorize', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant()]
    renderSettings(server)
    await grantsTable()
    expect(
      screen.getByText(/never authorizes a repair or a publication/),
    ).toBeVisible()
  })

  it('distinguishes awaiting revalidation from revoked', async () => {
    // Three states, not two. Nobody withdrew a restored grant, and nobody can vouch for it either;
    // collapsing that into "revoked" loses the difference between a decision and the absence of one.
    const server = createFakeServer(OWNER)
    server.data.grants = [
      grant({ grantId: 'g-restored', revalidationRequired: true, usable: false, unusableBecause: 'restored from a backup and has not been revalidated' }),
      grant({ grantId: 'g-revoked', revoked: true, revokedAt: '2026-09-02T00:00:00Z', usable: false, unusableBecause: 'has been revoked' }),
    ]
    renderSettings(server)

    const table = await grantsTable()
    expect(within(table).getByText(/Awaiting revalidation/)).toBeVisible()
    expect(within(table).getByText(/Revoked/)).toBeVisible()
  })

  it('keeps a revoked grant in the list', async () => {
    // "What was allowed to run, and when did that stop" needs both halves.
    const server = createFakeServer(OWNER)
    server.data.grants = [grant({ revoked: true, revokedAt: '2026-09-02T00:00:00Z', usable: false })]
    renderSettings(server)

    const table = await grantsTable()
    expect(within(table).getAllByRole('row')).toHaveLength(2)
    expect(within(table).getByText(/Revoked/)).toBeVisible()
  })

  it('shows the server’s own reason a grant cannot be used', async () => {
    // Not re-derived here. A screen with its own idea of usability eventually disagrees with the
    // code that enforces it, and the disagreement shows up as a grant this page calls usable and
    // every run refuses.
    const server = createFakeServer(OWNER)
    server.data.grants = [
      grant({ usable: false, unusableBecause: 'execution grant expired at 2026-09-01' }),
    ]
    renderSettings(server)

    const table = await grantsTable()
    expect(within(table).getByText('execution grant expired at 2026-09-01')).toBeVisible()
  })

  it('offers no confirm control for a grant nobody restored', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant()]
    renderSettings(server)

    const table = await grantsTable()
    expect(within(table).queryByRole('button', { name: /Confirm still authorized/ })).toBeNull()
    expect(within(table).getByRole('button', { name: 'Revoke' })).toBeVisible()
  })

  it('offers no revoke control for a grant already revoked', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant({ revoked: true, usable: false })]
    renderSettings(server)

    const table = await grantsTable()
    expect(within(table).queryByRole('button', { name: 'Revoke' })).toBeNull()
  })

  it('shows the server’s refusal verbatim rather than paraphrasing it', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant({ revalidationRequired: true, usable: false })]
    server.refuseWrite(
      'revalidations',
      409,
      'STALE_REVISION',
      'this grant is at revision 3 and you supplied 1; it changed since you read it',
    )
    renderSettings(server)

    const table = await grantsTable()
    await userEvent.click(within(table).getByRole('button', { name: /Confirm still authorized/ }))

    expect(
      await screen.findByText(/it changed since you read it/),
    ).toBeVisible()
  })
})

describe('schedules', () => {
  it('keeps a paused schedule in the list rather than making it look deleted', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant()]
    server.data.schedules = [schedule({ pausedAt: '2026-09-02T00:00:00Z', pausedBy: 'u-1' })]
    renderSettings(server)

    const table = await schedulesTable()
    expect(within(table).getByText(/Paused/)).toBeVisible()
    expect(within(table).getByRole('button', { name: 'Resume' })).toBeVisible()
  })

  it('says a schedule stopped because its grant moved, and shows both revisions', async () => {
    // The comparison is the explanation. Showing only the approved revision hides why it stopped;
    // showing only the current one hides what was actually approved.
    const server = createFakeServer(OWNER)
    server.data.grants = [grant({ revision: 4 })]
    server.data.schedules = [schedule({ grantRevisionAtApproval: 1 })]
    renderSettings(server)

    const table = await schedulesTable()
    expect(within(table).getByText(/Stopped — grant moved/)).toBeVisible()
    expect(within(table).getByText('approved at 1, now 4')).toBeVisible()
  })

  it('says a schedule is stopped when its grant is unusable', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant({ usable: false, unusableBecause: 'has been revoked', revoked: true })]
    server.data.schedules = [schedule()]
    renderSettings(server)

    const table = await schedulesTable()
    expect(within(table).getByText(/Stopped — grant unusable/)).toBeVisible()
  })

  it('does not claim a grant is revoked when it simply cannot be read', async () => {
    // Different facts. A schedule whose grant is invisible to this reader is not a schedule whose
    // grant was withdrawn, and presenting the second as the first would be inventing a decision.
    const server = createFakeServer(OWNER)
    server.data.grants = []
    server.data.schedules = [schedule({ grantId: 'a-grant-we-cannot-see' })]
    renderSettings(server)

    const table = await schedulesTable()
    expect(within(table).getByText(/Grant not visible/)).toBeVisible()
  })

  it('says resuming is not the same as running', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant()]
    server.data.schedules = [schedule()]
    renderSettings(server)

    await schedulesTable()
    expect(screen.getByText(/still skips, and the skip is recorded with its reason/)).toBeVisible()
    expect(screen.getByText(/makes a desktop available/)).toBeVisible()
  })
})

describe('recovering from a restore', () => {
  it('needs two separate confirmations, and the schedule’s one appears only after the grant’s', async () => {
    // The test this screen exists for. A single control that revalidated a grant and resumed its
    // schedules would restart work nobody asked to restart.
    const server = createFakeServer(OWNER)
    server.data.grants = [
      grant({
        revision: 3,
        revalidationRequired: true,
        usable: false,
        unusableBecause: 'restored from a backup and has not been revalidated',
      }),
    ]
    server.data.schedules = [schedule({ grantRevisionAtApproval: 1 })]
    renderSettings(server)

    // While the grant is unusable, the schedule offers no re-approval: doing it first cannot work,
    // and offering it would invite an operator to try and be refused.
    const schedules = await schedulesTable()
    expect(within(schedules).getByText(/Stopped — grant unusable/)).toBeVisible()
    expect(within(schedules).queryByRole('button', { name: 'Re-approve' })).toBeNull()

    // The grant's own control is offered, and the page says confirming it will not restart anything.
    const grants = await grantsTable()
    expect(within(grants).getByRole('button', { name: /Confirm still authorized/ })).toBeVisible()
    expect(
      screen.getByText(/does not restart the schedules that draw on it/),
    ).toBeVisible()
  })

  it('offers the schedule’s re-approval once its grant is usable and the revision has moved', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant({ revision: 4 })]
    server.data.schedules = [schedule({ grantRevisionAtApproval: 1 })]
    renderSettings(server)

    const table = await schedulesTable()
    expect(within(table).getByRole('button', { name: 'Re-approve' })).toBeVisible()
  })

  it('offers no re-approval when the schedule is already bound to the current revision', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant({ revision: 4 })]
    server.data.schedules = [schedule({ grantRevisionAtApproval: 4 })]
    renderSettings(server)

    const table = await schedulesTable()
    expect(within(table).queryByRole('button', { name: 'Re-approve' })).toBeNull()
    expect(within(table).getByText(/Active/)).toBeVisible()
  })

  it('explains why a restored grant cannot be trusted on its own', async () => {
    const server = createFakeServer(OWNER)
    server.data.grants = [grant({ revalidationRequired: true, usable: false })]
    renderSettings(server)

    await grantsTable()
    expect(
      screen.getByText(/live in restored data and revoked in the world/),
    ).toBeVisible()
  })
})
