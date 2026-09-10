/**
 * A button, which is a `<button>`.
 *
 * The whole component is a thin wrapper, and that is the point. Every custom button in the wild
 * eventually loses one of: keyboard activation with both Enter and Space, the implicit `button`
 * role, participation in form submission, or the `disabled` semantic. A native element loses none
 * of them, so the only job here is to keep the styling and the accessible name honest.
 *
 * Two rules the wrapper enforces that a bare element would not:
 *
 * `type` defaults to `"button"`. The HTML default is `"submit"`, so a decorative button placed in a
 * form submits it — a bug that is invisible with a mouse and immediate with a keyboard.
 *
 * An icon-only button must be given a `label`. It is a required parameter rather than an optional
 * one, so the failure is a type error at the call site rather than an unnamed control a sighted
 * reviewer will never notice.
 */

import type { JSX } from 'react'

import type { ButtonHTMLAttributes, ReactNode } from 'react'

type Variant = 'primary' | 'secondary' | 'destructive'

interface CommonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className'> {
  readonly variant?: Variant
  /** Renders `aria-busy` and disables activation. The label does not change: a control whose text
   * changes to "Saving…" leaves a screen-reader user hearing a different button from the one they
   * pressed. */
  readonly busy?: boolean
}

interface WithText extends CommonProps {
  readonly children: ReactNode
  readonly label?: string
}

interface IconOnly extends CommonProps {
  readonly children?: undefined
  /** Required for a control with no visible text. */
  readonly label: string
  readonly icon: ReactNode
}

export type ButtonProps = WithText | IconOnly

export const Button = (props: ButtonProps): JSX.Element => {
  const { variant = 'secondary', busy = false, type = 'button', disabled, ...rest } = props
  const isIconOnly = 'icon' in props
  const { label, icon, children, ...attributes } = rest as {
    label?: string
    icon?: ReactNode
    children?: ReactNode
  } & ButtonHTMLAttributes<HTMLButtonElement>

  return (
    <button
      {...attributes}
      type={type}
      className={`af-button af-button--${variant}`}
      disabled={disabled === true || busy}
      aria-busy={busy || undefined}
      aria-label={isIconOnly ? label : undefined}
    >
      {isIconOnly ? (
        <span aria-hidden="true" className="af-status__mark">
          {icon}
        </span>
      ) : (
        children
      )}
    </button>
  )
}
