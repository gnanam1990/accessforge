/**
 * A media query as React state.
 *
 * Subscribed to rather than sampled once, so a person who resizes a window, rotates a device or
 * changes their system text size gets the layout the new size deserves without reloading. Guarded
 * because `matchMedia` is absent in some non-browser environments and a layout hook must never be
 * the reason the application fails to render.
 */

import { useEffect, useState } from 'react'

export const useMediaQuery = (query: string): boolean => {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
    return window.matchMedia(query).matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const list = window.matchMedia(query)
    setMatches(list.matches)
    const onChange = (event: MediaQueryListEvent): void => setMatches(event.matches)
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])

  return matches
}

/** The breakpoint UI-UX section 8 names for reflow. Below this the navigation becomes a disclosure. */
export const NARROW_QUERY = '(max-width: 767px)'
