/**
 * The labelled sidebar, which becomes a labelled disclosure on a narrow screen.
 *
 * Labelled in both forms. UI-UX section 3 asks for a "compact labelled navigation drawer on narrow
 * screens", and the compact form here is a `<details>` element with the text "Menu" — not an
 * unlabelled hamburger glyph, which is the single most common icon-only control with no accessible
 * name on the web.
 *
 * `<details>` rather than a button and a `aria-expanded` div, because the element already has the
 * expanded/collapsed semantics, the keyboard behaviour and the announcement, and it works before any
 * JavaScript has run.
 *
 * `NavLink` supplies `aria-current="page"` for the active entry, and the stylesheet reinforces it
 * with weight and an inset bar so that current-ness is not carried by colour.
 */

import type { JSX } from 'react'

import { NavLink } from 'react-router-dom'
import { SectionIcon } from '../components/Brand'

import { WORKSPACE_ROUTES, workspacePath } from '../routes/routeMap'
import type { WorkspaceRole } from '../api/session'

/**
 * What the sidebar lists depends on how the caller may enter the workspace. Members see every
 * section; a tester-desk participant (the session carries TESTER or CLIENT, not a member role)
 * sees only the surfaces their authority can answer — every other section would render refusals
 * dressed up as navigation.
 */
const itemsFor = (role: WorkspaceRole | undefined) =>
  WORKSPACE_ROUTES.filter((route) => route.inPrimaryNavigation).filter((route) => {
    if (role === 'TESTER') return route.path.startsWith('tester-desk/tester')
    if (role === 'CLIENT') return route.path === 'tester-desk/client'
    return true
  })

const List = ({ workspaceId, role }: {
  readonly workspaceId: string
  readonly role: WorkspaceRole | undefined
}): JSX.Element => (
  <ul className="af-nav-list">
    {itemsFor(role).map((route, index) => (
      <li key={route.path}>
        {/* `end` so the match is exact. Without it, /projects/p-1 marks the Projects entry as the
            current page while the project detail screen is the one showing, and `aria-current` is
            how a screen-reader user establishes where they are. */}
        <NavLink end to={workspacePath(workspaceId, route.path)}>
          <SectionIcon index={index} /><span>{route.label}</span>
        </NavLink>
      </li>
    ))}
  </ul>
)

export const PrimaryNavigation = ({
  workspaceId,
  role,
  narrow,
}: {
  readonly workspaceId: string
  readonly role?: WorkspaceRole | undefined
  readonly narrow: boolean
}): JSX.Element => (
  <nav className="af-sidebar" aria-label="Workspace sections">
    {narrow ? (
      <details>
        <summary>Menu</summary>
        <List workspaceId={workspaceId} role={role} />
      </details>
    ) : (
      <List workspaceId={workspaceId} role={role} />
    )}
  </nav>
)
