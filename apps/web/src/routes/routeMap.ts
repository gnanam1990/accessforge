/**
 * The route table from UI-UX.md section 3, as data.
 *
 * As data rather than as JSX because three things have to agree with each other: the router, the
 * sidebar navigation, and the breadcrumb trail. Derived from one table they cannot disagree; written
 * out three times they will, and the failure is a navigation entry that leads somewhere the router
 * does not recognise.
 *
 * Each entry records which module owns the *screen* and whether that screen exists yet. Module 21
 * owns the shell, the route map and the chrome; the screens belong to 22, 23 and 24. A route whose
 * screen has not been built renders an explicit statement to that effect — never invented rows, and
 * never an empty state, which would claim the server had been asked.
 *
 * **A URL is a location, never an authority.** Every one of these paths is a stable deep link, and
 * the server authorises every request that results from following one. Nothing here — no fixture
 * value, no token, no secret reference — may ever be placed in a path or a query string, because
 * both go into browser history, into a referrer header, and into a screenshot.
 */

export interface RouteDefinition {
  /** Relative to `/w/:workspaceId`. */
  readonly path: string
  /** The sidebar label and the breadcrumb text. */
  readonly label: string
  /** Shown in the sidebar; detail routes reached from a list are not. */
  readonly inPrimaryNavigation: boolean
  /** The module that owns this screen's content. */
  readonly ownedByModule: number
  /** Whether that screen exists. False renders the explicit not-built statement. */
  readonly built: boolean
  /** The page's `<h1>`. */
  readonly heading: string
}

export const WORKSPACE_ROUTES: readonly RouteDefinition[] = [
  {
    path: 'overview',
    label: 'Overview',
    inPrimaryNavigation: true,
    ownedByModule: 22,
    heading: 'Overview',
    built: true,
  },
  {
    path: 'projects',
    label: 'Projects',
    inPrimaryNavigation: true,
    ownedByModule: 22,
    heading: 'Projects',
    built: true,
  },
  {
    path: 'projects/:projectId',
    label: 'Project',
    inPrimaryNavigation: false,
    ownedByModule: 22,
    heading: 'Project',
    built: true,
  },
  {
    path: 'projects/:projectId/journeys/:journeyId',
    label: 'Journey',
    inPrimaryNavigation: false,
    ownedByModule: 22,
    heading: 'Journey',
    built: true,
  },
  {
    path: 'runners',
    label: 'Runners',
    inPrimaryNavigation: true,
    ownedByModule: 22,
    heading: 'Runners',
    built: true,
  },
  {
    path: 'runs/:runId',
    label: 'Run',
    inPrimaryNavigation: false,
    ownedByModule: 23,
    heading: 'Run',
    built: false,
  },
  {
    path: 'findings/:findingId',
    label: 'Finding',
    inPrimaryNavigation: false,
    ownedByModule: 23,
    heading: 'Finding',
    built: false,
  },
  {
    path: 'patches/:patchId',
    label: 'Patch',
    inPrimaryNavigation: false,
    ownedByModule: 24,
    heading: 'Proposed repair',
    built: false,
  },
  {
    path: 'reviews/:reviewId',
    label: 'Review',
    inPrimaryNavigation: false,
    ownedByModule: 24,
    heading: 'Human review',
    built: false,
  },
  {
    path: 'exports/:exportId',
    label: 'Export',
    inPrimaryNavigation: false,
    ownedByModule: 23,
    heading: 'Evidence export',
    built: false,
  },
  {
    path: 'settings',
    label: 'Settings',
    inPrimaryNavigation: true,
    ownedByModule: 26,
    heading: 'Workspace settings',
    built: false,
  },
]

export const workspacePath = (workspaceId: string, path = 'overview'): string =>
  `/w/${encodeURIComponent(workspaceId)}/${path}`

export interface Crumb {
  readonly label: string
  readonly href: string | null
}

/**
 * The breadcrumb trail for a location.
 *
 * Built by walking the path segments and matching them against the route table, so a trail cannot
 * contain a step the router does not serve. Identifier segments are shown as the route's label
 * ("Run") rather than as the raw identifier: a breadcrumb reading a bare UUID tells the reader
 * nothing, and reading it aloud is worse.
 */
export const breadcrumbsFor = (
  workspaceId: string,
  workspaceName: string,
  pathname: string,
): readonly Crumb[] => {
  const prefix = `/w/${encodeURIComponent(workspaceId)}/`
  if (!pathname.startsWith(prefix)) return []

  const rest = pathname.slice(prefix.length).split('/').filter(Boolean)
  const crumbs: Crumb[] = [{ label: workspaceName, href: workspacePath(workspaceId) }]

  const consumed: string[] = []
  for (const segment of rest) {
    consumed.push(segment)
    const candidate = consumed.join('/')
    const match = WORKSPACE_ROUTES.find((route) => matchesPattern(route.path, candidate))
    if (match === undefined) continue
    crumbs.push({
      label: match.label,
      href: candidate === rest.join('/') ? null : `${prefix}${candidate}`,
    })
  }
  return crumbs
}

const matchesPattern = (pattern: string, candidate: string): boolean => {
  const patternParts = pattern.split('/')
  const candidateParts = candidate.split('/')
  if (patternParts.length !== candidateParts.length) return false
  return patternParts.every((part, index) => part.startsWith(':') || part === candidateParts[index])
}
