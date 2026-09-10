/**
 * Review and export: the four compressions this module exists to prevent.
 *
 * Patch application, machine verification, human acceptance and external publication are four
 * different things with four different authorities. The prompt's objective is that they not be
 * compressed into one ambiguous Approve button, and most of these tests are about the boundaries
 * between them rather than about any one screen working.
 */

import { MemoryRouter } from 'react-router-dom'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { App } from '../App'
import { ApiClient } from '../api/client'
import { createFakeServer } from '../test/fakeServer'
import type { FakeServer, SessionResponse } from '../test/fakeServer'

const MEMBER: SessionResponse = {
  userId: 'u-1',
  email: 'reviewer@example.test',
  workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role: 'REVIEWER' }],
}

const REQUEST = {
  reviewRequestId: 'rq-1',
  patchDigest: '1'.repeat(64),
  verificationDigest: '2'.repeat(64),
  journeyVersionId: 'j-1',
  environmentDigest: '3'.repeat(64),
  requestedBy: 'u-2',
  requestedOf: 'u-1',
  requestedAt: '2026-09-10T12:00:00Z',
  reviewCount: 0,
}

const REVIEW = {
  reviewId: 'review-1',
  reviewRequestId: 'rq-1',
  reviewerId: 'u-1-abcdefgh',
  reviewerRole: 'REVIEWER',
  verdict: 'ACCEPT',
  observations: 'The error is announced when focus reaches it',
  limitations: 'VoiceOver only; no other reader was tried',
  usedAssistiveTechnology: true,
  assistiveTechnologyDetail: 'VoiceOver on macOS 26.6',
  boundTo: {
    patchDigest: '1'.repeat(64),
    verificationDigest: '2'.repeat(64),
    journeyVersionId: 'j-1',
    environmentDigest: '3'.repeat(64),
  },
  supersedes: null,
  supersededBy: null,
  submittedAt: '2026-09-10T12:30:00Z',
  meansNothingAbout: [
    'Whether the application is usable by people with disabilities in general.',
    'Permission to merge, deploy, publish or release anything.',
    'The machine outcome, which this does not rewrite and cannot overturn.',
  ],
}

const EXPORT = {
  exportId: 'export-1',
  runId: 'run-1',
  attemptId: 'attempt-1',
  bundleDigest: '4'.repeat(64),
  trustLevel: 'FULLY_VERIFIABLE',
  signingKeyId: 'af-2026-09',
  retentionAtExport: { transcripts: 'RETAINED', screenshots: 'DELETED' },
  createdAt: '2026-09-10T12:00:00Z',
  expiresAt: '2026-10-10T12:00:00Z',
  verifyWith: 'accessforge-verify <bundle> --trust-root <key supplied by the issuer>',
  limitations: [
    'Obtain the verifying key independently of the bundle.',
    'The retention state above is what was true at export time, not now.',
  ],
}

const renderAt = (server: FakeServer, path: string): void => {
  const client = new ApiClient({ fetchImpl: server.fetch, cookieSource: () => '' })
  render(
    <MemoryRouter initialEntries={[path]}>
      <App client={client} />
    </MemoryRouter>,
  )
}

describe('the review queue', () => {
  it('shows zero assessments as zero rather than as an answered request', async () => {
    const server = createFakeServer(MEMBER)
    server.data.reviewRequests.push(REQUEST)
    renderAt(server, '/w/ws-1/reviews/new')

    await screen.findByRole('heading', { level: 1, name: 'Reviews' })
    const table = await screen.findByRole('table', { name: /Reviews that have been asked for/ })
    // Treating an assignment as a review is how a process reports completed review that never
    // happened.
    expect(within(table).getByText('0')).toBeVisible()
    expect(screen.getByText(/nothing about whether they looked/)).toBeVisible()
  })

  it('says why there is nothing to review rather than showing an empty table', async () => {
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/reviews/new')

    expect(
      await screen.findByRole('heading', { name: 'Nobody has been asked to review anything' }),
    ).toBeVisible()
    expect(screen.getByText(/modules 14\s*\n?\s*and 15|modules 14 and 15/)).toBeVisible()
  })
})

describe('the review form', () => {
  const openForm = async (server: FakeServer): Promise<void> => {
    server.data.reviewRequests.push(REQUEST)
    renderAt(server, '/w/ws-1/reviews/new')
    await screen.findByRole('heading', { level: 1, name: 'Reviews' })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Review patch/ }))
  }

  it('shows exactly what is being assessed, in full', async () => {
    const server = createFakeServer(MEMBER)
    await openForm(server)
    expect(await screen.findByText('1'.repeat(64))).toBeVisible()
    expect(screen.getByText('2'.repeat(64))).toBeVisible()
  })

  it('refuses to record an assessment that does not say whether a reader was used', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openForm(server)

    await user.click(screen.getByRole('radio', { name: 'Accept' }))
    await user.type(screen.getByLabelText(/What you observed/), 'The error is announced')
    await user.click(screen.getByRole('button', { name: 'Record assessment' }))

    const summary = await screen.findByRole('alert')
    expect(summary).toHaveFocus()
    // Never inferred from a role, a default, or silence.
    expect(summary).toHaveTextContent(/never inferred/)
    expect(server.bodies.filter((entry) => entry.url.endsWith('/reviews'))).toEqual([])
  })

  it('requires unable-to-assess to say what was missing', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openForm(server)

    await user.click(screen.getByRole('radio', { name: 'Unable to assess' }))
    await user.type(screen.getByLabelText(/What you observed/), 'I could not open the transcript')
    await user.click(screen.getByRole('radio', { name: 'No' }))
    await user.click(screen.getByRole('button', { name: 'Record assessment' }))

    // The least useful record in the system reports that a person's time was spent and nothing
    // about why they could not answer.
    expect(await screen.findByRole('alert')).toHaveTextContent(/Say what was missing/)
  })

  it('sends both what the reviewer saw and what the request holds now', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openForm(server)

    await user.click(screen.getByRole('radio', { name: 'Accept' }))
    await user.type(screen.getByLabelText(/What you observed/), 'The error is announced')
    await user.click(screen.getByRole('radio', { name: 'Yes' }))
    await user.click(screen.getByRole('button', { name: 'Record assessment' }))

    const sent = server.bodies.find((entry) => entry.url.endsWith('/reviews'))?.body as
      | Record<string, unknown>
      | undefined
    // Both, so the server's staleness comparison has two values to compare. A form that read one
    // and sent it twice would make the check agree with itself.
    expect(sent?.['patchDigest']).toBe(REQUEST.patchDigest)
    expect(sent?.['currentPatchDigest']).toBe(REQUEST.patchDigest)
    expect(sent?.['usedAssistiveTechnology']).toBe(true)
  })

  it('asks nothing about who the reviewer is', async () => {
    const server = createFakeServer(MEMBER)
    await openForm(server)
    await screen.findByRole('group', { name: /assistive technology while reviewing/ })

    // The question is about this review, not about the person. Nothing asks for a disability, a
    // demographic, or study participation.
    const text = document.body.textContent ?? ''
    expect(text).toMatch(/not a question about you/)
    expect(text).not.toMatch(/disabilit(y|ies)\?|are you|do you have/i)
  })

  it('clears the reader detail when the reviewer says they used none', async () => {
    const user = userEvent.setup()
    const server = createFakeServer(MEMBER)
    await openForm(server)

    await user.click(screen.getByRole('radio', { name: 'Yes' }))
    await user.type(screen.getByLabelText(/Which assistive technology/), 'VoiceOver')
    await user.click(screen.getByRole('radio', { name: 'No' }))
    await user.click(screen.getByRole('radio', { name: 'Yes' }))

    // The server refuses detail from someone who reported using none: one of the two would be
    // wrong and the record must not guess which.
    expect(screen.getByLabelText(/Which assistive technology/)).toHaveValue('')
  })
})

describe('a recorded review', () => {
  it('shows the limits the server attached, not a list this interface composed', async () => {
    const server = createFakeServer(MEMBER)
    server.data.review = REVIEW
    renderAt(server, '/w/ws-1/reviews/review-1')

    await screen.findByRole('heading', { level: 1, name: /Review by/ })
    for (const limit of REVIEW.meansNothingAbout) {
      expect(screen.getByText(limit)).toBeVisible()
    }
  })

  it('states that it does not rewrite the machine outcome', async () => {
    const server = createFakeServer(MEMBER)
    server.data.review = REVIEW
    renderAt(server, '/w/ws-1/reviews/review-1')
    expect(await screen.findByText(/does not rewrite and cannot overturn/)).toBeVisible()
  })

  it('reports assistive-technology use with its version', async () => {
    const server = createFakeServer(MEMBER)
    server.data.review = REVIEW
    renderAt(server, '/w/ws-1/reviews/review-1')
    expect(await screen.findByText(/VoiceOver on macOS 26.6/)).toBeVisible()
  })

  it('shows a superseded review unchanged, saying a later one replaced it', async () => {
    const server = createFakeServer(MEMBER)
    server.data.review = { ...REVIEW, supersededBy: 'review-2' }
    renderAt(server, '/w/ws-1/reviews/review-1')
    expect(
      await screen.findByRole('heading', { name: 'A later assessment replaced this one' }),
    ).toBeVisible()
    // Unchanged: corrections are appended, not edits.
    expect(screen.getByText('ACCEPT')).toBeVisible()
  })
})

describe('the export screen', () => {
  it('puts the limitations above the instructions for using it', async () => {
    const server = createFakeServer(MEMBER)
    server.data.exportRecord = EXPORT
    renderAt(server, '/w/ws-1/exports/export-1')

    await screen.findByRole('heading', { level: 1, name: /Export export-/ })
    const headings = screen.getAllByRole('heading').map((h) => h.textContent)
    const limits = headings.indexOf('What this bundle does not establish')
    const checking = headings.indexOf('Checking it')
    expect(limits).toBeGreaterThan(-1)
    expect(limits).toBeLessThan(checking)
  })

  it('says a signature attributes rather than proves', async () => {
    const server = createFakeServer(MEMBER)
    server.data.exportRecord = EXPORT
    renderAt(server, '/w/ws-1/exports/export-1')
    expect(
      await screen.findByText(/does not make the manifest’s contents true/),
    ).toBeVisible()
    expect(screen.getByText(/a forger signs with their own key and embeds it/)).toBeVisible()
  })

  it('explains the trust levels the exporter actually produces', async () => {
    const server = createFakeServer(MEMBER)
    server.data.exportRecord = { ...EXPORT, trustLevel: 'INCOMPLETE' }
    renderAt(server, '/w/ws-1/exports/export-1')
    // A real bundle came back INCOMPLETE and the first version of this map fell through to the
    // unrecognised branch — correct behaviour, and the wrong words for the commonest case.
    expect(
      await screen.findByText(/something that should exist does not/),
    ).toBeVisible()
    expect(
      screen.queryByText(/This build does not recognise that trust level/),
    ).not.toBeInTheDocument()
  })

  it('still reports an unrecognised trust level as unrecognised', async () => {
    const server = createFakeServer(MEMBER)
    server.data.exportRecord = { ...EXPORT, trustLevel: 'SOMETHING_NEW' }
    renderAt(server, '/w/ws-1/exports/export-1')
    expect(
      await screen.findByText(/This build does not recognise that trust level/),
    ).toBeVisible()
  })

  it('says the retention shown is a snapshot, not the present', async () => {
    const server = createFakeServer(MEMBER)
    server.data.exportRecord = EXPORT
    renderAt(server, '/w/ws-1/exports/export-1')
    expect(await screen.findByText(/not what it holds now/)).toBeVisible()
  })

  it('offers no share link and says publication is a separate workflow', async () => {
    const server = createFakeServer(MEMBER)
    server.data.exportRecord = EXPORT
    renderAt(server, '/w/ws-1/exports/export-1')

    await screen.findByRole('heading', { name: 'This export is private' })
    expect(screen.getByText(/no share link/)).toBeVisible()
    expect(screen.getByText(/belongs to module 20, which does not exist/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /share|publish/i })).toBeNull()
  })

  it('does not describe the bundle as proof of accessibility', async () => {
    const server = createFakeServer(MEMBER)
    server.data.exportRecord = EXPORT
    renderAt(server, '/w/ws-1/exports/export-1')
    await screen.findByRole('heading', { level: 1, name: /Export export-/ })
    expect(
      screen.getByText(/not evidence that the application is usable by people with disabilities/),
    ).toBeVisible()
  })
})

describe('the repair workspace', () => {
  it('says no repair can exist rather than showing an inert diff and a disabled button', async () => {
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/patches/p-1')

    await screen.findByRole('heading', { level: 1, name: 'Proposed repair' })
    expect(
      screen.getByRole('heading', { name: 'No repair can exist in this build' }),
    ).toBeVisible()
    // No approval control of any kind: the thing that must never appear before there is something
    // to approve.
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.getByText(/would look the same as one waiting for data/)).toBeVisible()
  })

  it('says what each missing piece would have to establish', async () => {
    const server = createFakeServer(MEMBER)
    renderAt(server, '/w/ws-1/patches/p-1')
    await screen.findByRole('heading', { level: 1, name: 'Proposed repair' })
    expect(screen.getByText(/Reviewer agreement is not a substitute for reproduction/)).toBeVisible()
    expect(
      screen.getByText(/does not override a failing functional requirement/),
    ).toBeVisible()
  })
})
