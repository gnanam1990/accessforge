/**
 * The route table, and the rule that a URL is a location rather than a credential.
 */

import { describe, expect, it } from 'vitest'

import { WORKSPACE_ROUTES, breadcrumbsFor, workspacePath } from './routeMap'

describe('the route table', () => {
  it('covers every route UI-UX section 3 lists', () => {
    expect(WORKSPACE_ROUTES.map((route) => route.path)).toEqual([
      'overview',
      'projects',
      'projects/:projectId',
      'projects/:projectId/journeys/:journeyId',
      'runners',
      'runs/:runId',
      'findings/:findingId',
      'patches/:patchId',
      'reviews/:reviewId',
      'exports/:exportId',
      'settings',
    ])
  })

  it('puts no query string or fragment in any path', () => {
    // Filter and sort state belongs in the query string at the screen's discretion. A path with a
    // baked-in query would make that state part of the route, and therefore part of every link.
    for (const route of WORKSPACE_ROUTES) {
      expect(route.path).not.toContain('?')
      expect(route.path).not.toContain('#')
    }
  })

  it('names an owning module for every screen', () => {
    for (const route of WORKSPACE_ROUTES) {
      expect(route.ownedByModule).toBeGreaterThan(21)
    }
  })

  it('gives every route a heading and a label', () => {
    for (const route of WORKSPACE_ROUTES) {
      expect(route.heading.length).toBeGreaterThan(0)
      expect(route.label.length).toBeGreaterThan(0)
    }
  })
})

describe('workspacePath', () => {
  it('encodes the workspace identifier', () => {
    // An identifier arrives from the server and is put into a URL. Encoding it means a value
    // containing a slash produces a path that does not resolve, rather than one that resolves
    // somewhere else.
    expect(workspacePath('a/b')).toBe('/w/a%2Fb/overview')
  })

  it('defaults to the overview', () => {
    expect(workspacePath('ws-1')).toBe('/w/ws-1/overview')
  })
})

describe('breadcrumbsFor', () => {
  it('starts at the workspace and ends at the current page, unlinked', () => {
    const crumbs = breadcrumbsFor('ws-1', 'Alder', '/w/ws-1/projects/p-1')
    expect(crumbs.map((crumb) => crumb.label)).toEqual(['Alder', 'Projects', 'Project'])
    expect(crumbs.at(-1)?.href).toBeNull()
    expect(crumbs[1]?.href).toBe('/w/ws-1/projects')
  })

  it('shows a route label rather than an identifier for a parameter segment', () => {
    const crumbs = breadcrumbsFor('ws-1', 'Alder', '/w/ws-1/runs/8f14e45f')
    expect(crumbs.map((crumb) => crumb.label)).toEqual(['Alder', 'Run'])
    expect(JSON.stringify(crumbs)).not.toContain('8f14e45f')
  })

  it('walks a deep path one level at a time', () => {
    const crumbs = breadcrumbsFor('ws-1', 'Alder', '/w/ws-1/projects/p-1/journeys/j-2')
    expect(crumbs.map((crumb) => crumb.label)).toEqual(['Alder', 'Projects', 'Project', 'Journey'])
  })

  it('returns nothing for a path outside this workspace', () => {
    // Not a partial trail. A trail built from another workspace's path would put that workspace's
    // shape on screen.
    expect(breadcrumbsFor('ws-1', 'Alder', '/w/ws-2/overview')).toEqual([])
  })

  it('ignores a segment no route serves rather than inventing a step for it', () => {
    const crumbs = breadcrumbsFor('ws-1', 'Alder', '/w/ws-1/nonsense/deeper')
    expect(crumbs.map((crumb) => crumb.label)).toEqual(['Alder'])
  })
})
