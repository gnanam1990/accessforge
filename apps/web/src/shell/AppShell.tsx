/**
 * The chrome around every authenticated page.
 *
 * Landmarks first: a `<header>`, a `<nav>` per navigation group each with its own label, and one
 * `<main>` carrying the skip link's target. A screen-reader user navigates by landmarks before they
 * navigate by anything else, and two unlabelled `<nav>` elements are two identical entries in that
 * list.
 *
 * `<main>` has `tabIndex={-1}` so the skip link actually moves focus into it rather than merely
 * scrolling it into view — see `SkipLink` for why that distinction is the difference between a skip
 * link that works and one that appears to.
 */

import type { JSX } from 'react'

import type { ReactNode } from 'react'

import { AccountControls } from './AccountControls'
import { Breadcrumbs } from './Breadcrumbs'
import { PrimaryNavigation } from './PrimaryNavigation'
import { WorkspaceSwitcher } from './WorkspaceSwitcher'
import { NARROW_QUERY, useMediaQuery } from './useMediaQuery'
import { MAIN_CONTENT_ID } from '../a11y/RouteHeading'
import { SkipLink } from '../a11y/SkipLink'
import type { WorkspaceMembership } from '../api/session'
import type { Crumb } from '../routes/routeMap'

export interface AppShellProps {
  readonly email: string
  readonly workspaces: readonly WorkspaceMembership[]
  readonly workspaceId: string | null
  readonly crumbs: readonly Crumb[]
  readonly onSignOut: () => void
  readonly onSwitchWorkspace: (workspaceId: string) => void
  readonly banner?: ReactNode
  readonly children: ReactNode
}

export const AppShell = ({
  email,
  workspaces,
  workspaceId,
  crumbs,
  onSignOut,
  onSwitchWorkspace,
  banner,
  children,
}: AppShellProps): JSX.Element => {
  const narrow = useMediaQuery(NARROW_QUERY)

  return (
    <div className="af-shell">
      <SkipLink />
      <header className="af-header">
        <div className="af-row">
          <span style={{ fontWeight: 700 }}>AccessForge</span>
          <WorkspaceSwitcher
            workspaces={workspaces}
            current={workspaceId}
            onSwitch={onSwitchWorkspace}
          />
          <Breadcrumbs crumbs={crumbs} />
        </div>
        <AccountControls email={email} onSignOut={onSignOut} />
      </header>
      <div className="af-body">
        {workspaceId !== null && <PrimaryNavigation workspaceId={workspaceId} narrow={narrow} />}
        <main id={MAIN_CONTENT_ID} tabIndex={-1} className="af-main af-stack">
          {banner}
          {children}
        </main>
      </div>
    </div>
  )
}
