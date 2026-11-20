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
import { Brand } from '../components/Brand'
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
  /** Scoped presentation for one product surface; only 'tester-desk' exists today. */
  readonly surface?: 'tester-desk' | undefined
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
  surface,
}: AppShellProps): JSX.Element => {
  const narrow = useMediaQuery(NARROW_QUERY)

  return (
    <div className={`af-shell${surface === 'tester-desk' ? ' af-td' : ''}`}>
      <SkipLink />
      <header className="af-header">
        <div className="af-row">
          <Brand />
          <WorkspaceSwitcher
            workspaces={workspaces}
            current={workspaceId}
            onSwitch={onSwitchWorkspace}
          />
          <Breadcrumbs crumbs={crumbs} />
        </div>
        <AccountControls email={email} onSignOut={onSignOut} />
      </header>
      <div className={`af-body${workspaceId === null ? ' af-body-no-sidebar' : ''}`}>
        {workspaceId !== null && (
          <PrimaryNavigation
            workspaceId={workspaceId}
            role={workspaces.find((w) => w.workspaceId === workspaceId)?.role}
            narrow={narrow}
          />
        )}
        <main id={MAIN_CONTENT_ID} tabIndex={-1} className="af-main af-stack">
          {banner}
          {children}
        </main>
      </div>
    </div>
  )
}
