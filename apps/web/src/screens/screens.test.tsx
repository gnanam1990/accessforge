/**
 * The configuration-to-run path, through the real components.
 *
 * The scenarios are the ones module 22's prompt lists: authorizing a project and an environment,
 * authoring a journey and having it refused for the right reason, reading a runner inventory that
 * never claims readiness it has not proved, and requesting a run that is displayed as accepted
 * rather than passed.
 *
 * They render `App`, not the screens in isolation, so the route map, the shell, the guard and the
 * client's outcome handling are all in the path. A screen tested alone would pass with a route that
 * nothing reaches.
 */

import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'
import type { FakeServer, SessionResponse } from '../test/fakeServer'

const MEMBER: SessionResponse = {
  userId: 'u-1',
  email: 'engineer@example.test',
  workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role: 'MAINTAINER' }],
}

const renderAt = (server: FakeServer, path: string): void => {
  const client = new ApiClient({ fetchImpl: server.fetch, cookieSource: () => '' })
  render(
    <MemoryRouter initialEntries={[path]}>
      <App client={client} />
    </MemoryRouter>,
  )
}

const RUNNER = {
  runnerId: 'r-1',
  name: 'desk-1',
  status: 'OFFLINE',
  platform: 'darwin',
  profile: { readerName: 'VoiceOver', readerVersion: 'macOS 26.6' },
  leaseEpoch: 0,
  quarantineReason: null,
  revoked: false,
  preflightPassedAt: null,
  resetCount: 0,
  hasActiveLease: false,
  createdAt: '2026-09-10T00:00:00Z',
}

const MANIFEST = {
  sealedManifestId: 'm-1',
  manifestDigest: '9'.repeat(64),
  journeyDigest: '1'.repeat(64),
  assertionSetDigest: '2'.repeat(64),
  fixtureDigest: '3'.repeat(64),
  runnerProfileDigest: '5'.repeat(64),
  navigatorPolicyDigest: '4'.repeat(64),
  environmentConfigDigest: '6'.repeat(64),
  environmentName: 'Local',
  evaluatorVersion: 'evaluator-1',
  modelConfigDigest: '7'.repeat(64),
  sourceCommitSha: 'abc123',
  sourceTreeDigest: '8'.repeat(64),
  buildArtifactDigest: '0'.repeat(64),
  runId: null,
  createdAt: '2026-09-10T00:00:00Z',
}

const JOURNEY = {
  journeyVersionId: 'j-1',
  name: 'Recover from a form error',
  platform: 'darwin',
  journeyDigest: '1'.repeat(64),
  assertionSetDigest: '2'.repeat(64),
  fixtureDigest: '3'.repeat(64),
  navigatorPolicyDigest: '4'.repeat(64),
  reviewerSummary: { journey: 'Recover from a form error' },
  supersedes: null,
  supersededBy: null,
  createdAt: '2026-09-10T00:00:00Z',
}

describe('projects', () => {
  it('refuses a repository without the person who authorized it, and says why', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/projects')
    await screen.findByRole('heading', { level: 1, name: 'Projects' })

    await user.type(screen.getByLabelText(/Project name/), 'Reference app')
    await user.type(screen.getByLabelText(/Repository URL/), 'https://github.test/x/y')
    await user.click(screen.getByRole('button', { name: 'Add project' }))

    const summary = await screen.findByRole('alert')
    expect(summary).toHaveFocus()
    // The rule, stated where a person reads it rather than only enforced by the server.
    expect(summary).toHaveTextContent(/Reachability is not consent/)
    // And nothing was sent: a refused form must not have half-acted.
    expect(server.calls.filter((call) => call.startsWith('POST') && call.includes('/projects'))).toEqual(
      [],
    )
  })

  it('says so on the page that entering a URL is not permission', async () => {
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/projects')
    await screen.findByRole('heading', { level: 1, name: 'Projects' })
    // UI-UX section 2: required explanatory text cannot exist only inside a tooltip.
    expect(
      screen.getByText(/being reachable is not permission to automate against it/),
    ).toBeVisible()
  })

  it('offers real members to authorize a repository, not a free-text name', async () => {
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/projects')
    await screen.findByRole('heading', { level: 1, name: 'Projects' })

    // A name is not a record of who granted permission, and the column holds an identity. A
    // free-text field here produced an unhandled server error the first time a person typed their
    // own name into it.
    const picker = await screen.findByLabelText(/Authorized by/)
    expect(picker.tagName.toLowerCase()).toBe('select')
    // Waited for, not queried synchronously. The `<select>` renders as soon as the screen does and
    // its options arrive with the *members* request, so a synchronous query here is a race -- it
    // passed most of the time and failed roughly one run in ten, which is the worst kind of test
    // failure: real, intermittent, and easy to blame on whatever was changed last.
    await waitFor(() =>
      expect(
        within(picker).getByRole('option', { name: /engineer@example.test/ }),
      ).toBeInTheDocument(),
    )
  })

  it('creates a project and shows it in the list', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/projects')
    await screen.findByRole('heading', { level: 1, name: 'Projects' })

    await user.type(screen.getByLabelText(/Project name/), 'Reference app')
    await user.click(screen.getByRole('button', { name: 'Add project' }))

    expect(await screen.findByRole('link', { name: 'Reference app' })).toBeInTheDocument()
  })

  it('distinguishes an empty list from a failed one', async () => {
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/projects')
    // The server answered, and there is nothing here yet.
    expect(await screen.findByText(/The server answered, and there is nothing here yet/)).toBeVisible()

    server.setOffline(true)
    const other = createFakeServer(MEMBER)
    other.setOffline(true)
    renderAt(other, '/w/ws-1/projects')
    // A failed request is never an empty result.
    expect(
      await screen.findByRole('heading', { name: /No connection to the server/ }),
    ).toBeVisible()
  })
})

describe('environments', () => {
  it('refuses one identity that both resets state and attests to it', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    server.data.projects.push({
      projectId: 'p-1',
      name: 'Reference app',
      repositoryUrl: null,
      createdAt: '2026-09-10T00:00:00Z',
    })
    renderAt(server, '/w/ws-1/projects/p-1')
    await screen.findByRole('heading', { level: 1, name: 'Reference app' })

    await user.type(screen.getByLabelText(/Environment name/), 'Local')
    await user.type(screen.getByLabelText(/Permitted origins/), 'https://localhost:8443')
    await user.type(screen.getByLabelText(/Observer credential reference/), 'vault://same')
    await user.type(screen.getByLabelText(/Reset credential reference/), 'vault://same')
    await user.type(screen.getByLabelText(/Authorization expires/), '2027-01-01T00:00')
    await user.click(screen.getByRole('button', { name: 'Authorize environment' }))

    const summary = await screen.findByRole('alert')
    expect(summary).toHaveTextContent(/not an independent observer/)
  })

  it('names each blank credential reference instead of reporting them as equal', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    server.data.projects.push({
      projectId: 'p-1',
      name: 'Reference app',
      repositoryUrl: null,
      createdAt: '2026-09-10T00:00:00Z',
    })
    renderAt(server, '/w/ws-1/projects/p-1')
    await screen.findByRole('heading', { level: 1, name: 'Reference app' })

    await user.type(screen.getByLabelText(/Environment name/), 'Local')
    await user.type(screen.getByLabelText(/Permitted origins/), 'https://localhost:8443')
    await user.type(screen.getByLabelText(/Authorization expires/), '2027-01-01T00:00')
    await user.click(screen.getByRole('button', { name: 'Authorize environment' }))

    const summary = await screen.findByRole('alert')
    // Two blank references are equal, so the server refused them as "not an independent observer" —
    // accurate about the comparison and wrong about the cause. One blank reference passed the
    // domain entirely and was persisted empty.
    expect(summary).toHaveTextContent(/the independent observer reads with/)
    expect(summary).toHaveTextContent(/resets fixture state/)
    expect(summary.textContent).not.toMatch(/not an independent observer/)
  })

  it('states that authorizing is not a connection test', async () => {
    const server = createFakeServer(MEMBER)
    server.data.projects.push({
      projectId: 'p-1',
      name: 'Reference app',
      repositoryUrl: null,
      createdAt: '2026-09-10T00:00:00Z',
    })
    renderAt(server, '/w/ws-1/projects/p-1')
    expect(
      await screen.findByRole('heading', {
        name: 'This records permission; it does not test a connection',
      }),
    ).toBeVisible()
  })

  it('names why an environment is unusable rather than reporting a bare no', async () => {
    const server = createFakeServer(MEMBER)
    server.data.projects.push({
      projectId: 'p-1',
      name: 'Reference app',
      repositoryUrl: null,
      createdAt: '2026-09-10T00:00:00Z',
    })
    server.data.environments.push({
      environmentId: 'e-1',
      name: 'Staging',
      allowedOrigins: ['https://staging.test'],
      fixtureResetStrategy: 'TRUNCATE_AND_SEED',
      permittedEffects: ['FIXTURE_SUBMIT'],
      configDigest: 'a'.repeat(64),
      expiresAt: '2020-01-01T00:00:00Z',
      revoked: false,
      supersededBy: null,
      expired: true,
      usable: false,
    })
    renderAt(server, '/w/ws-1/projects/p-1')
    // "Expired", "revoked" and "superseded" lead an operator to three different actions.
    expect(await screen.findByText('Authorization expired')).toBeVisible()
  })
})

describe('journey authoring', () => {
  const openProject = async (server: FakeServer): Promise<void> => {
    server.data.projects.push({
      projectId: 'p-1',
      name: 'Reference app',
      repositoryUrl: null,
      createdAt: '2026-09-10T00:00:00Z',
    })
    renderAt(server, '/w/ws-1/projects/p-1')
    await screen.findByRole('heading', { level: 1, name: 'Reference app' })
  }

  it('offers only the actions the server permits', async () => {
    const server = createFakeServer(MEMBER)
    await openProject(server)
    const group = await screen.findByRole('group', { name: 'Permitted actions' })
    // Read from the server rather than hard-coded here: a client-side copy of the policy is a
    // second definition of it, and an author offered a control the server refuses is invited to
    // fail.
    expect(within(group).getByRole('checkbox', { name: 'TYPE_TEXT' })).toBeInTheDocument()
    expect(within(group).queryByRole('checkbox', { name: 'OPEN_TERMINAL' })).not.toBeInTheDocument()
  })

  it('offers only key chords for the chosen platform, and clears them when it changes', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openProject(server)

    const chords = await screen.findByRole('group', { name: 'Permitted key chords' })
    await user.click(within(chords).getByRole('checkbox', { name: 'CTRL+OPT+RIGHT' }))
    expect(within(chords).getByRole('checkbox', { name: 'CTRL+OPT+RIGHT' })).toBeChecked()

    await user.selectOptions(screen.getByLabelText(/^Platform/), 'win32')
    expect(
      within(screen.getByRole('group', { name: 'Permitted key chords' })).queryByRole('checkbox', {
        name: 'CTRL+OPT+RIGHT',
      }),
    ).not.toBeInTheDocument()

    // Gone from the screen is not the same as gone from the request. A chord the new platform does
    // not permit, still selected in state, produces a refusal about a control the author can no
    // longer see — so the submitted body is what this asserts.
    await user.type(screen.getByLabelText(/Journey name/), 'Recover from a form error')
    await user.type(screen.getByLabelText(/trying to do/), 'Submit the contact form')
    await user.type(screen.getByLabelText(/Start address/), 'https://localhost:8443/contact')
    await user.type(screen.getByLabelText(/count as having succeeded/), 'The form is received')
    await user.type(screen.getByLabelText(/Fixture template/), 'contact-form')
    await user.type(screen.getByLabelText(/Assertion 1 description/), 'One submission recorded')
    await user.click(screen.getByRole('button', { name: 'Freeze version' }))

    const sent = server.bodies.find((entry) => entry.url.endsWith('/journeys'))
    expect((sent?.body as { allowedKeyChords?: string[] }).allowedKeyChords).not.toContain(
      'CTRL+OPT+RIGHT',
    )
  })

  it('refuses a budget the browser would have sent as zero or null', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openProject(server)

    await user.type(screen.getByLabelText(/Journey name/), 'Recover from a form error')
    await user.type(screen.getByLabelText(/trying to do/), 'Submit the contact form')
    await user.type(screen.getByLabelText(/Start address/), 'https://localhost:8443/contact')
    await user.type(screen.getByLabelText(/count as having succeeded/), 'The form is received')
    await user.type(screen.getByLabelText(/Fixture template/), 'contact-form')
    await user.type(screen.getByLabelText(/Assertion 1 description/), 'One submission recorded')
    await user.clear(screen.getByLabelText(/Maximum actions/))
    await user.click(screen.getByRole('button', { name: 'Freeze version' }))

    // The form is `noValidate`, so `min` and `max` block nothing. `Number('')` is 0, which the
    // server then refuses with a message about a value the author could have corrected here.
    const summary = await screen.findByRole('alert')
    expect(summary).toHaveTextContent(/Maximum actions must be a whole number between 1 and 500/)
    expect(server.bodies.filter((entry) => entry.url.endsWith('/journeys'))).toEqual([])
  })

  it('links a failed submission to the fields it is about', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openProject(server)

    await user.click(screen.getByRole('button', { name: 'Freeze version' }))
    const summary = await screen.findByRole('alert')
    expect(summary).toHaveFocus()

    const links = within(summary).getAllByRole('link')
    expect(links.length).toBeGreaterThan(1)
    // Each entry reaches a real control. A message with nowhere to go is one somebody has to guess
    // at.
    for (const link of links) {
      const target = (link.getAttribute('href') ?? '').slice(1)
      expect(document.getElementById(target), target).not.toBeNull()
    }
  })

  it('renders an unsupported capability as a capability problem, not a typing mistake', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openProject(server)
    server.refuseWrite(
      '/journeys',
      422,
      'UNSUPPORTED_CAPABILITY',
      'actions [OPEN_TERMINAL] are not in the allowlist',
    )

    await user.type(screen.getByLabelText(/Journey name/), 'Recover from a form error')
    await user.type(screen.getByLabelText(/trying to do/), 'Submit the contact form')
    await user.type(screen.getByLabelText(/Start address/), 'https://localhost:8443/contact')
    await user.type(screen.getByLabelText(/count as having succeeded/), 'The form is received')
    await user.type(screen.getByLabelText(/Fixture template/), 'contact-form')
    await user.type(screen.getByLabelText(/Assertion 1 description/), 'One submission recorded')
    await user.click(screen.getByRole('button', { name: 'Freeze version' }))

    // 422 is a different conversation from 400: the request is well formed and the capability does
    // not exist.
    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent(/AccessForge cannot do what this journey asks/)
    expect(notice).toHaveTextContent(/not a mistake in what you typed/)
  })

  it('shows what freezing sealed, and that it authorizes nothing to run', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openProject(server)

    await user.type(screen.getByLabelText(/Journey name/), 'Recover from a form error')
    await user.type(screen.getByLabelText(/trying to do/), 'Submit the contact form')
    await user.type(screen.getByLabelText(/Start address/), 'https://localhost:8443/contact')
    await user.type(screen.getByLabelText(/count as having succeeded/), 'The form is received')
    await user.type(screen.getByLabelText(/Fixture template/), 'contact-form')
    await user.type(screen.getByLabelText(/Assertion 1 description/), 'One submission recorded')
    await user.click(screen.getByRole('button', { name: 'Freeze version' }))

    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('This version is frozen and authorizes nothing to run.')
    // The complete digest, not a prefix: a digest shown truncated is one nobody can compare.
    expect(notice).toHaveTextContent('c'.repeat(64))
  })

  it('says that freezing and running are separate actions', async () => {
    const server = createFakeServer(MEMBER)
    await openProject(server)
    expect(
      await screen.findByText(/Freezing seals this journey and authorizes nothing to execute/),
    ).toBeVisible()
  })
})

describe('the runner inventory', () => {
  it('renders a never-passed preflight as "Never", not as a blank', async () => {
    const server = createFakeServer(MEMBER)
    server.data.runners.push({
      runnerId: 'r-1',
      name: 'desk-1',
      status: 'OFFLINE',
      platform: 'darwin',
      profile: { readerName: 'VoiceOver', readerVersion: 'macOS 26.6' },
      leaseEpoch: 0,
      quarantineReason: null,
      revoked: false,
      preflightPassedAt: null,
      resetCount: 0,
      hasActiveLease: false,
      createdAt: '2026-09-10T00:00:00Z',
    })
    renderAt(server, '/w/ws-1/runners')

    await screen.findByRole('heading', { level: 1, name: 'Runners' })
    // A blank cell reads as missing data rather than as the fact that this desktop has never proved
    // a reader was running on it.
    expect(await screen.findByText('Never')).toBeVisible()
  })

  it('does not render a missing reader version as the word null', async () => {
    const server = createFakeServer(MEMBER)
    server.data.runners.push({
      ...RUNNER,
      profile: { readerName: 'VoiceOver', readerVersion: null as unknown as string },
    })
    renderAt(server, '/w/ws-1/runners')

    // Scoped to the inventory: the support matrix on the same screen also names VoiceOver.
    const inventory = await screen.findByRole('table', { name: /Enrolled runners/ })
    expect(within(inventory).getByText('VoiceOver')).toBeVisible()
    // `=== undefined` is false for null, so the template interpolated the value and printed
    // " null" beside the reader's name.
    expect(inventory.textContent).not.toMatch(/VoiceOver null/)
  })

  it('follows the cursor rather than showing the first page as the inventory', async () => {
    const server = createFakeServer(MEMBER)
    server.data.runners.push(RUNNER, { ...RUNNER, runnerId: 'r-2', name: 'desk-2' })
    server.setRunnerPaging('paged')
    renderAt(server, '/w/ws-1/runners')

    await screen.findByRole('heading', { level: 1, name: 'Runners' })
    // One page rendered under the heading "Runners" is a claim that there are no more.
    expect(await screen.findByText('desk-1')).toBeVisible()
    expect(await screen.findByText('desk-2')).toBeVisible()
  })

  it('says so when it stopped following the cursor before the server ran out', async () => {
    const server = createFakeServer(MEMBER)
    server.data.runners.push(RUNNER)
    server.setRunnerPaging('endless')
    renderAt(server, '/w/ws-1/runners')

    // A bound has to exist, or one screen becomes an unbounded number of requests. Reaching it must
    // be visible: a runner that is not shown may still be holding a desktop.
    expect(await screen.findByRole('heading', { name: 'This list is not complete' })).toBeVisible()
  })

  it('repeats the server’s statement about what READY means', async () => {
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/runners')
    expect(
      await screen.findByText(/not inferred from the runner process being reachable/),
    ).toBeVisible()
  })

  it('discloses the untested and unavailable reader matrix', async () => {
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/runners')
    await screen.findByRole('heading', { level: 1, name: 'Runners' })

    const matrix = screen.getByRole('table', {
      name: /Reader and platform matrix/,
    })
    // A screen offering three profiles without saying which are verified invites an operator to
    // choose one that cannot run.
    expect(within(matrix).getByText(/No real VoiceOver trace has been captured/)).toBeVisible()
    expect(within(matrix).getByText(/belongs to module 09 and is not implemented/)).toBeVisible()
  })
})

describe('requesting a run', () => {
  const openJourney = async (server: FakeServer): Promise<void> => {
    server.data.journeyVersions.push(JOURNEY)
    server.data.sealedManifests.push(MANIFEST)
    renderAt(server, '/w/ws-1/projects/p-1/journeys/j-1')
    await screen.findByRole('heading', { level: 1, name: 'Recover from a form error' })
  }

  it('refuses to request a run when nothing has been sealed for this version', async () => {
    const server = createFakeServer(MEMBER)
    server.data.journeyVersions.push(JOURNEY)
    renderAt(server, '/w/ws-1/projects/p-1/journeys/j-1')
    await screen.findByRole('heading', { level: 1, name: 'Recover from a form error' })

    // A run is requested against a sealed manifest. Sending the journey digest in that field queues
    // a run whose identity matches nothing, and dispatch would refuse it later for naming a
    // different sealed manifest — so the control is absent and the reason is on the page.
    expect(
      await screen.findByRole('heading', { name: 'This version cannot be run yet' }),
    ).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Request a run…' })).not.toBeInTheDocument()
    expect(screen.getByText(/Sealing needs a recorded source snapshot/)).toBeVisible()
  })

  it('sends the sealed manifest digest, not the journey digest', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openJourney(server)

    await user.click(screen.getByRole('button', { name: 'Request a run…' }))
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Request a run' }),
    )

    const sent = server.bodies.find((entry) => entry.url.endsWith('/runs'))
    expect((sent?.body as { manifestDigest?: string }).manifestDigest).toBe(MANIFEST.manifestDigest)
    expect((sent?.body as { manifestDigest?: string }).manifestDigest).not.toBe(
      JOURNEY.journeyDigest,
    )
  })

  it('keeps the dialog and the key when the server did not answer', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openJourney(server)

    await user.click(screen.getByRole('button', { name: 'Request a run…' }))
    server.setOffline(true)
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Request a run' }),
    )

    const alert = await screen.findByRole('alert')
    // It cannot say nothing was changed: the request may have arrived and the answer been lost.
    expect(alert).toHaveTextContent(/may have received the request/)
    // It must not claim the request had no effect, in any wording. Only the server knows.
    expect(alert.textContent).not.toMatch(/nothing was (changed|created)/i)

    server.setOffline(false)
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: 'Request a run' }),
    )
    await screen.findByRole('status')

    const keys = server.bodies
      .filter((entry) => entry.url.endsWith('/runs'))
      .map((entry) => entry.idempotencyKey)
    // Two attempts, one key. A fresh key on the retry would turn one authorization into two runs.
    expect(keys).toHaveLength(2)
    expect(keys[0]).toBe(keys[1])
    expect(keys[0]).not.toBeUndefined()
  })

  it('shows the exact digests in the confirmation, in full', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openJourney(server)

    await user.click(screen.getByRole('button', { name: 'Request a run…' }))
    const dialog = await screen.findByRole('dialog', {
      name: 'Request a run of this journey version',
    })
    expect(within(dialog).getByText(MANIFEST.manifestDigest)).toBeVisible()
    expect(within(dialog).getByText('3'.repeat(64))).toBeVisible()
    expect(within(dialog).getByText(/does not approve a repair, merge anything/)).toBeVisible()
  })

  it('names its consequences instead of offering OK and Cancel', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openJourney(server)

    await user.click(screen.getByRole('button', { name: 'Request a run…' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Request a run' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Do not run' })).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: /^(OK|Cancel|Confirm)$/ })).toBeNull()
  })

  it('displays 202 as accepted, never as passed', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openJourney(server)

    await user.click(screen.getByRole('button', { name: 'Request a run…' }))
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Request a run' }),
    )

    const notice = await screen.findByRole('status')
    expect(notice).toHaveTextContent('QUEUED')
    expect(notice).toHaveTextContent('NOT_EVALUATED')
    expect(notice).toHaveTextContent(/nothing has been established/)
    // Nothing anywhere on the page says the run passed, succeeded or is complete.
    expect(notice.textContent).not.toMatch(/passed|succeeded|complete/i)
  })

  it('sends one operation when the confirmation is pressed twice', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openJourney(server)

    await user.click(screen.getByRole('button', { name: 'Request a run…' }))
    const confirm = within(await screen.findByRole('dialog')).getByRole('button', {
      name: 'Request a run',
    })
    // Two clicks with nothing awaited between them, so both handlers run before React re-renders.
    // `user.dblClick` lets the first render complete, which the cleared idempotency key already
    // covers; only the synchronous ref guard can stop this one.
    fireEvent.click(confirm)
    fireEvent.click(confirm)

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent('QUEUED')
    })
    // One request, not two. `busy` is state, so it has not re-rendered by the time the second click
    // arrives; the guard has to be a ref, checked synchronously. The idempotency key would make the
    // second request harmless at the server, and sending it anyway would still be the client
    // dispatching an approval twice.
    expect(server.calls.filter((call) => call === 'POST /v1/workspaces/ws-1/runs')).toHaveLength(1)
  })

  it('reports a refused dispatch instead of implying the run started', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openJourney(server)
    server.refuseWrite(
      '/runs',
      409,
      'CONFLICT',
      'every runner for VoiceOver on darwin is quarantined',
    )

    await user.click(screen.getByRole('button', { name: 'Request a run…' }))
    await user.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Request a run' }),
    )

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The run was not requested')
    expect(alert).toHaveTextContent(/quarantined/)
  })

  it('marks a superseded version rather than letting it look current', async () => {
    const server = createFakeServer(MEMBER)
    server.data.journeyVersions.push({ ...JOURNEY, supersededBy: 'j-2' })
    renderAt(server, '/w/ws-1/projects/p-1/journeys/j-1')
    expect(
      await screen.findByRole('heading', { name: 'A later version has replaced this one' }),
    ).toBeVisible()
  })

  it('shows the navigator policy in full, so the boundary is checkable', async () => {
    const server = createFakeServer(MEMBER)
    await openJourney(server)
    expect(await screen.findByText(/"taskSummary": "Submit the form"/)).toBeVisible()
  })
})
