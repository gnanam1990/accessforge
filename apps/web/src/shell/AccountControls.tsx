/**
 * The signed-in identity, the theme choice and sign-out.
 *
 * Sign-out is a plain button rather than a confirmation dialog: it is reversible by signing in again,
 * and a confirmation on a safe action trains people to dismiss confirmations on unsafe ones.
 *
 * The theme control is a radio group, not a two-state toggle. There are three states — light, dark
 * and follow the system — and a toggle cannot express the third, which is the default and the one
 * most people want.
 */

import type { JSX } from 'react'

import { Button } from '../components/Button'
import { useTheme } from '../a11y/ThemeProvider'
import type { ThemeChoice } from '../a11y/ThemeProvider'

const CHOICES: readonly { readonly value: ThemeChoice; readonly label: string }[] = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
]

export const AccountControls = ({
  email,
  onSignOut,
}: {
  readonly email: string
  readonly onSignOut: () => void
}): JSX.Element => {
  const { choice, setChoice } = useTheme()

  return (
    <div className="af-row">
      <fieldset style={{ border: 0, margin: 0, padding: 0 }}>
        <legend className="af-visually-hidden">Colour theme</legend>
        <div className="af-row">
          {CHOICES.map((option) => (
            <label key={option.value} className="af-row" style={{ gap: 'var(--af-space-1)' }}>
              <input
                type="radio"
                name="af-theme"
                value={option.value}
                checked={choice === option.value}
                onChange={() => setChoice(option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      </fieldset>
      <p className="af-secondary" style={{ margin: 0 }}>
        Signed in as {email}
      </p>
      <Button variant="secondary" onClick={onSignOut}>
        Sign out
      </Button>
    </div>
  )
}
