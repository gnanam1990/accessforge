/**
 * A modal dialog, built on the native `<dialog>` element.
 *
 * `showModal()` gives four behaviours for free that a div-with-`role="dialog"` has to reimplement,
 * and reimplementations get them wrong in ways that only a keyboard or screen-reader user notices:
 * the focus trap, inertness of the rest of the page, `Escape` to close, and the backdrop.
 *
 * What this component adds:
 *
 * **Focus returns to the element that opened it** — explicitly, rather than relying on the
 * browser's restoration, which does not survive the trigger being unmounted or re-rendered. UI-UX
 * section 8 requires dialogs not to trap focus after unmount, so the returned-to element is
 * captured at open time and checked for presence in the document before it is focused.
 *
 * **Closing is one path.** `Escape`, the close button and the backdrop all run the same `onClose`,
 * so a caller cannot end up with state that only updates on some of them.
 *
 * **The heading is required and is the accessible name.** A dialog announced only as "dialog" tells
 * the reader nothing about what it is asking.
 *
 * Consequence-specific naming is the caller's job: UI-UX section 2 requires approval, cancellation,
 * deletion and publication to be separate named actions, so this component has no "Confirm" button
 * of its own to offer.
 */

import type { JSX } from 'react'

import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'

export interface DialogProps {
  readonly open: boolean
  readonly heading: string
  readonly onClose: () => void
  readonly children: ReactNode
  /** The dialog's actions. Each should name its consequence. */
  readonly actions: ReactNode
}

/** Return focus to `target`, but only while it is still in the document. */
const restoreFocus = (target: HTMLElement | null): void => {
  // Focusing a detached node silently sends focus to `<body>`, which is exactly the "focus lost
  // after unmount" failure this guards against.
  if (target !== null && document.contains(target)) target.focus()
}

export const Dialog = ({
  open,
  heading,
  onClose,
  children,
  actions,
}: DialogProps): JSX.Element => {
  const ref = useRef<HTMLDialogElement | null>(null)
  const returnTo = useRef<HTMLElement | null>(null)
  /** Set while this component is the one calling `close()`, so the native `close` event that
   * results is recognised as an echo rather than a second request to close. */
  const closingOurselves = useRef(false)
  const headingId = useId()

  useEffect(() => {
    const element = ref.current
    if (element === null) return

    if (open && !element.open) {
      returnTo.current = document.activeElement as HTMLElement | null
      // `showModal` is what supplies the focus trap, the inertness of the rest of the page and the
      // backdrop. Where it is absent — jsdom, which the unit tests run in — the element is opened by
      // attribute instead. That is an honest degradation rather than a simulation: the modal
      // behaviours genuinely are not applied, so the tests here verify the return-focus and
      // single-close-path behaviour and make no claim about the trap. The trap is the platform's.
      if (typeof element.showModal === 'function') element.showModal()
      else element.setAttribute('open', '')
    }

    if (!open && element.open) {
      closingOurselves.current = true
      if (typeof element.close === 'function') {
        element.close()
      } else {
        // The degraded path mirrors the native one, `close` event included. Without the event the
        // fallback would be quieter than the real element — and the guard against the duplicate
        // callback would be untestable in exactly the environment the tests run in, which is how a
        // guard ends up shipping unexercised.
        element.removeAttribute('open')
        element.dispatchEvent(new Event('close'))
      }
      closingOurselves.current = false
      restoreFocus(returnTo.current)
      returnTo.current = null
    }
  }, [open])

  useEffect(
    () =>
      // Unmount cleanup, because `{open && <Dialog />}` is the obvious way to write a caller — and
      // it removes the element without ever rendering `open={false}`, so the branch above never
      // runs. Focus would be left on a node that no longer exists, which means `<body>`, which means
      // a keyboard user starts again from the top of the page.
      () => {
        restoreFocus(returnTo.current)
        returnTo.current = null
      },
    [],
  )

  return (
    <dialog
      ref={ref}
      className="af-dialog"
      aria-labelledby={headingId}
      onCancel={(event) => {
        // The browser's own Escape handling. Prevented and routed through `onClose` so the caller's
        // state and the element's state cannot disagree.
        event.preventDefault()
        onClose()
      }}
      onClose={() => {
        // The native `close` event fires both when the browser closes the dialog and when this
        // component does. Only the first is a request; the second is the echo of one already
        // handled, and calling back for it would run the caller's close path twice — closing
        // whatever they opened next, in the worst case.
        if (closingOurselves.current) return
        onClose()
      }}
    >
      <div className="af-stack">
        <h2 id={headingId} className="af-notice__heading">
          {heading}
        </h2>
        {children}
        <div className="af-row">{actions}</div>
      </div>
    </dialog>
  )
}
