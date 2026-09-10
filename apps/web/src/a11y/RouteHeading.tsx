/**
 * The page heading, which is also where focus lands after a genuine route change.
 *
 * A single-page application replaces the document without the browser's own navigation, so nothing
 * resets the reading position: a screen-reader user who activates a link stays wherever they were,
 * hears nothing, and has no way to know the page changed. Moving focus to the new `<h1>` is the
 * standard repair.
 *
 * Two conditions on "genuine", and both matter:
 *
 * **Not on first paint.** Focusing the heading when the application loads takes focus away from the
 * browser's own starting position, and a person who has just pressed Enter in the address bar did
 * not ask to be moved.
 *
 * **Not when only the query string changed.** Filtering and sorting live in the URL (UI-UX section
 * 3), so a filter change is a location change — and throwing focus back to the heading every time
 * someone types in a filter box makes filtering impossible to use.
 *
 * **Whether this is the first paint is a fact about the application, not about this component.** An
 * earlier version kept the previous pathname in a ref inside `RouteHeading`, which was wrong in a
 * way no unit test caught and a real browser found immediately: React reuses the component instance
 * when a navigation stays within the same route element, so heading-to-heading moves worked — but a
 * navigation that swaps the subtree (the workspace chooser to a workspace, say) mounts a *fresh*
 * `RouteHeading`, whose ref is empty, which the component then read as "this is the first paint" and
 * declined to move focus. The one navigation most likely to be made by keyboard was the one that
 * silently did nothing. The record now lives in a provider mounted once, above every route.
 *
 * Background events never move focus. That is enforced by this being the only thing in the
 * application that calls `focus()` on a heading.
 */

import { createContext, useContext, useEffect, useMemo, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import type { JSX, ReactNode } from 'react'

export const MAIN_CONTENT_ID = 'af-main-content'

interface RouteFocusApi {
  /** True exactly once per genuine arrival at a new path, and never for the first paint. */
  readonly claimFocusForPath: (pathname: string) => boolean
}

const RouteFocusContext = createContext<RouteFocusApi | null>(null)

export const RouteFocusProvider = ({ children }: { readonly children: ReactNode }): JSX.Element => {
  const handled = useRef<string | null>(null)
  const started = useRef(false)

  const api = useMemo<RouteFocusApi>(
    () => ({
      claimFocusForPath: (pathname: string) => {
        if (!started.current) {
          started.current = true
          handled.current = pathname
          return false
        }
        if (handled.current === pathname) return false
        handled.current = pathname
        return true
      },
    }),
    [],
  )

  return <RouteFocusContext.Provider value={api}>{children}</RouteFocusContext.Provider>
}

export const RouteHeading = ({ children }: { readonly children: ReactNode }): JSX.Element => {
  const { pathname } = useLocation()
  const heading = useRef<HTMLHeadingElement | null>(null)
  const api = useContext(RouteFocusContext)

  useEffect(() => {
    // Without a provider the heading simply never takes focus. Failing quiet rather than throwing,
    // because a component rendered in isolation — a preview, a test of the heading alone — is not a
    // navigation, and an exception there would be noise about a thing that did not happen.
    if (api === null) return
    if (!api.claimFocusForPath(pathname)) return
    heading.current?.focus()
  }, [api, pathname])

  return (
    // Programmatically focusable only. A heading in the tab order is an extra stop for everyone, on
    // every page, to reach something they were already reading.
    <h1 ref={heading} tabIndex={-1} style={{ fontSize: 'var(--af-text-title)', marginTop: 0 }}>
      {children}
    </h1>
  )
}
