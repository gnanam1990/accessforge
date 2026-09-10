/**
 * The first focusable thing on every page.
 *
 * It is rendered at all times and moved off-screen with `transform`, not hidden with `display:
 * none` — a hidden element is not focusable, and a skip link that cannot be reached by Tab is
 * decoration.
 *
 * **Focus is moved explicitly, not left to the fragment.** The href is kept, because it is what makes
 * this a link and what works with JavaScript unavailable. But the browser's own handling of a
 * fragment is only reliably "scroll it into view and set the sequential-navigation starting point" —
 * and in this application, driven in a real browser, following the link left focus on the link
 * itself, so the next Tab went back into the header the person was trying to skip. That is the
 * classic skip link that appears to work and does not; the unit tests could assert the target was
 * focusable and could not assert the browser had focused it.
 *
 * So the handler focuses `<main>` directly. `<main>` carries `tabIndex={-1}` for that to be possible
 * without putting it in everyone's tab order.
 */

import type { JSX } from 'react'

import { MAIN_CONTENT_ID } from './RouteHeading'

export const SkipLink = (): JSX.Element => (
  <a
    className="af-skip-link"
    href={`#${MAIN_CONTENT_ID}`}
    onClick={(event) => {
      const main = document.getElementById(MAIN_CONTENT_ID)
      if (main === null) return
      // The default is prevented only when the target exists: with no target, letting the browser
      // do whatever it does with an unresolvable fragment is better than swallowing the activation.
      event.preventDefault()
      // `focus()` scrolls the element into view by default; an explicit `scrollIntoView` after it
      // adds nothing and is a second thing that can fail.
      main.focus()
    }}
  >
    Skip to main content
  </a>
)
