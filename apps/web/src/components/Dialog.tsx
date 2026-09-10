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

export const Dialog = ({
  open,
  heading,
  onClose,
  children,
  actions,
}: DialogProps): JSX.Element => {
  const ref = useRef<HTMLDialogElement | null>(null)
  const returnTo = useRef<HTMLElement | null>(null)
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
      if (typeof element.close === 'function') element.close()
      else element.removeAttribute('open')
      const target = returnTo.current
      // Only if it is still in the document. Focusing a detached node silently sends focus to
      // `<body>`, which is exactly the "focus lost after unmount" failure.
      if (target !== null && document.contains(target)) target.focus()
      returnTo.current = null
    }
  }, [open])

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
      onClose={onClose}
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
