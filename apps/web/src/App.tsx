/**
 * The application: providers, the authenticated gate, and the route tree.
 *
 * The gate is the part worth reading. It renders on the session state, and every state produces a
 * different screen:
 *
 * * `checking` → a loading message. Not the sign-in form, which would flash on every page load and
 *   be gone before anyone could read it, and not the shell, which would render empty chrome around
 *   nothing.
 * * `unreachable` → "no connection", with no sign-in form. The person may well be signed in; asking
 *   them to sign in again answers a question they did not ask.
 * * `anonymous` → the sign-in screen. Distinguishing "never signed in" from "your session ended"
 *   because the second needs an explanation and the first does not.
 * * `signInUnavailable` → the sign-in screen, which in that state shows the missing-dependency
 *   notice instead of a form.
 * * `authenticated` → the shell and the routes.
 *
 * The routes are generated from `WORKSPACE_ROUTES`, so the router and the navigation cannot
 * disagree, and each entry says whether its screen exists. The ones that do not render
 * `ScreenNotBuilt`, which names the owning module and requests nothing — inventing a screen that a
 * later module owns is precisely what module 21's acceptance gate forbids.
 */

import type { JSX } from 'react'

import { Link, Navigate, Route, Routes } from 'react-router-dom'

import { AnnouncerProvider } from './a11y/Announcer'
import { RouteFocusProvider, RouteHeading } from './a11y/RouteHeading'
import { ThemeProvider } from './a11y/ThemeProvider'
import { LoadingState, OfflineState } from './components/states'
import { Notice } from './components/Notice'
import { ChooseWorkspaceScreen } from './routes/ChooseWorkspaceScreen'
import { ScreenNotBuilt } from './routes/ScreenNotBuilt'
import { JourneyScreen } from './screens/JourneyScreen'
import { OverviewScreen } from './screens/OverviewScreen'
import { ProjectScreen } from './screens/ProjectScreen'
import { ProjectsScreen } from './screens/ProjectsScreen'
import { RunnersScreen } from './screens/RunnersScreen'
import { SignInScreen } from './routes/SignInScreen'
import { WorkspaceRoute } from './routes/WorkspaceRoute'
import { WORKSPACE_ROUTES } from './routes/routeMap'
import { AppShell } from './shell/AppShell'
import { SessionProvider, useSession } from './session/SessionProvider'
import type { ApiClient } from './api/client'

/**
 * The screens that exist, by route pattern.
 *
 * Keyed by the same string the route table uses, so a screen cannot be wired to a path the
 * navigation does not know about. A route marked built with no entry here would render nothing at
 * all, which the test suite asserts cannot happen.
 */
const SCREENS: Readonly<Record<string, JSX.Element>> = {
  overview: <OverviewScreen />,
  projects: <ProjectsScreen />,
  'projects/:projectId': <ProjectScreen />,
  'projects/:projectId/journeys/:journeyId': <JourneyScreen />,
  runners: <RunnersScreen />,
}

const AuthenticatedRoutes = (): JSX.Element => (
  <Routes>
    <Route path="/" element={<Navigate to="/workspaces" replace />} />
    <Route path="/workspaces" element={<WorkspacesShell />} />
    <Route path="/w/:workspaceId" element={<WorkspaceRoute />}>
      <Route index element={<Navigate to="overview" replace />} />
      {WORKSPACE_ROUTES.map((route) => (
        <Route
          key={route.path}
          path={route.path}
          element={route.built ? SCREENS[route.path] : <ScreenNotBuilt route={route} />}
        />
      ))}
    </Route>
    <Route path="*" element={<UnknownRoute />} />
  </Routes>
)

/** The workspace chooser, inside the shell but with no workspace selected. */
const WorkspacesShell = (): JSX.Element => {
  const { state, signOut } = useSession()
  if (state.status !== 'authenticated') return <LoadingState what="your account" />
  return (
    <AppShell
      email={state.email}
      workspaces={state.workspaces}
      workspaceId={null}
      crumbs={[]}
      onSignOut={() => void signOut()}
      onSwitchWorkspace={() => undefined}
    >
      <ChooseWorkspaceScreen />
    </AppShell>
  )
}

/**
 * A path this application does not serve.
 *
 * Distinct from a resource that is not available: this one is about the URL, not about permission or
 * existence, and it says nothing about whether any workspace or resource exists.
 */
const UnknownRoute = (): JSX.Element => (
  <main style={{ padding: 'var(--af-space-8)' }} className="af-stack">
    <RouteHeading>This page does not exist</RouteHeading>
    <Notice tone="warning" heading="This page does not exist" headingLevel={2}>
      <p>
        The address does not match any page in this application. This is about the address alone and
        tells you nothing about whether a workspace or a resource exists.
      </p>
      {/* A way out. A dead-end page with no navigation leaves a person with the back button and a
          guess, and this route is reached most often by a mistyped or truncated link. */}
      <p>
        <Link className="af-link" to="/workspaces">
          Go to your workspaces
        </Link>
      </p>
    </Notice>
  </main>
)

const Gate = (): JSX.Element => {
  const { state, refresh } = useSession()

  switch (state.status) {
    case 'checking':
      return (
        <main style={{ padding: 'var(--af-space-8)' }}>
          <LoadingState what="your session" />
        </main>
      )
    case 'unreachable':
      return (
        <main style={{ padding: 'var(--af-space-8)' }}>
          <OfflineState onRetry={() => void refresh()} />
        </main>
      )
    case 'anonymous':
    case 'signInUnavailable':
      return <SignInScreen />
    case 'authenticated':
      return <AuthenticatedRoutes />
  }
}

export const App = ({ client }: { readonly client: ApiClient }): JSX.Element => (
  <ThemeProvider>
    <AnnouncerProvider>
      <RouteFocusProvider>
        <SessionProvider client={client}>
          <Gate />
        </SessionProvider>
      </RouteFocusProvider>
    </AnnouncerProvider>
  </ThemeProvider>
)
