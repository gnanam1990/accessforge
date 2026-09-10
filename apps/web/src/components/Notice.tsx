/**
 * A persistent, on-page message.
 *
 * Persistent is the whole design. UI-UX section 2: "Toasts are supplementary: important errors and
 * outcomes remain on the page until resolved or intentionally dismissed." A message that vanishes
 * after four seconds is a message a screen-reader user hears halfway through and can never re-read,
 * and it is also the only copy of the instruction they needed.
 *
 * The heading level is a required parameter. A component that hard-coded `<h2>` would produce a
 * broken outline the first time it was used inside a subsection, and heading order is one of the
 * few things a screen-reader user navigates by.
 *
 * Dismissal is optional and, where offered, is a named button rather than an unlabelled ×. A notice
 * that cannot be dismissed accumulates; one that dismisses itself on a timer takes the instruction
 * away from the person who reads slowly. Whether a particular message may be dismissed is the
 * caller's decision, because only the caller knows whether the condition is resolved.
 */

import type { JSX } from 'react'

import type { ReactNode } from 'react'

import { Button } from './Button'

export type NoticeTone = 'problem' | 'warning' | 'information'

export interface NoticeProps {
  readonly tone: NoticeTone
  readonly heading: string
  readonly headingLevel: 2 | 3 | 4
  readonly children?: ReactNode
  readonly actions?: ReactNode
  /** Set only for a message that appears in response to something the user just did. A notice that
   * is part of the page on arrival must not be a live region: it would be announced twice. */
  readonly live?: boolean
  /** When given, the notice can be dismissed. The button names what it dismisses, so a reader
   * moving between buttons does not meet three controls all called "Dismiss". */
  readonly onDismiss?: () => void
}

export const Notice = ({
  tone,
  heading,
  headingLevel,
  children,
  actions,
  live = false,
  onDismiss,
}: NoticeProps): JSX.Element => {
  const Heading = `h${headingLevel}` as 'h2' | 'h3' | 'h4'
  return (
    <div
      className={`af-notice af-notice--${tone}`}
      // `alert` for a problem the person needs now; a polite status for the rest. Not `alert` for
      // everything: an assertive region interrupts whatever the reader was in the middle of.
      role={live ? (tone === 'problem' ? 'alert' : 'status') : undefined}
    >
      <Heading className="af-notice__heading">{heading}</Heading>
      {children}
      {(actions !== undefined || onDismiss !== undefined) && (
        <div className="af-row">
          {actions}
          {onDismiss !== undefined && (
            <Button variant="secondary" onClick={onDismiss}>
              {`Dismiss: ${heading}`}
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
