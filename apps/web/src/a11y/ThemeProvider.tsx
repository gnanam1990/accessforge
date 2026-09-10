/**
 * Light, dark, or the operating system's choice.
 *
 * "System" is the default and is not a stored value: with no `data-theme` attribute the stylesheet's
 * `prefers-color-scheme` block applies, so the page follows the platform including a change made
 * while it is open. An application that read the preference once at startup and wrote a fixed
 * attribute would stop following it.
 *
 * The choice is stored in `localStorage`, which is the right tool for exactly this: a per-browser
 * convenience that nothing depends on. Every access is guarded, because the accessor itself throws
 * in a browser configured to block site data, and a theme preference must never be the reason an
 * application fails to start.
 *
 * Reduced motion is *not* stored or toggled. It is read from the platform and only from the
 * platform: a person who has asked their system for less motion has already answered, and offering
 * them the question again in an application is asking them to configure the same thing twice.
 */

import type { JSX } from 'react'

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export type ThemeChoice = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'accessforge.theme'

const read = (): ThemeChoice => {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : 'system'
  } catch {
    return 'system'
  }
}

interface ThemeApi {
  readonly choice: ThemeChoice
  readonly setChoice: (choice: ThemeChoice) => void
  /** Read from the platform. Exposed so a component can skip an optional transition entirely rather
   * than run it for zero milliseconds. */
  readonly prefersReducedMotion: boolean
}

const ThemeContext = createContext<ThemeApi | null>(null)

export const ThemeProvider = ({ children }: { readonly children: ReactNode }): JSX.Element => {
  const [choice, setChoiceState] = useState<ThemeChoice>(read)
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false)

  useEffect(() => {
    const root = document.documentElement
    if (choice === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', choice)
  }, [choice])

  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)')
    setPrefersReducedMotion(query.matches)
    const onChange = (event: MediaQueryListEvent): void => setPrefersReducedMotion(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next)
    try {
      if (next === 'system') window.localStorage.removeItem(STORAGE_KEY)
      else window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // A browser blocking site data. The choice still applies to this page; it simply will not be
      // remembered, and that is not worth an error message.
    }
  }, [])

  const api = useMemo(
    () => ({ choice, setChoice, prefersReducedMotion }),
    [choice, setChoice, prefersReducedMotion],
  )

  return <ThemeContext.Provider value={api}>{children}</ThemeContext.Provider>
}

export const useTheme = (): ThemeApi => {
  const api = useContext(ThemeContext)
  if (api === null) throw new Error('useTheme requires a ThemeProvider above it')
  return api
}
