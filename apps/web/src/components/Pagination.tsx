/**
 * Keyset pagination controls.
 *
 * Next and previous, not numbered pages. The API pages by cursor — module 18 chose a keyset over an
 * `OFFSET` because offsets skip and repeat rows as a table changes underneath a reader — and a
 * numbered control would imply a total page count the server never computes, and a "page 7" a
 * cursor cannot address.
 *
 * The control announces what it did. A person who presses Next and hears nothing has no way to know
 * whether the table changed, because the button itself does not move.
 */

import type { JSX } from 'react'

import { Button } from './Button'

export interface PaginationProps {
  readonly label: string
  readonly onPrevious: (() => void) | null
  readonly onNext: (() => void) | null
  /** Shown as text. Never "page 3 of 9": the server does not know the total. */
  readonly positionDescription: string
}

export const Pagination = ({
  label,
  onPrevious,
  onNext,
  positionDescription,
}: PaginationProps): JSX.Element => (
  <nav className="af-row" aria-label={label}>
    <Button
      variant="secondary"
      disabled={onPrevious === null}
      onClick={() => onPrevious?.()}
    >
      Previous
    </Button>
    <Button variant="secondary" disabled={onNext === null} onClick={() => onNext?.()}>
      Next
    </Button>
    <p className="af-secondary" role="status">
      {positionDescription}
    </p>
  </nav>
)
