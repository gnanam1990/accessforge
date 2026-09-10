/**
 * One frozen journey version: what it sealed, what the navigator will be given, and requesting a run.
 *
 * The run request is the consequential action on this screen, and it is built around four rules.
 *
 * **The preview shows the exact thing being authorized.** Journey digest, assertion set digest,
 * fixture digest and navigator policy digest, in full, in the dialog. SECURITY-PRIVACY section 4:
 * a consequential approval displays its exact target and effect.
 *
 * **The dialog names its consequence.** "Request a run" and "Do not run", not "OK" and "Cancel".
 *
 * **202 is displayed as accepted, never as passed.** The confirmation says the run is queued, shows
 * `NOT_EVALUATED` as the outcome, and offers a link rather than a result.
 *
 * **A second press is not a second run.** Two things make that true, and it is worth being precise
 * about which does the work. `busy` disables the button, and React flushes a discrete event's state
 * update synchronously — so the second half of a double-click arrives at a control that is already
 * disabled and produces no event at all. The idempotency key, generated when the dialog opens and
 * cleared when it closes, covers the rest: a retry the *person* makes, a slow network, a stray
 * activation after the dialog has gone. An earlier version also held a synchronous re-entry ref;
 * a mutation check showed nothing could reach it, and a guard nothing can reach is worse than no
 * guard, because it invites the reader to believe the danger is handled somewhere else.
 *
 * The navigator policy is shown in full on the page. It contains no oracle material by construction
 * — the compiler builds it that way — and displaying it is how that claim becomes checkable by a
 * person rather than asserted in a document.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { Dialog } from '../components/Dialog'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { RunOutcomeBadge, RunStatusBadge } from '../components/StatusBadge'
import { getJourneyVersion, getNavigatorPolicy, requestRun } from '../api/resources'
import type { RunRequested } from '../api/resources'
import type { RunOutcome, RunStatus } from '../components/StatusBadge'
import { useResource } from '../api/useResource'
import { workspacePath } from '../routes/routeMap'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { useJourneyId } from './useJourneyId'

const Digest = ({ label, value }: { readonly label: string; readonly value: string }): JSX.Element => (
  <>
    <dt>{label}</dt>
    <dd>
      {/* Complete, not truncated. A digest shown as its first eight characters is a digest nobody
          can compare, and comparing them is the only thing they are for. */}
      <code>{value}</code>
    </dd>
  </>
)

export const JourneyScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const journeyId = useJourneyId()
  const { client } = useSession()
  const { announce } = useAnnouncer()

  const version = useResource(
    (signal) => getJourneyVersion(client, workspaceId, journeyId, signal),
    [client, workspaceId, journeyId],
  )
  const policy = useResource(
    (signal) => getNavigatorPolicy(client, workspaceId, journeyId, signal),
    [client, workspaceId, journeyId],
  )

  const [confirming, setConfirming] = useState(false)
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null)
  const [requested, setRequested] = useState<RunRequested | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const closeConfirmation = (): void => {
    setConfirming(false)
    // The key belongs to one confirmation. Keeping it would let a later activation replay this
    // authorization, which is the opposite of what an idempotency key is for.
    setIdempotencyKey(null)
  }

  const openConfirmation = (): void => {
    // Generated once, when the dialog opens. Every press of the button inside the dialog carries
    // the same key, so a double-click is one operation rather than two runs.
    setIdempotencyKey(crypto.randomUUID())
    setRefusal(null)
    setConfirming(true)
  }

  const dispatch = async (manifestDigest: string): Promise<void> => {
    if (idempotencyKey === null) return
    setBusy(true)
    const outcome = await requestRun(
      client,
      workspaceId,
      { manifestDigest },
      idempotencyKey,
    )
    setBusy(false)
    closeConfirmation()

    switch (outcome.kind) {
      case 'accepted':
      case 'ok':
        setRequested(outcome.value)
        // "Requested", not "started". Nothing has established that a desktop did anything.
        announce('Run requested. It is queued and has established nothing yet.')
        break
      case 'problem':
        setRefusal(outcome.problem.detail)
        break
      case 'offline':
        setRefusal(
          'The request did not reach the server, so no run was requested and nothing was changed.',
        )
        break
      case 'cancelled':
      case 'stale':
      case 'unauthenticated':
        break
    }
  }

  return (
    <ResourceView resource={version} what="this journey version">
      {(journey) => (
        <>
          <RouteHeading>{journey.name}</RouteHeading>

          {journey.supersededBy !== null && (
            <Notice tone="warning" heading="A later version has replaced this one" headingLevel={2}>
              <p>
                This version is unchanged and every run sealed against it keeps it. It is not what a
                new run should use.
              </p>
            </Notice>
          )}

          {refusal !== null && (
            <Notice tone="problem" heading="The run was not requested" headingLevel={2} live>
              <p>{refusal}</p>
            </Notice>
          )}

          {requested !== null && (
            <Notice tone="information" heading="Run requested" headingLevel={2} live>
              <p className="af-row">
                <RunStatusBadge status={requested.status as RunStatus} />
                <RunOutcomeBadge outcome={requested.outcome as RunOutcome} />
              </p>
              <p>
                The server accepted the request. Nothing has run, and nothing has been established:
                the run is waiting for an eligible runner.
              </p>
              <p>
                <Link className="af-link" to={workspacePath(workspaceId, `runs/${requested.runId}`)}>
                  Open this run
                </Link>
              </p>
            </Notice>
          )}

          <section className="af-stack">
            <h2>What this version sealed</h2>
            <dl>
              <dt>Platform</dt>
              <dd>{journey.platform}</dd>
              <Digest label="Journey digest" value={journey.journeyDigest} />
              <Digest label="Assertion set digest" value={journey.assertionSetDigest} />
              <Digest label="Fixture digest" value={journey.fixtureDigest} />
              <Digest label="Navigator policy digest" value={journey.navigatorPolicyDigest} />
              <dt>Frozen</dt>
              <dd>
                <time dateTime={journey.createdAt}>{journey.createdAt}</time>
              </dd>
              <dt>Supersedes</dt>
              <dd>
                {journey.supersedes === null ? (
                  <span className="af-secondary">Nothing; this is the first version</span>
                ) : (
                  <code>{journey.supersedes}</code>
                )}
              </dd>
            </dl>
          </section>

          <section className="af-stack">
            <h2>What approving a run of this version permits</h2>
            <p className="af-secondary">
              Written by the compiler when the version was frozen, from the same values that were
              sealed.
            </p>
            <pre className="af-mono">{JSON.stringify(journey.reviewerSummary, null, 2)}</pre>
          </section>

          <section className="af-stack">
            <h2>Exactly what the navigator will be given</h2>
            <p className="af-secondary">
              Shown in full so the boundary is checkable rather than asserted. It contains no
              selectors, no page structure and none of the independent observer's expectations.
            </p>
            <ResourceView resource={policy} what="the navigator policy">
              {(value) => (
                <pre className="af-mono">{JSON.stringify(value.navigatorPolicy, null, 2)}</pre>
              )}
            </ResourceView>
          </section>

          <section className="af-stack">
            <h2>Run this version</h2>
            <p className="af-secondary">
              A run is dispatched only to a runner that has passed a preflight for the required
              reader. Requesting one queues it; it does not start it.
            </p>
            <Button variant="primary" onClick={openConfirmation}>
              Request a run…
            </Button>
          </section>

          <Dialog
            open={confirming}
            heading="Request a run of this journey version"
            onClose={closeConfirmation}
            actions={
              <>
                <Button
                  variant="primary"
                  busy={busy}
                  onClick={() => void dispatch(journey.journeyDigest)}
                >
                  Request a run
                </Button>
                <Button onClick={closeConfirmation}>Do not run</Button>
              </>
            }
          >
            <p>This authorizes one execution of exactly the version below, and nothing else.</p>
            <dl>
              <dt>Journey</dt>
              <dd>{journey.name}</dd>
              <dt>Platform and reader</dt>
              <dd>{journey.platform}</dd>
              <Digest label="Journey digest" value={journey.journeyDigest} />
              <Digest label="Assertion set digest" value={journey.assertionSetDigest} />
              <Digest label="Fixture digest" value={journey.fixtureDigest} />
              <Digest label="Navigator policy digest" value={journey.navigatorPolicyDigest} />
            </dl>
            <p className="af-secondary">
              Scope <code>RUN_EFFECTS</code>. It permits only the effects this journey froze, on the
              origins this project authorized. It does not approve a repair, merge anything, deploy
              anything or publish anything.
            </p>
          </Dialog>
        </>
      )}
    </ResourceView>
  )
}
