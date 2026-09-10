/**
 * One recorded assessment, and the form for making one.
 *
 * The screen exists to stop four compressions, each of which is the shape a review interface takes
 * when nobody is watching.
 *
 * **Being asked is not reviewing.** The queue shows how many assessments each request has received,
 * and zero is displayed as zero. A process that treated assignment as review would report completed
 * review that never happened.
 *
 * **A review is bound to exact material, and the binding is checked by the server.** The form sends
 * both the digests the reviewer looked at *and* the digests the system holds now. That looks
 * redundant and is not: comparing a record to itself always agrees, so a form that read one value
 * and sent it twice would make the staleness check pass unconditionally while appearing to protect
 * something.
 *
 * **Assistive-technology use is asked, never inferred.** It is a required choice with no default,
 * because "a person accepted this" and "a person accepted this having driven it with a screen
 * reader" are different claims and the second cannot be assumed from a role, a checkbox elsewhere,
 * or silence.
 *
 * **A review authorizes nothing.** The limits come from the server with the record, so this
 * interface cannot shorten them — and the shortest version of that list is the one that makes an
 * ACCEPT look like a release decision.
 *
 * No demographic or disability question is asked anywhere on this screen. Whether the reviewer used
 * assistive technology is a fact about *this review*; whether they are a disabled person is not
 * asked, not stored, and not inferable from the answer.
 */

import { useId, useState } from 'react'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { ErrorSummary } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/states'
import { getReview, listReviewRequests, submitReview } from '../api/resources'
import type { ReviewRequest } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { useReviewId } from './useReviewId'

const VERDICTS = [
  {
    value: 'ACCEPT',
    label: 'Accept',
    meaning: 'The repair does what it claims, for the journey and profile stated below.',
  },
  {
    value: 'CHANGES_REQUESTED',
    label: 'Changes requested',
    meaning: 'Something specific is wrong with it. Say what in the observations.',
  },
  {
    value: 'UNABLE_TO_ASSESS',
    label: 'Unable to assess',
    meaning:
      'Something needed to judge it was missing. This is a useful answer, and it requires saying ' +
      'what was missing.',
  },
] as const

const ReviewForm = ({
  workspaceId,
  reviewRequest,
  onSubmitted,
}: {
  readonly workspaceId: string
  readonly reviewRequest: ReviewRequest
  readonly onSubmitted: () => void
}): JSX.Element => {
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const verdictId = useId()
  const observationsId = useId()
  const limitationsId = useId()
  const atId = useId()
  const atDetailId = useId()

  const [verdict, setVerdict] = useState('')
  const [observations, setObservations] = useState('')
  const [limitations, setLimitations] = useState('')
  const [usedAt, setUsedAt] = useState<'yes' | 'no' | ''>('')
  const [atDetail, setAtDetail] = useState('')
  const [errors, setErrors] = useState<readonly { fieldId: string; message: string }[]>([])
  const [submissionId, setSubmissionId] = useState(0)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const submit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSubmissionId((current) => current + 1)
    setRefusal(null)

    const found: { fieldId: string; message: string }[] = []
    if (verdict === '') found.push({ fieldId: verdictId, message: 'Choose an assessment.' })
    if (observations.trim() === '') {
      found.push({ fieldId: observationsId, message: 'Say what you observed.' })
    }
    if (usedAt === '') {
      found.push({
        fieldId: atId,
        message:
          'Say whether you used assistive technology while reviewing. It is never inferred, ' +
          'because reading a transcript and driving the page with a screen reader are different ' +
          'claims.',
      })
    }
    if (verdict === 'UNABLE_TO_ASSESS' && limitations.trim() === '') {
      found.push({
        fieldId: limitationsId,
        message:
          'Say what was missing. "Unable to assess" without that records only that your time was ' +
          'spent.',
      })
    }
    setErrors(found)
    if (found.length > 0) return

    setBusy(true)
    const outcome = await submitReview(client, workspaceId, {
      patchDigest: reviewRequest.patchDigest,
      verificationDigest: reviewRequest.verificationDigest,
      journeyVersionId: reviewRequest.journeyVersionId,
      environmentDigest: reviewRequest.environmentDigest,
      // What the reviewer looked at, and what the request records now. The server compares them; a
      // form that sent one value twice would make that comparison agree with itself.
      currentPatchDigest: reviewRequest.patchDigest,
      currentVerificationDigest: reviewRequest.verificationDigest,
      requestId: reviewRequest.reviewRequestId,
      verdict,
      observations: observations.trim(),
      limitations: limitations.trim(),
      usedAssistiveTechnology: usedAt === 'yes',
      ...(usedAt === 'yes' && atDetail.trim() !== ''
        ? { assistiveTechnologyDetail: atDetail.trim() }
        : {}),
    })
    setBusy(false)

    switch (outcome.kind) {
      case 'ok':
      case 'accepted':
        announce('Assessment recorded.')
        onSubmitted()
        break
      case 'problem':
        setRefusal(outcome.problem.detail)
        break
      case 'offline':
        setRefusal(
          'The server did not answer, so whether the assessment was recorded is unknown. Read ' +
            'this request again before submitting a second one.',
        )
        break
      case 'cancelled':
      case 'stale':
      case 'unauthenticated':
        break
    }
  }

  return (
    <div className="af-panel af-stack">
      <h3>Record your assessment</h3>

      <Notice tone="information" heading="What this record is, and is not" headingLevel={4}>
        <p>
          It is your judgement, attributable to you. It does not rewrite the machine outcome, cannot
          make an inconclusive result pass, and is not permission to merge, deploy or publish
          anything.
        </p>
      </Notice>

      <ErrorSummary submissionId={submissionId} errors={errors} />

      {refusal !== null && (
        <Notice tone="problem" heading="The assessment was not recorded" headingLevel={4} live>
          <p>{refusal}</p>
        </Notice>
      )}

      <form onSubmit={(event) => void submit(event)} noValidate>
        <fieldset id={verdictId} style={{ border: 0, padding: 0, margin: 0 }}>
          <legend>Your assessment</legend>
          {VERDICTS.map((option) => (
            <div key={option.value}>
              <label className="af-row" style={{ gap: 'var(--af-space-2)' }}>
                <input
                  type="radio"
                  name="verdict"
                  value={option.value}
                  checked={verdict === option.value}
                  onChange={() => setVerdict(option.value)}
                />
                {option.label}
              </label>
              <p className="af-secondary">{option.meaning}</p>
            </div>
          ))}
        </fieldset>

        <FormField
          id={observationsId}
          label="What you observed"
          hint="Specific enough that somebody else could look at the same thing."
          required
        >
          {({ id, describedBy, invalid }) => (
            <textarea
              id={id}
              rows={4}
              value={observations}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
              onChange={(event) => setObservations(event.target.value)}
            />
          )}
        </FormField>

        <FormField
          id={limitationsId}
          label="What you could not judge"
          hint="Required when you are unable to assess. Useful in every other case too."
        >
          {({ id, describedBy, invalid }) => (
            <textarea
              id={id}
              rows={3}
              value={limitations}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
              onChange={(event) => setLimitations(event.target.value)}
            />
          )}
        </FormField>

        <fieldset id={atId} style={{ border: 0, padding: 0, margin: 0 }}>
          <legend>Did you use assistive technology while reviewing this?</legend>
          <p className="af-secondary">
            A fact about this review, asked because it changes what the record claims. It is not a
            question about you, and nothing here asks whether you are a disabled person.
          </p>
          <label className="af-row" style={{ gap: 'var(--af-space-2)' }}>
            <input
              type="radio"
              name="used-at"
              value="yes"
              checked={usedAt === 'yes'}
              onChange={() => setUsedAt('yes')}
            />
            Yes
          </label>
          <label className="af-row" style={{ gap: 'var(--af-space-2)' }}>
            <input
              type="radio"
              name="used-at"
              value="no"
              checked={usedAt === 'no'}
              onChange={() => {
                setUsedAt('no')
                // Cleared, because the server refuses detail from someone who reported using none —
                // one of the two would be wrong and the record must not guess which.
                setAtDetail('')
              }}
            />
            No
          </label>
        </fieldset>

        {usedAt === 'yes' && (
          <FormField
            id={atDetailId}
            label="Which assistive technology, and which version"
            hint="A different reader version announces differently, so the version is part of the claim."
          >
            {({ id, describedBy }) => (
              <input
                id={id}
                value={atDetail}
                aria-describedby={describedBy}
                onChange={(event) => setAtDetail(event.target.value)}
              />
            )}
          </FormField>
        )}

        <Button type="submit" variant="primary" busy={busy}>
          Record assessment
        </Button>
      </form>
    </div>
  )
}

const ReviewQueue = ({ workspaceId }: { readonly workspaceId: string }): JSX.Element => {
  const { client } = useSession()
  const [submitted, setSubmitted] = useState(0)
  const requests = useResource(
    (signal) => listReviewRequests(client, workspaceId, signal),
    [client, workspaceId, submitted],
  )
  const [chosen, setChosen] = useState<string | null>(null)

  return (
    <ResourceView resource={requests} what="the reviews that have been asked for">
      {(page) => {
        const selected = page.items.find((item) => item.reviewRequestId === chosen)
        return page.items.length === 0 ? (
          <EmptyState heading="Nobody has been asked to review anything" because="nothing-created-yet">
            <p className="af-secondary">
              A review request binds a patch digest and a verification digest. Nothing in this system
              produces either yet — the patch sandbox and the candidate verifier belong to modules 14
              and 15, which do not exist — so there is nothing to be asked about.
            </p>
          </EmptyState>
        ) : (
          <>
            <Notice tone="information" heading="How to read this list" headingLevel={3}>
              <p>{page.meaning}</p>
            </Notice>
            {!page.complete && (
              <Notice tone="warning" heading="This list is not complete" headingLevel={3}>
                <p>
                  There are more requests than this screen read. What is below is a prefix, and a
                  request that is not shown is still waiting for somebody.
                </p>
              </Notice>
            )}
            <DataTable<ReviewRequest>
              caption="Reviews that have been asked for, and how many assessments each has received"
              rows={page.items}
              rowKey={(item) => item.reviewRequestId}
              columns={[
                {
                  key: 'patch',
                  header: 'Patch',
                  isRowHeader: true,
                  cell: (item) => <code>{item.patchDigest.slice(0, 16)}…</code>,
                },
                {
                  key: 'asked',
                  header: 'Asked at',
                  cell: (item) => <time dateTime={item.requestedAt}>{item.requestedAt}</time>,
                },
                {
                  key: 'assessments',
                  header: 'Assessments recorded',
                  cell: (item) => (
                    <StatusBadge tone={item.reviewCount === 0 ? 'neutral' : 'pass'} kind="Reviews">
                      {String(item.reviewCount)}
                    </StatusBadge>
                  ),
                },
                {
                  key: 'open',
                  header: 'Review',
                  cell: (item) => (
                    <Button onClick={() => setChosen(item.reviewRequestId)}>
                      Review patch {item.patchDigest.slice(0, 8)}
                    </Button>
                  ),
                },
              ]}
            />

            {selected !== undefined && (
              <>
                <h3>What you are being asked to assess</h3>
                <dl>
                  <dt>Patch digest</dt>
                  <dd>
                    <code>{selected.patchDigest}</code>
                  </dd>
                  <dt>Verification digest</dt>
                  <dd>
                    <code>{selected.verificationDigest}</code>
                  </dd>
                  <dt>Journey version</dt>
                  <dd>
                    <code>{selected.journeyVersionId}</code>
                  </dd>
                  <dt>Environment digest</dt>
                  <dd>
                    <code>{selected.environmentDigest}</code>
                  </dd>
                </dl>
                <ReviewForm
                  workspaceId={workspaceId}
                  reviewRequest={selected}
                  onSubmitted={() => {
                    setChosen(null)
                    setSubmitted((current) => current + 1)
                  }}
                />
              </>
            )}
          </>
        )
      }}
    </ResourceView>
  )
}

export const ReviewScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const reviewId = useReviewId()
  const { client } = useSession()
  // `new` is not an identifier, so no record is asked for. Reading it anyway would send a request
  // the server can only refuse, and put a 400 in the log for a page that is working correctly.
  const review = useResource(
    (signal) =>
      reviewId === 'new'
        ? Promise.resolve({ kind: 'cancelled' as const })
        : getReview(client, workspaceId, reviewId, signal),
    [client, workspaceId, reviewId],
  )

  // The route carries a review id. `new` is the one value that is not one: it opens the queue
  // instead of a record, so a person can reach the form by a stable link.
  if (reviewId === 'new') {
    return (
      <>
        <RouteHeading>Reviews</RouteHeading>
        <ReviewQueue workspaceId={workspaceId} />
      </>
    )
  }

  return (
    <ResourceView resource={review} what="this review">
      {(record) => (
        <>
          <RouteHeading>Review by {record.reviewerId.slice(0, 8)}</RouteHeading>

          <p className="af-row">
            <StatusBadge
              tone={record.verdict === 'ACCEPT' ? 'pass' : 'neutral'}
              kind="Assessment"
            >
              {record.verdict}
            </StatusBadge>
          </p>

          {record.supersededBy !== null && (
            <Notice tone="warning" heading="A later assessment replaced this one" headingLevel={2}>
              <p>
                This record is unchanged and still says what it said. Corrections are appended, so
                the history reads as what was said and then what was said instead.
              </p>
            </Notice>
          )}

          <section className="af-stack">
            <h2>What this person said</h2>
            <dl>
              <dt>Observations</dt>
              <dd>{record.observations}</dd>
              <dt>What they could not judge</dt>
              <dd>
                {record.limitations === '' ? (
                  <span className="af-secondary">Nothing recorded</span>
                ) : (
                  record.limitations
                )}
              </dd>
              <dt>Used assistive technology</dt>
              <dd>
                {record.usedAssistiveTechnology ? 'Yes' : 'No'}
                {record.assistiveTechnologyDetail !== null && (
                  <span className="af-secondary"> · {record.assistiveTechnologyDetail}</span>
                )}
              </dd>
              <dt>Reviewer role at the time</dt>
              <dd>{record.reviewerRole}</dd>
              <dt>Submitted</dt>
              <dd>
                <time dateTime={record.submittedAt}>{record.submittedAt}</time>
              </dd>
            </dl>
          </section>

          <section className="af-stack">
            <h2>Exactly what was assessed</h2>
            <dl>
              <dt>Patch digest</dt>
              <dd>
                <code>{record.boundTo.patchDigest}</code>
              </dd>
              <dt>Verification digest</dt>
              <dd>
                <code>{record.boundTo.verificationDigest}</code>
              </dd>
              <dt>Journey version</dt>
              <dd>
                <code>{record.boundTo.journeyVersionId}</code>
              </dd>
              <dt>Environment digest</dt>
              <dd>
                <code>{record.boundTo.environmentDigest}</code>
              </dd>
            </dl>
            <p className="af-secondary">
              Bound by digest rather than by reference. A patch whose bytes changed is a different
              patch, and a review that followed a reference would carry this acceptance onto content
              nobody looked at.
            </p>
          </section>

          <section className="af-stack">
            <h2>What this does not establish</h2>
            <ul>
              {record.meansNothingAbout.map((limit) => (
                <li key={limit}>{limit}</li>
              ))}
            </ul>
          </section>
        </>
      )}
    </ResourceView>
  )
}
