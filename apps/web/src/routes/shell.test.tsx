/**
 * The shell: routing, the authenticated gate, tenancy, focus and the session's end.
 *
 * The scenarios are the ones module 21's prompt lists under required verification — unauthenticated
 * routing, membership revocation, rapid workspace switching, a stale response arriving after logout,
 * denied data, an API failure, keyboard-only operation and failed form focus. Each is here because
 * the comfortable version of the same code passes a happy-path test and fails a person.
 */

import { MemoryRouter } from 'react-router-dom'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'

import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'
import type { FakeServer, SessionResponse } from '../test/fakeServer'

const MEMBER: SessionResponse = {
  userId: 'u-1',
  email: 'engineer@example.test',
  workspaces: [
    { workspaceId: 'ws-alder', name: 'Alder', role: 'OWNER' },
    { workspaceId: 'ws-zebra', name: 'Zebra', role: 'VIEWER' },
  ],
}

const renderApp = (
  server: FakeServer,
  entries: string[] = ['/'],
): { readonly client: ApiClient } => {
  const client = new ApiClient({ fetchImpl: server.fetch, cookieSource: () => '' })
  render(
    <MemoryRouter initialEntries={entries}>
      <App client={client} />
    </MemoryRouter>,
  )
  return { client }
}

beforeEach(() => {
  document.documentElement.removeAttribute('data-theme')
  window.localStorage.clear()
})

describe('the authenticated gate', () => {
  it('shows neither a sign-in form nor the shell while it is still checking', async () => {
    const server = createFakeServer(MEMBER)
    server.holdSession()
    renderApp(server)

    expect(await screen.findByRole('status')).toHaveTextContent('Loading your session…')
    // A sign-in form here would flash on every page load and be gone before anyone could read it.
    expect(screen.queryByLabelText('Email address')).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Workspace sections' })).not.toBeInTheDocument()

    server.releaseSession()
    await screen.findByRole('heading', { name: 'Choose a workspace' })
  })

  it('sends an unauthenticated visitor to sign-in, whatever they asked for', async () => {
    const server = createFakeServer(null)
    renderApp(server, ['/w/ws-alder/runners'])

    expect(await screen.findByLabelText(/Email address/)).toBeInTheDocument()
    // Nothing about the workspace appears. Rendering the chrome around a sign-in form would show a
    // workspace name to somebody with no session.
    expect(screen.queryByText('Alder')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Runners' })).not.toBeInTheDocument()
  })

  it('offers no sign-in form when the server cannot reach its own dependencies', async () => {
    const server = createFakeServer(MEMBER)
    server.setOffline(true)
    renderApp(server)

    expect(await screen.findByRole('heading', { name: /No connection to the server/ })).toBeVisible()
    // The person may well be signed in. Asking them to sign in again answers a question they did
    // not ask, and their credentials were never the problem.
    expect(screen.queryByLabelText(/Email address/)).not.toBeInTheDocument()
  })

  it('recovers when the connection returns', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    server.setOffline(true)
    renderApp(server)

    await screen.findByRole('heading', { name: /No connection to the server/ })
    server.setOffline(false)
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await screen.findByRole('heading', { name: 'Choose a workspace' })
  })
})

describe('sign-in', () => {
  it('moves focus once to the error summary after a failed submission, and keeps the value', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(null)
    server.setSignInOutcome('refused')
    renderApp(server)

    const field = await screen.findByLabelText(/Email address/)
    await user.type(field, 'engineer@example.test')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    const summary = await screen.findByRole('alert')
    expect(summary).toHaveFocus()
    expect(within(summary).getByRole('link', { name: /sign-in was refused/ })).toBeInTheDocument()
    // The value survives. Clearing the field on failure makes a person retype it every attempt.
    expect(screen.getByLabelText(/Email address/)).toHaveValue('engineer@example.test')
  })

  it('does not validate or move focus when the field merely loses focus', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(null)
    renderApp(server)

    const field = await screen.findByLabelText(/Email address/)
    await user.click(field)
    await user.tab()

    // Validating on blur and moving focus makes a form impossible to complete with a keyboard.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('reports an empty submission as a field error rather than sending it', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(null)
    renderApp(server)

    await screen.findByLabelText(/Email address/)
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveFocus()
    expect(server.calls.filter((call) => call.includes('POST'))).toHaveLength(0)
  })

  it('keeps the form and the typed address when the sign-in request cannot reach the server', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(null)
    renderApp(server)

    const field = await screen.findByLabelText(/Email address/)
    await user.type(field, 'engineer@example.test')
    server.setOffline(true)
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    // Promoting this to the shared session state unmounts the sign-in screen, which takes the
    // address the person just typed and the explanation with it.
    expect(await screen.findByRole('alert')).toHaveTextContent(/did not reach the server/)
    expect(screen.getByLabelText(/Email address/)).toHaveValue('engineer@example.test')
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('replaces the form with an explanation when the deployment has no identity provider', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(null)
    server.setSignInOutcome('no-provider')
    renderApp(server)

    await user.type(await screen.findByLabelText(/Email address/), 'engineer@example.test')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    await screen.findByRole('heading', { name: /A service this depends on is unavailable/ })
    // A form that could never succeed would make the person conclude their credentials were wrong.
    expect(screen.queryByRole('button', { name: 'Sign in' })).not.toBeInTheDocument()
  })
})

describe('the workspace shell', () => {
  it('builds the navigation from the memberships the server reported', async () => {
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])

    await screen.findByRole('heading', { level: 1, name: 'Overview' })
    const nav = screen.getByRole('navigation', { name: 'Workspace sections' })
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'Overview',
      'Projects',
      'Runners',
      'Settings',
    ])

    const switcher = screen.getByLabelText('Workspace')
    expect(within(switcher).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Alder (owner)',
      'Zebra (viewer)',
    ])
  })

  it('marks only the exact page as current, not every page beneath it', async () => {
    const server = createFakeServer(MEMBER)
    server.data.projects.push({
      projectId: 'p-1',
      name: 'Reference app',
      repositoryUrl: null,
      createdAt: '2026-09-10T00:00:00Z',
    })
    renderApp(server, ['/w/ws-alder/projects/p-1'])
    await screen.findByRole('heading', { level: 1, name: 'Reference app' })

    const nav = screen.getByRole('navigation', { name: 'Workspace sections' })
    // `aria-current` is how a screen-reader user establishes where they are. Marking Projects while
    // the project detail screen is showing tells them they are somewhere they are not.
    expect(
      within(nav)
        .getAllByRole('link')
        .filter((link) => link.getAttribute('aria-current') === 'page'),
    ).toHaveLength(0)
  })

  it('shows breadcrumbs that end at the current page without linking to it', async () => {
    const server = createFakeServer(MEMBER)
    server.data.projects.push({
      projectId: 'p-1',
      name: 'Reference app',
      repositoryUrl: null,
      createdAt: '2026-09-10T00:00:00Z',
    })
    renderApp(server, ['/w/ws-alder/projects/p-1'])

    const trail = await screen.findByRole('navigation', { name: 'Breadcrumb' })
    expect(within(trail).getAllByRole('listitem').map((item) => item.textContent?.trim())).toEqual([
      'Alder',
      'Projects',
      'Project',
    ])
    // The current page is present and is not a link: a link to where you already are is a trap for
    // anyone navigating by links.
    expect(within(trail).queryByRole('link', { name: 'Project' })).not.toBeInTheDocument()
  })

  it('never puts an identifier in the breadcrumb text', async () => {
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/runs/1f0c9e6a-0000-4000-8000-000000000001'])

    const trail = await screen.findByRole('navigation', { name: 'Breadcrumb' })
    // A breadcrumb reading a bare UUID tells the reader nothing, and reading it aloud is worse.
    expect(trail.textContent).not.toContain('1f0c9e6a')
    expect(trail.textContent).toContain('Run')
  })

  it('states which module owns a screen it has not built, and requests nothing', async () => {
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/settings'])

    await screen.findByRole('heading', { level: 1, name: 'Workspace settings' })
    expect(
      screen.getByRole('heading', { name: 'Workspace settings is not built yet' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/belongs to module 26/)).toBeInTheDocument()
    // The only call made is the session read. A screen that requested data it cannot render would
    // produce exactly the half-built behaviour the acceptance gate forbids.
    expect(server.calls.filter((call) => !call.endsWith('/v1/session'))).toEqual([])
  })

  it('answers a workspace the person is not a member of exactly as it answers one that does not exist', async () => {
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-not-mine/overview'])

    await screen.findByRole('heading', { name: 'Not available' })
    // No mention of membership, permission, or another tenant. Being more informative than the
    // server would rebuild the cross-tenant existence oracle module 18 closed.
    expect(screen.queryByText(/permission/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/member/i)).not.toBeInTheDocument()
  })

  it('does not serve an address that is not a page', async () => {
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/not/a/page'])

    await screen.findByRole('heading', { level: 1, name: 'This page does not exist' })
    // Distinct from "not available": this one is about the address, and says nothing about whether
    // any workspace or resource exists.
    expect(screen.getByText(/tells you nothing about whether a workspace/)).toBeInTheDocument()
    // And it is not a dead end. This route is reached most often by a truncated link, and leaving a
    // person with only the back button is a poor answer to a mistake they may not have made.
    expect(screen.getByRole('link', { name: 'Go to your workspaces' })).toHaveAttribute(
      'href',
      '/workspaces',
    )
  })
})

describe('focus management', () => {
  it('puts the skip link first and lands focus inside main when it is followed', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })

    document.body.focus()
    await user.tab()
    const skip = screen.getByRole('link', { name: 'Skip to main content' })
    expect(skip).toHaveFocus()

    await user.keyboard('{Enter}')
    // Focus must actually be inside main afterwards. Asserting only that the target is focusable is
    // what let a real browser leave focus on the link, so that the next Tab went straight back into
    // the header the person was skipping.
    const main = document.getElementById('af-main-content')
    expect(main).toHaveAttribute('tabindex', '-1')
    expect(main).toHaveFocus()
  })

  it('moves focus to the heading on a genuine route change, but not on first paint', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])

    const first = await screen.findByRole('heading', { level: 1, name: 'Overview' })
    // Not on arrival: a person who just pressed Enter in the address bar did not ask to be moved.
    expect(first).not.toHaveFocus()

    await user.click(screen.getByRole('link', { name: 'Runners' }))
    const next = await screen.findByRole('heading', { level: 1, name: 'Runners' })
    expect(next).toHaveFocus()
  })

  it('moves focus to the heading when the route change swaps the whole subtree', async () => {
    // The regression a real browser found and the unit tests did not. Navigating within the
    // workspace reuses the route element, so a per-component record of "have we navigated yet"
    // happens to work; arriving from the chooser mounts a fresh heading, and a per-component record
    // reads that as the first paint and declines to move focus. The record belongs to the
    // application.
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/workspaces'])

    await screen.findByRole('heading', { level: 1, name: 'Choose a workspace' })
    await user.click(screen.getByRole('link', { name: /Alder/ }))

    const heading = await screen.findByRole('heading', { level: 1, name: 'Overview' })
    expect(heading).toHaveFocus()
  })

  it('names the page in the document title, not just the product', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })
    // Eleven routes under one unchanging title give a reader eleven identical history entries.
    expect(document.title).toBe('Overview · AccessForge')

    await user.click(screen.getByRole('link', { name: 'Runners' }))
    await screen.findByRole('heading', { level: 1, name: 'Runners' })
    expect(document.title).toBe('Runners · AccessForge')
  })

  it('does not leave the heading in the tab order', async () => {
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    const heading = await screen.findByRole('heading', { level: 1, name: 'Overview' })
    expect(heading).toHaveAttribute('tabindex', '-1')
  })
})

describe('tenancy and the end of a session', () => {
  it('switches workspace by navigating, and discards what was in flight when it did', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    const { client } = renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })

    const before = client.epoch
    await user.selectOptions(screen.getByLabelText('Workspace'), 'ws-zebra')

    await waitFor(() => {
      expect(screen.getByRole('navigation', { name: 'Breadcrumb' }).textContent).toContain('Zebra')
    })
    // The route change unmounts the previous workspace's screens. The epoch advance discards the
    // responses that were already on their way to them.
    expect(client.epoch).toBeGreaterThan(before)
  })

  it('survives rapid switching without showing a mixture of two workspaces', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })

    const switcher = screen.getByLabelText('Workspace')
    await user.selectOptions(switcher, 'ws-zebra')
    await user.selectOptions(switcher, 'ws-alder')
    await user.selectOptions(switcher, 'ws-zebra')

    await waitFor(() => {
      const trail = screen.getByRole('navigation', { name: 'Breadcrumb' }).textContent ?? ''
      expect(trail).toContain('Zebra')
      expect(trail).not.toContain('Alder')
    })
  })

  it('shows no tenant data after sign-out', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' }).textContent).toContain('Alder')

    await user.click(screen.getByRole('button', { name: 'Sign out' }))

    await screen.findByLabelText(/Email address/)
    expect(screen.queryByText('Alder')).not.toBeInTheDocument()
    expect(screen.queryByText('engineer@example.test')).not.toBeInTheDocument()
  })

  it('discards a session response that arrives after sign-out', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })

    // A read is in flight when sign-out happens. Its answer describes a session that no longer
    // exists, and rendering it would restore the shell after the person signed out.
    server.holdSession()
    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    await screen.findByLabelText(/Email address/)
    server.setSession(MEMBER)
    server.releaseSession()

    await waitFor(() => {
      expect(screen.getByLabelText(/Email address/)).toBeInTheDocument()
    })
    expect(screen.queryByText('Alder')).not.toBeInTheDocument()
  })

  it('says so when sign-out cleared the browser but the server never confirmed it', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })

    server.setSignOutFails(true)
    await user.click(screen.getByRole('button', { name: 'Sign out' }))

    // The tenant data goes either way — that is the part that protects a shared machine.
    await screen.findByLabelText(/Email address/)
    expect(screen.queryByText('Alder')).not.toBeInTheDocument()

    // But claiming the session ended would be a claim this code cannot support: the cookie is gone
    // from here and the session is still live for anyone holding a copy of the token.
    expect(
      await screen.findByRole('heading', { name: 'Your sign-out was not confirmed' }),
    ).toBeVisible()
  })

  it('does not claim an unconfirmed sign-out when the server did confirm it', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })

    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    await screen.findByLabelText(/Email address/)
    expect(
      screen.queryByRole('heading', { name: 'Your sign-out was not confirmed' }),
    ).not.toBeInTheDocument()
  })

  it('drops a revoked membership from the navigation on the next session read', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })
    expect(within(screen.getByLabelText('Workspace')).getAllByRole('option')).toHaveLength(2)

    // The owner removes this person from Zebra while the tab is open. The next read is the next
    // sign-in here; what matters is that the list comes from the answer rather than from memory.
    server.setSession({ ...MEMBER, workspaces: [MEMBER.workspaces[0]!] })
    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    await user.type(await screen.findByLabelText(/Email address/), 'engineer@example.test')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => {
      const options = within(screen.getByLabelText('Workspace')).getAllByRole('option')
      expect(options.map((option) => option.textContent)).toEqual(['Alder (owner)'])
    })
    // And nothing offers a way into Zebra any more. A navigation built from a remembered membership
    // list would still show the link, and following it would produce a 404 the person cannot
    // explain.
    expect(screen.queryByText(/Zebra/)).not.toBeInTheDocument()
  })

  it('answers a deep link into a revoked workspace as not available', async () => {
    const server = createFakeServer({ ...MEMBER, workspaces: [MEMBER.workspaces[0]!] })
    renderApp(server, ['/w/ws-zebra/overview'])

    await screen.findByRole('heading', { name: 'Not available' })
    expect(screen.queryByRole('heading', { level: 1, name: 'Overview' })).not.toBeInTheDocument()
  })
})

describe('theme', () => {
  it('follows the system by default, setting no attribute at all', async () => {
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })
    // No attribute means the stylesheet's prefers-color-scheme block applies, including a change the
    // person makes while the page is open.
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
  })

  it('offers three choices, because following the system is one of them', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    renderApp(server, ['/w/ws-alder/overview'])
    await screen.findByRole('heading', { level: 1, name: 'Overview' })

    const group = screen.getByRole('group', { name: 'Colour theme' })
    expect(within(group).getAllByRole('radio')).toHaveLength(3)

    await user.click(within(group).getByRole('radio', { name: 'Dark' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(window.localStorage.getItem('accessforge.theme')).toBe('dark')

    await user.click(within(group).getByRole('radio', { name: 'System' }))
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
    expect(window.localStorage.getItem('accessforge.theme')).toBeNull()
  })
})
