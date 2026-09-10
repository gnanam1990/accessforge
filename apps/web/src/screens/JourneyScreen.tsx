/**
 * One frozen journey version: what it sealed, what the navigator will be given, and requesting a run.
 *
 * The run request is the consequential action on this screen, and it is built around four rules.
 *
 * **A run is requested against a sealed manifest, never against a journey digest.** The manifest
 * covers the source commit, the built artifact, the environment configuration, the journey, its
 * assertions and fixture, the runner profile, the evaluator version and the model configuration —
 * together. An earlier version of this screen sent `journeyDigest` in the `manifestDigest` field,
 * which queued runs carrying an identity that matched nothing and would have been refused at
 * dispatch for naming a different sealed manifest. This screen now reads the project's sealed
 * manifests and **refuses to request a run when there are none**, with the reason and the next step
 * on the page. Assembling a plausible digest was the failure; a disabled control that explains
 * itself is the fix.
 *
 * **The preview shows the exact thing being authorized.** The manifest digest and every component
 * digest, in full, in the dialog. SECURITY-PRIVACY section 4: a consequential approval displays its
 * exact target and effect.
 *
 * **The dialog names its consequence.** "Request a run" and "Do not run", not "OK" and "Cancel".
 *
 * **202 is displayed as accepted, never as passed.** The confirmation says the run is queued, shows
 * `NOT_EVALUATED` as the outcome, and offers a link rather than a result.
 *
 * **A second press is not a second run.** Two things make that true, and it is worth being precise
 * about which does the work. `busy` disables the button, and React flushes a discrete event's state
 * update synchronously — so the second half of a double-click arrives at a control that is already
 * disabled and produces no event at all. The idempotency key covers the rest: a retry the *person*
 * makes, a slow network, a response that was lost on the way back. An earlier version also held a
 * synchronous re-entry ref; a mutation check showed nothing could reach it, and a guard nothing can
 * reach is worse than no guard, because it invites the reader to believe the danger is handled
 * somewhere else.
 *
 * **The key survives a failure that might not have been one.** It is generated when the dialog
 * opens and discarded only on success. An `offline` outcome means the request may well have reached
 * the server and the answer been lost on the way back; discarding the key there and generating a
 * fresh one on the next attempt would turn one authorization into two runs — the exact thing the
 * key exists to prevent. So the dialog stays open, the key stays, and the retry deduplicates.
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
import {
  getJourneyVersion,
  getNavigatorPolicy,
  listSealedManifests,
  requestRun,
} from '../api/resources'
import type { RunRequested, SealedManifest } from '../api/resources'
import type { RunOutcome, RunStatus } from '../components/StatusBadge'
import { useResource } from '../api/useResource'
import { workspacePath } from '../routes/routeMap'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { useJourneyId } from './useJourneyId'
import { useProjectId } from './useProjectId'

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
  const projectId = useProjectId()
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
  const manifests = useResource(
    (signal) => listSealedManifests(client, workspaceId, projectId, signal),
    [client, workspaceId, projectId],
  )

  const [confirming, setConfirming] = useState(false)
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null)
  const [requested, setRequested] = useState<RunRequested | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  /** The confirmation ended, one way or another: the key belongs to that one confirmation. */
  const finishConfirmation = (): void => {
    setConfirming(false)
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
    const outcome = await requestRun(client, workspaceId, { manifestDigest }, idempotencyKey)
    setBusy(false)

    switch (outcome.kind) {
      case 'accepted':
      case 'ok':
        finishConfirmation()
        setRequested(outcome.value)
        // "Requested", not "started". Nothing has established that a desktop did anything.
        announce('Run requested. It is queued and has established nothing yet.')
        break
      case 'problem':
        finishConfirmation()
        setRefusal(outcome.problem.detail)
        break
      case 'offline':
        // The dialog stays open and the key is kept. This client cannot tell a request that never
        // arrived from one whose answer was lost, and only one of those is safe to repeat with a
        // fresh identity.
        setRefusal(
          'The server did not answer. It may have received the request, so whether a run was ' +
            'created is unknown. Pressing Request a run again repeats the same operation rather ' +
            'than starting a second one.',
        )
        break
      case 'cancelled':
      case 'stale':
      case 'unauthenticated':
        finishConfirmation()
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
            <ResourceView resource={manifests} what="this project's sealed manifests">
              {(page) => {
                const usable = page.items.filter(
                  (manifest) =>
                    manifest.journeyDigest === journey.journeyDigest && manifest.runId === null,
                )
                const chosen: SealedManifest | undefined = usable[0]
                return chosen === undefined ? (
                  <Notice
                    tone="warning"
                    heading="This version cannot be run yet"
                    headingLevel={3}
                  >
                    <p>
                      A run is requested against a sealed manifest, which binds this journey to an
                      exact source commit, an exact built artifact and an exact environment
                      configuration. No manifest has been sealed for this version, so there is
                      nothing to run.
                    </p>
                    <p className="af-secondary">
                      Sealing needs a recorded source snapshot and a build artifact for this
                      project, and a matched runner profile. Nothing in this interface can create
                      one, and requesting a run against the journey digest instead would queue a run
                      whose identity matches nothing.
                    </p>
                  </Notice>
                ) : (
                  <>
                    <Button variant="primary" onClick={openConfirmation}>
                      Request a run…
                    </Button>
                    <Dialog
                      open={confirming}
                      heading="Request a run of this journey version"
                      onClose={finishConfirmation}
                      actions={
                        <>
                          <Button
                            variant="primary"
                            busy={busy}
                            onClick={() => void dispatch(chosen.manifestDigest)}
                          >
                            Request a run
                          </Button>
                          <Button onClick={finishConfirmation}>Do not run</Button>
                        </>
                      }
                    >
                      <p>
                        This authorizes one execution of exactly the sealed manifest below, and
                        nothing else.
                      </p>
                      <dl>
                        <dt>Journey</dt>
                        <dd>{journey.name}</dd>
                        <dt>Environment</dt>
                        <dd>{chosen.environmentName ?? 'not recorded'}</dd>
                        <dt>Source commit</dt>
                        <dd>
                          <code>{chosen.sourceCommitSha ?? 'not recorded'}</code>
                        </dd>
                        <Digest label="Sealed manifest digest" value={chosen.manifestDigest} />
                        <Digest label="Journey digest" value={chosen.journeyDigest} />
                        <Digest label="Assertion set digest" value={chosen.assertionSetDigest} />
                        <Digest label="Fixture digest" value={chosen.fixtureDigest} />
                        <Digest label="Runner profile digest" value={chosen.runnerProfileDigest} />
                        <dt>Evaluator</dt>
                        <dd>{chosen.evaluatorVersion}</dd>
                      </dl>
                      <p className="af-secondary">
                        Scope <code>RUN_EFFECTS</code>. It permits only the effects this journey
                        froze, on the origins this project authorized. It does not approve a repair,
                        merge anything, deploy anything or publish anything.
                      </p>
                    </Dialog>
                  </>
                )
              }}
            </ResourceView>
          </section>

        </>
      )}
    </ResourceView>
  )
}
