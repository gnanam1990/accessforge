/**
 * The live region, and the restraint it is built around.
 *
 * The failure this guards against is concrete: a run emits events continuously, and a live region
 * fed from that stream reads every one of them. A screen-reader user then cannot hear anything else,
 * including the control they are trying to operate.
 */

import type { JSX } from 'react'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { AnnouncerProvider, useAnnouncer } from './Announcer'

const Harness = ({ messages }: { readonly messages: readonly string[] }): JSX.Element => {
  const { announce } = useAnnouncer()
  return (
    <button type="button" onClick={() => messages.forEach(announce)}>
      Announce
    </button>
  )
}

const renderHarness = (messages: readonly string[]): HTMLElement => {
  const { container } = render(
    <AnnouncerProvider>
      <Harness messages={messages} />
    </AnnouncerProvider>,
  )
  return container.querySelector('[aria-live]') as HTMLElement
}

describe('the announcer', () => {
  it('is polite, never assertive', async () => {
    const region = renderHarness(['Run interrupted; 1 unresolved action.'])
    // Assertive would interrupt the reader mid-sentence. A genuine alert belongs on the page, where
    // it can be re-read.
    expect(region).toHaveAttribute('aria-live', 'polite')
  })

  it('keeps at most three pending messages', async () => {
    const user = userEvent.setup()
    const region = renderHarness(['one', 'two', 'three', 'four', 'five'])
    await user.click(screen.getByRole('button'))
    // A queue that grew without limit would still be reading the first minute's events long after
    // they stopped mattering.
    expect(region.querySelectorAll('p')).toHaveLength(3)
    expect(region.textContent).toBe('threefourfive')
  })

  it('does not repeat the message it just made', async () => {
    const user = userEvent.setup()
    const region = renderHarness(['Saved.', 'Saved.'])
    await user.click(screen.getByRole('button'))
    expect(region.querySelectorAll('p')).toHaveLength(1)
  })

  it('reads a repetition that is separated by something else', async () => {
    const user = userEvent.setup()
    const region = renderHarness(['Saved.', 'Queued.', 'Saved.'])
    await user.click(screen.getByRole('button'))
    expect(region.textContent).toBe('Saved.Queued.Saved.')
  })

  it('never moves focus', async () => {
    const user = userEvent.setup()
    renderHarness(['Run interrupted.'])
    const button = screen.getByRole('button')
    button.focus()
    await user.click(button)
    // Focus belongs to the person. UI-UX section 3: background events do not move focus.
    expect(button).toHaveFocus()
  })

  it('is hidden visually but present in the accessibility tree', async () => {
    const region = renderHarness([])
    expect(region.className).toContain('af-visually-hidden')
    // `display: none` would take it out of the accessibility tree along with the screen.
    expect(region).toBeVisible()
  })
})
