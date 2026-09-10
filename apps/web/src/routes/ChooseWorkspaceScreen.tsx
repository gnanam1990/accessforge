/**
 * Where a signed-in person with no workspace in the URL lands.
 *
 * Three cases, and they are different screens rather than three sentences on one:
 *
 * * **Several memberships** — a list of links, because a person who arrives here chose to.
 * * **Exactly one** — still a list. Redirecting automatically would make the back button useless:
 *   the redirect fires again the moment they return to this page.
 * * **None** — an explicit statement, and specifically not an invitation to create a workspace. This
 *   deployment has no route that creates one, and offering a control that does nothing is worse than
 *   admitting there is nothing to do here.
 */

import type { JSX } from 'react'

import { Link } from 'react-router-dom'

import { RouteHeading } from '../a11y/RouteHeading'
import { EmptyState } from '../components/states'
import { workspacePath } from './routeMap'
import { useSession } from '../session/SessionProvider'

export const ChooseWorkspaceScreen = (): JSX.Element => {
  const { state } = useSession()
  const workspaces = state.status === 'authenticated' ? state.workspaces : []

  return (
    <>
      <RouteHeading>Choose a workspace</RouteHeading>
      {workspaces.length === 0 ? (
        <EmptyState heading="You are not a member of any workspace" because="nothing-created-yet">
          <p className="af-secondary">
            A workspace owner has to add you before there is anything here. Membership is granted by
            the server, and this page shows exactly what it reported.
          </p>
        </EmptyState>
      ) : (
        <nav aria-label="Your workspaces">
          <ul className="af-nav-list">
            {workspaces.map((workspace) => (
              <li key={workspace.workspaceId}>
                <Link to={workspacePath(workspace.workspaceId)}>
                  {workspace.name}
                  <span className="af-secondary"> · {workspace.role.toLowerCase()}</span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </>
  )
}
