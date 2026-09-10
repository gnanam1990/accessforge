/**
 * One workspace's pages, and the guard in front of them.
 *
 * The guard is the reason this component exists. A person can type, bookmark or be sent a URL for
 * any workspace at all, and the answer for a workspace they are not a member of must be exactly the
 * answer for one that does not exist. The server already behaves that way — module 18's
 * `problems.not_found` is a single helper so the two cannot drift — and the UI must not be more
 * informative than the server chose to be. So `membershipFor` returning null produces
 * `NotFoundState`, with no mention of membership, existence, or another tenant.
 *
 * This is a display concern only. It is not the authorization: every request the screens make is
 * authorised by the server on its own terms. A guard here that granted anything would be permission
 * inferred from UI visibility, which SECURITY-PRIVACY section 3 explicitly forbids.
 */

import type { JSX } from 'react'

import { Outlet, useLocation, useParams } from 'react-router-dom'

import { AppShell } from '../shell/AppShell'
import { NotFoundState } from '../components/states'
import { breadcrumbsFor } from './routeMap'
import { membershipFor, useSession } from '../session/SessionProvider'
import { useAnnouncer } from '../a11y/Announcer'

export const WorkspaceRoute = (): JSX.Element => {
  const { workspaceId = '' } = useParams()
  const { pathname } = useLocation()
  const { state, signOut, client } = useSession()
  const { announce } = useAnnouncer()

  if (state.status !== 'authenticated') {
    // The router only mounts this below an authenticated gate, so reaching here means the session
    // ended between the gate's render and this one.
    return <NotFoundState />
  }

  const membership = membershipFor(state, workspaceId)
  const crumbs =
    membership === null ? [] : breadcrumbsFor(workspaceId, membership.name, pathname)

  return (
    <AppShell
      email={state.email}
      workspaces={state.workspaces}
      workspaceId={membership === null ? null : workspaceId}
      crumbs={crumbs}
      onSignOut={() => void signOut()}
      onSwitchWorkspace={(next) => {
        // The route change unmounts the screens, which discards their state. This discards what was
        // already in flight when it happened: a response for the workspace being left would
        // otherwise arrive afterwards and be rendered into the one being entered.
        client.invalidateInFlight()
        const name = state.workspaces.find((w) => w.workspaceId === next)?.name ?? 'workspace'
        announce(`Switched to ${name}.`)
      }}
    >
      {membership === null ? <NotFoundState /> : <Outlet />}
    </AppShell>
  )
}
