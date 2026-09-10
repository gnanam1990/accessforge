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

import { WORKSPACE_ROUTES, workspacePath } from '../routes/routeMap'

const items = WORKSPACE_ROUTES.filter((route) => route.inPrimaryNavigation)

const List = ({ workspaceId }: { readonly workspaceId: string }): JSX.Element => (
  <ul className="af-nav-list">
    {items.map((route) => (
      <li key={route.path}>
        {/* `end` so the match is exact. Without it, /projects/p-1 marks the Projects entry as the
            current page while the project detail screen is the one showing, and `aria-current` is
            how a screen-reader user establishes where they are. */}
        <NavLink end to={workspacePath(workspaceId, route.path)}>
          {route.label}
        </NavLink>
      </li>
    ))}
  </ul>
)

export const PrimaryNavigation = ({
  workspaceId,
  narrow,
}: {
  readonly workspaceId: string
  readonly narrow: boolean
}): JSX.Element => (
  <nav aria-label="Workspace sections">
    {narrow ? (
      <details>
        <summary>Menu</summary>
        <List workspaceId={workspaceId} />
      </details>
    ) : (
      <List workspaceId={workspaceId} />
    )}
  </nav>
)
