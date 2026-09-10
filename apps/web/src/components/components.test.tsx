/**
 * The primitives, exercised the way the people who depend on them use them.
 *
 * Keyboard and accessible name, not appearance. A screenshot test would pass on a button with no
 * accessible name, a dialog that traps focus after unmount, and a status conveyed only by colour —
 * which are the three defects these components exist to prevent.
 */

import type { JSX } from 'react'

import { useState } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Button } from './Button'
import { DataTable } from './DataTable'
import { Dialog } from './Dialog'
import { ErrorSummary } from './ErrorSummary'
import { FormField } from './FormField'
import { Notice } from './Notice'
import { Pagination } from './Pagination'
import { RunOutcomeBadge, RunStatusBadge } from './StatusBadge'
import { Tabs } from './Tabs'
import {
  DependencyUnavailableState,
  EmptyState,
  FailureState,
  LoadingState,
  NotBuiltYetState,
  NotFoundState,
  PermissionDeniedState,
} from './states'
import type { Problem } from '../api/problem'

const problem = (overrides: Partial<Problem> = {}): Problem => ({
  code: 'CONFLICT',
  title: 'Conflict',
  detail: 'Something conflicted.',
  status: 409,
  requestId: 'req-1',
  ...overrides,
})

describe('Button', () => {
  it('defaults to type=button so it cannot submit a form it merely sits inside', () => {
    render(<Button>Do the thing</Button>)
    expect(screen.getByRole('button', { name: 'Do the thing' })).toHaveAttribute('type', 'button')
  })

  it('gives an icon-only button an accessible name', () => {
    render(<Button label="Copy digest" icon="⧉" />)
    expect(screen.getByRole('button', { name: 'Copy digest' })).toBeInTheDocument()
  })

  it('hides the icon itself from assistive technology', () => {
    render(<Button label="Copy digest" icon="⧉" />)
    expect(screen.getByRole('button', { name: 'Copy digest' }).textContent).toBe('⧉')
    expect(screen.getByText('⧉')).toHaveAttribute('aria-hidden', 'true')
  })

  it('keeps its label while busy, rather than changing to a progress word', async () => {
    // A control whose text changes to "Saving…" leaves a screen-reader user hearing a different
    // button from the one they pressed, and breaks any script that found it by name.
    const { rerender } = render(<Button>Freeze version</Button>)
    rerender(<Button busy>Freeze version</Button>)
    const button = await screen.findByRole('button', { name: 'Freeze version' })
    expect(button).toHaveAttribute('aria-busy', 'true')
    expect(button).toBeDisabled()
  })
})

describe('FormField', () => {
  it('associates the label with the control', () => {
    render(
      <FormField label="Project name">
        {({ id, describedBy, invalid }) => (
          <input id={id} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
        )}
      </FormField>,
    )
    expect(screen.getByLabelText('Project name')).toBeInTheDocument()
  })

  it('announces the hint and the error together rather than one replacing the other', () => {
    render(
      <FormField label="Origin" hint="The exact permitted origin." error="This is required.">
        {({ id, describedBy, invalid }) => (
          <input id={id} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
        )}
      </FormField>,
    )
    const input = screen.getByLabelText(/Origin/)
    expect(input).toHaveAccessibleDescription('The exact permitted origin. This is required.')
    expect(input).toHaveAttribute('aria-invalid', 'true')
  })

  it('does not mark a field invalid when there is no error to describe', () => {
    render(
      <FormField label="Origin">
        {({ id, describedBy, invalid }) => (
          <input id={id} aria-describedby={describedBy} aria-invalid={invalid || undefined} />
        )}
      </FormField>,
    )
    expect(screen.getByLabelText('Origin')).not.toHaveAttribute('aria-invalid')
  })

  it('marks a required field in text, not with an asterisk and a legend elsewhere', () => {
    render(
      <FormField label="Intent" required>
        {({ id }) => <input id={id} />}
      </FormField>,
    )
    expect(screen.getByText('(required)')).toBeInTheDocument()
  })
})

describe('ErrorSummary', () => {
  it('takes focus after a failed submission', () => {
    render(
      <ErrorSummary submissionId={1} errors={[{ fieldId: 'name', message: 'Name is required.' }]} />,
    )
    expect(screen.getByRole('alert')).toHaveFocus()
  })

  it('returns focus on a second identical failure', () => {
    const errors = [{ fieldId: 'name', message: 'Name is required.' }]
    const { rerender } = render(<ErrorSummary submissionId={1} errors={errors} />)
    const summary = screen.getByRole('alert')
    ;(document.activeElement as HTMLElement).blur()
    expect(summary).not.toHaveFocus()

    rerender(<ErrorSummary submissionId={2} errors={errors} />)
    // Without this, the second attempt looks to a screen-reader user exactly like nothing happening.
    expect(screen.getByRole('alert')).toHaveFocus()
  })

  it('links each entry to the control it is about', () => {
    render(
      <ErrorSummary submissionId={1} errors={[{ fieldId: 'origin', message: 'Bad origin.' }]} />,
    )
    expect(screen.getByRole('link', { name: 'Bad origin.' })).toHaveAttribute('href', '#origin')
  })

  it('renders nothing at all when there are no errors', () => {
    render(<ErrorSummary submissionId={0} errors={[]} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('is not in the tab order', async () => {
    render(
      <>
        <button type="button">Before</button>
        <ErrorSummary submissionId={1} errors={[{ fieldId: 'a', message: 'Bad.' }]} />
        <button type="button">After</button>
      </>,
    )
    screen.getByRole('button', { name: 'Before' }).focus()
    await userEvent.tab()
    // Straight to the link inside it, not onto the container. A focusable container would be an
    // extra stop everyone tabs past on every visit.
    expect(screen.getByRole('link', { name: 'Bad.' })).toHaveFocus()
  })
})

const DialogHarness = (): JSX.Element => {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button onClick={() => setOpen(true)}>Approve repair…</Button>
      <Dialog
        open={open}
        heading="Approve this candidate build"
        onClose={() => setOpen(false)}
        actions={
          <>
            <Button variant="primary" onClick={() => setOpen(false)}>
              Approve isolated build
            </Button>
            <Button onClick={() => setOpen(false)}>Keep it unapproved</Button>
          </>
        }
      >
        <p>Scope PATCH_APPLY. Does not merge or deploy.</p>
      </Dialog>
    </>
  )
}

describe('Dialog', () => {
  it('is operable with the keyboard alone and returns focus to the trigger', async () => {
    const user = userEvent.setup()
    render(<DialogHarness />)

    const trigger = screen.getByRole('button', { name: 'Approve repair…' })
    trigger.focus()
    await user.keyboard('{Enter}')

    const dialog = screen.getByRole('dialog', { name: 'Approve this candidate build' })
    expect(dialog).toBeVisible()

    await user.click(within(dialog).getByRole('button', { name: 'Keep it unapproved' }))
    // Returned explicitly, not left to the browser: the browser's restoration does not survive the
    // trigger being re-rendered, and losing focus to <body> is the failure this prevents.
    expect(trigger).toHaveFocus()
  })

  it('is named by its heading rather than announced as an unnamed dialog', async () => {
    const user = userEvent.setup()
    render(<DialogHarness />)
    await user.click(screen.getByRole('button', { name: 'Approve repair…' }))
    expect(screen.getByRole('dialog', { name: 'Approve this candidate build' })).toBeInTheDocument()
  })

  it('routes the browser’s own Escape handling through one close path', async () => {
    const user = userEvent.setup()
    render(<DialogHarness />)
    const trigger = screen.getByRole('button', { name: 'Approve repair…' })
    await user.click(trigger)
    const dialog = screen.getByRole('dialog')
    fireEvent(dialog, new Event('cancel', { bubbles: false, cancelable: true }))
    // Closed via the caller's state, so the element and the caller cannot disagree about whether it
    // is open.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('offers consequence-named actions and no generic Confirm', async () => {
    const user = userEvent.setup()
    render(<DialogHarness />)
    await user.click(screen.getByRole('button', { name: 'Approve repair…' }))
    expect(screen.queryByRole('button', { name: /^(OK|Confirm|Yes)$/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve isolated build' })).toBeInTheDocument()
  })
})

const TabsHarness = (): JSX.Element => {
  const [selected, setSelected] = useState('manifest')
  return (
    <Tabs
      label="Run detail"
      selectedId={selected}
      onSelect={setSelected}
      tabs={[
        { id: 'manifest', label: 'Manifest', content: <p>Manifest contents</p> },
        { id: 'timeline', label: 'Timeline', content: <p>Timeline contents</p> },
        { id: 'evidence', label: 'Evidence', content: <p>Evidence contents</p> },
      ]}
    />
  )
}

describe('Tabs', () => {
  it('keeps only the selected tab in the tab order', async () => {
    render(<TabsHarness />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((tab) => tab.getAttribute('tabindex'))).toEqual(['0', '-1', '-1'])
  })

  it('moves with the arrow keys and wraps at both ends', async () => {
    const user = userEvent.setup()
    render(<TabsHarness />)
    screen.getByRole('tab', { name: 'Manifest' }).focus()

    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Timeline' })).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Timeline' })).toHaveAttribute('aria-selected', 'true')

    await user.keyboard('{ArrowLeft}{ArrowLeft}')
    expect(screen.getByRole('tab', { name: 'Evidence' })).toHaveFocus()

    await user.keyboard('{Home}')
    expect(screen.getByRole('tab', { name: 'Manifest' })).toHaveFocus()

    await user.keyboard('{End}')
    expect(screen.getByRole('tab', { name: 'Evidence' })).toHaveFocus()
  })

  it('labels each panel with its tab', () => {
    render(<TabsHarness />)
    expect(screen.getByRole('tabpanel', { name: 'Manifest' })).toBeInTheDocument()
  })
})

describe('StatusBadge', () => {
  it('says which kind of value it is, so two bare words are not ambiguous', () => {
    render(
      <>
        <RunStatusBadge status="COMPLETED" />
        <RunOutcomeBadge outcome="INCONCLUSIVE" />
      </>,
    )
    expect(screen.getByText('Status:')).toBeInTheDocument()
    expect(screen.getByText('Outcome:')).toBeInTheDocument()
  })

  it('carries the status in text, not in colour', () => {
    render(<RunOutcomeBadge outcome="FAIL" />)
    // The text is the assertion. A reader who cannot distinguish the colours, or who is listening,
    // gets the same information.
    expect(screen.getByText('FAIL')).toBeInTheDocument()
  })

  it('does not render COMPLETED with a success appearance', () => {
    const { container } = render(<RunStatusBadge status="COMPLETED" />)
    // "Ended" says nothing about what was established. A green COMPLETED would be the UI deriving a
    // verdict, which is the server's job.
    expect(container.querySelector('.af-status--pass')).toBeNull()
  })

  it('does not render RUNNING with a success appearance', () => {
    const { container } = render(<RunStatusBadge status="RUNNING" />)
    expect(container.querySelector('.af-status--pass')).toBeNull()
  })
})

describe('the operational states', () => {
  it('renders loading as a message, with no content-shaped placeholder', () => {
    const { container } = render(<LoadingState what="runs" />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading runs…')
    // A skeleton is a claim that content is coming, and is indistinguishable from content that
    // arrived empty or failed.
    expect(container.querySelectorAll('table, li').length).toBe(0)
  })

  it('says which kind of nothing an empty state is', () => {
    render(<EmptyState heading="No runs" because="filtered-out" />)
    expect(screen.getByText(/filters exclude every record/)).toBeInTheDocument()
  })

  it('renders a failure as a problem with its request id, never as an empty list', () => {
    render(<FailureState problem={problem()} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Something conflicted.')
    expect(alert).toHaveTextContent('req-1')
  })

  it('offers a retry only when the caller supplies one', () => {
    const { rerender } = render(<FailureState problem={problem()} />)
    expect(screen.queryByRole('button', { name: /Try loading this again/ })).not.toBeInTheDocument()
    rerender(<FailureState problem={problem()} onRetry={vi.fn()} />)
    expect(screen.getByRole('button', { name: /Try loading this again/ })).toBeInTheDocument()
  })

  it('keeps permission-denied and not-found as different screens', () => {
    const { unmount } = render(<PermissionDeniedState problem={problem({ code: 'PERMISSION_DENIED' })} />)
    expect(screen.getByText(/do not have permission/)).toBeInTheDocument()
    unmount()

    render(<NotFoundState />)
    // And not-found must not mention permission at all: doing so would reconstruct the cross-tenant
    // existence oracle module 18 closed.
    expect(screen.queryByText(/permission/i)).not.toBeInTheDocument()
    expect(screen.getByText(/same answer/)).toBeInTheDocument()
  })

  it('says a dependency failure is not about the request', () => {
    render(<DependencyUnavailableState problem={problem({ code: 'DEPENDENCY_UNAVAILABLE' })} />)
    expect(screen.getByText(/server-side dependency/)).toBeInTheDocument()
  })

  it('names the module that owns an unbuilt screen and requests nothing', () => {
    render(<NotBuiltYetState screen="Run" ownedByModule={23} />)
    expect(screen.getByRole('heading', { name: 'Run is not built yet' })).toBeInTheDocument()
    expect(screen.getByText(/belongs to module 23/)).toBeInTheDocument()
    expect(screen.getByText(/nothing shown on this page is data/)).toBeInTheDocument()
  })
})

describe('DataTable', () => {
  it('renders real table semantics with a caption and column headers', () => {
    render(
      <DataTable
        caption="Recent runs"
        rowKey={(row: { id: string }) => row.id}
        rows={[{ id: 'r1' }, { id: 'r2' }]}
        columns={[
          { key: 'id', header: 'Run', cell: (row) => row.id, isRowHeader: true },
          { key: 'status', header: 'Status', cell: () => 'QUEUED' },
        ]}
      />,
    )
    const table = screen.getByRole('table', { name: 'Recent runs' })
    expect(within(table).getByRole('columnheader', { name: 'Run' })).toBeInTheDocument()
    expect(within(table).getAllByRole('rowheader')).toHaveLength(2)
  })

  it('makes its scroll container reachable by keyboard', () => {
    const { container } = render(
      <DataTable
        caption="Recent runs"
        rowKey={(row: { id: string }) => row.id}
        rows={[]}
        columns={[{ key: 'id', header: 'Run', cell: (row) => row.id }]}
      />,
    )
    // A scrollable region that cannot be focused is content that cannot be read without a mouse.
    expect(container.querySelector('.af-table-scroll')).toHaveAttribute('tabindex', '0')
  })
})

describe('Pagination', () => {
  it('describes the position without inventing a total page count', () => {
    render(
      <Pagination
        label="Runs"
        onPrevious={null}
        onNext={vi.fn()}
        positionDescription="Showing the first 50 runs."
      />,
    )
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Showing the first 50 runs.')
    expect(screen.queryByText(/of \d+ pages/)).not.toBeInTheDocument()
  })
})

describe('Notice', () => {
  it('is not a live region when it is part of the page on arrival', () => {
    render(<Notice tone="information" heading="Scope" headingLevel={2}>body</Notice>)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('is an alert only for a problem the person needs now', () => {
    const { unmount } = render(
      <Notice tone="problem" heading="Refused" headingLevel={2} live>
        body
      </Notice>,
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    unmount()
    render(
      <Notice tone="warning" heading="Stale" headingLevel={2} live>
        body
      </Notice>,
    )
    // Polite, because an assertive region interrupts whatever the reader was in the middle of.
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('is dismissible only when the caller says the message may be dismissed', async () => {
    const user = userEvent.setup()
    const onDismiss = vi.fn()
    const { rerender } = render(
      <Notice tone="warning" heading="Stale data" headingLevel={2}>
        body
      </Notice>,
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()

    rerender(
      <Notice tone="warning" heading="Stale data" headingLevel={2} onDismiss={onDismiss}>
        body
      </Notice>,
    )
    // Named for what it dismisses. Three notices with three buttons all called "Dismiss" is a
    // reader moving between identical controls with no way to tell them apart.
    await user.click(screen.getByRole('button', { name: 'Dismiss: Stale data' }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('takes its heading level from the caller so the outline stays correct', () => {
    render(<Notice tone="information" heading="Nested" headingLevel={3}>body</Notice>)
    expect(screen.getByRole('heading', { level: 3, name: 'Nested' })).toBeInTheDocument()
  })
})
