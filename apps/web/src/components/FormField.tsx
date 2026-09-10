/**
 * A labelled control with an optional hint and an optional error.
 *
 * Four things this does that the equivalent hand-written markup usually gets wrong:
 *
 * **The label is a real `<label>` with a real `for`.** Not a placeholder. A placeholder disappears
 * the moment someone types, which removes the only description of the field from a person who was
 * relying on it — and placeholders are frequently rendered at a contrast no guideline permits.
 *
 * **The hint and the error are both wired through one `aria-describedby`.** Two attributes fighting
 * over the same slot is why hints silently disappear from announcements when validation fails.
 *
 * **`aria-invalid` appears only when there is an error to describe.** `aria-invalid="false"` on
 * every field is noise, and a field marked invalid with no message is worse than an unmarked one.
 *
 * **Required is marked in text.** An asterisk with a legend elsewhere on the page is a lookup the
 * reader has to perform from memory.
 */

import type { JSX } from 'react'

import { useId } from 'react'
import type { ReactNode } from 'react'

export interface FormFieldProps {
  /** Supply one when the caller needs to know it before rendering — an error summary linking to
   * this control, for instance. Omitted, the field generates its own. */
  readonly id?: string
  readonly label: string
  readonly hint?: string
  readonly error?: string
  readonly required?: boolean
  /** Receives the ids it must apply. The control stays the caller's, so a select, a textarea and a
   * group of radios all work without this component growing a mode for each. */
  readonly children: (ids: {
    readonly id: string
    readonly describedBy: string | undefined
    readonly invalid: boolean
  }) => ReactNode
}

export const FormField = ({
  id: suppliedId,
  label,
  hint,
  error,
  required = false,
  children,
}: FormFieldProps): JSX.Element => {
  // Generated unconditionally — hooks cannot be called conditionally — and used only when the
  // caller supplied nothing. A caller that needs the id before render (to link an error summary to
  // it) passes its own, rather than reading one back out during render.
  const generated = useId()
  const id = suppliedId ?? generated
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  const describedBy =
    [hint !== undefined ? hintId : null, error !== undefined ? errorId : null]
      .filter((value): value is string => value !== null)
      .join(' ') || undefined

  return (
    <div className="af-field">
      <label htmlFor={id}>
        {label}
        {required && <span className="af-secondary"> (required)</span>}
      </label>
      {hint !== undefined && (
        <p id={hintId} className="af-secondary">
          {hint}
        </p>
      )}
      {children({ id, describedBy, invalid: error !== undefined })}
      {error !== undefined && (
        <p id={errorId} className="af-field__error">
          {error}
        </p>
      )}
    </div>
  )
}
