/**
 * The focusable summary shown after a failed submission.
 *
 * The behaviour required by UI-UX section 4 is precise, and each clause is there because the
 * obvious alternative fails someone:
 *
 * **Focus moves once, to the summary.** Not to the first invalid field: a person who lands in a
 * text box has no idea how many other things are wrong, or that anything is wrong at all.
 *
 * **It moves on submission, never on blur.** Validating on blur and moving focus makes a form
 * impossible to fill in with a keyboard, because every attempt to leave a field throws the person
 * back somewhere else.
 *
 * **Each entry links to its control.** A list of complaints with no way to reach the field is a
 * scavenger hunt.
 *
 * **A repeated failure re-announces.** `submissionId` changes on every attempt, so the effect
 * re-runs and focus returns even when the errors are identical — otherwise the second attempt looks
 * to a screen-reader user exactly like nothing happening.
 */

import type { JSX } from 'react'

import { useEffect, useRef } from 'react'

export interface FieldError {
  readonly fieldId: string
  readonly message: string
}

export interface ErrorSummaryProps {
  readonly errors: readonly FieldError[]
  /** Increments once per submission attempt. */
  readonly submissionId: number
  readonly heading?: string
}

export const ErrorSummary = ({
  errors,
  submissionId,
  heading = 'This form could not be submitted',
}: ErrorSummaryProps): JSX.Element | null => {
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (errors.length === 0) return
    ref.current?.focus()
    // Depends on submissionId as well as the errors, so an identical second failure still moves
    // focus. Without it the list is unchanged, the effect does not re-run, and the person is left
    // wondering whether their second attempt did anything.
  }, [submissionId, errors.length])

  if (errors.length === 0) return null

  return (
    <div
      ref={ref}
      // Programmatically focusable, not in the tab order. It exists to be moved to, not to be an
      // extra stop everyone must tab past on every visit.
      tabIndex={-1}
      role="alert"
      aria-labelledby="af-error-summary-heading"
      className="af-error-summary"
    >
      <h2 id="af-error-summary-heading" className="af-notice__heading">
        {heading}
      </h2>
      <ul>
        {errors.map((error) => (
          <li key={error.fieldId}>
            <a className="af-link" href={`#${error.fieldId}`}>
              {error.message}
            </a>
          </li>
        ))}
      </ul>
    </div>
  )
}
