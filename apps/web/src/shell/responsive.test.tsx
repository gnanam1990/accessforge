/**
 * The navigation at the widths UI-UX section 8 names.
 *
 * The compact form is a labelled disclosure, not an icon. An unlabelled hamburger glyph is the single
 * most common icon-only control with no accessible name on the web, and every entry behind it becomes
 * unreachable for a reader who cannot guess what the glyph is.
 */

import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { PrimaryNavigation } from './PrimaryNavigation'

const renderNav = (narrow: boolean): void => {
  render(
    <MemoryRouter>
      <PrimaryNavigation workspaceId="ws-1" narrow={narrow} />
    </MemoryRouter>,
  )
}

describe('the primary navigation', () => {
  it('is a labelled landmark at every width', () => {
    renderNav(false)
    expect(screen.getByRole('navigation', { name: 'Workspace sections' })).toBeInTheDocument()
  })

  it('shows every entry directly at desktop widths', () => {
    renderNav(false)
    const nav = screen.getByRole('navigation', { name: 'Workspace sections' })
    expect(within(nav).getAllByRole('link')).toHaveLength(4)
  })

  it('collapses into a disclosure with a text label, not a glyph', async () => {
    const user = userEvent.setup()
    renderNav(true)

    const toggle = screen.getByText('Menu')
    expect(toggle.tagName.toLowerCase()).toBe('summary')

    await user.click(toggle)
    const nav = screen.getByRole('navigation', { name: 'Workspace sections' })
    expect(within(nav).getAllByRole('link')).toHaveLength(4)
  })

  it('keeps every entry in the document even while collapsed', () => {
    // `<details>` hides its content without removing it, so a reader's own find-in-page and a
    // browser's in-page search still reach the entries.
    renderNav(true)
    const nav = screen.getByRole('navigation', { name: 'Workspace sections' })
    expect(within(nav).getAllByRole('link', { hidden: true })).toHaveLength(4)
  })
})
