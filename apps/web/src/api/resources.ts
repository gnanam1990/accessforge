/**
 * The `/v1` calls the screens make, and the shapes they return.
 *
 * One module rather than one per screen, so the set of things this application asks the server for
 * is readable in one place. Every function returns the client's `ApiOutcome`, so no caller can
 * accidentally treat a refusal as an empty result.
 *
 * The types here describe what the server sends. They are deliberately not shared with the server's
 * own models — there is no code generation in this repository yet, and a hand-written type that
 * *claims* to be generated would be worse than one that is honestly hand-written. `contracts/
 * openapi.json` is the contract; the tests that matter run against the live application.
 */

import type { ApiClient, ApiOutcome } from './client'

export interface Page<T> {
  readonly items: readonly T[]
  readonly nextCursor: string | null
}

/**
 * A listing read across every page the server offered.
 *
 * `complete` is false when the page bound was reached before the cursor ran out. A screen showing a
 * prefix of an inventory has to say so; silently rendering it under the heading "the runners" is a
 * claim that there are no more.
 */
export interface DrainedPage<T> extends Page<T> {
  readonly complete: boolean
}

export interface Member {
  readonly userId: string
  readonly email: string
  readonly role: string
}

export interface Project {
  readonly projectId: string
  readonly name: string
  readonly repositoryUrl: string | null
  readonly createdAt: string
}

export interface Environment {
  readonly environmentId: string
  readonly name: string
  readonly allowedOrigins: readonly string[]
  readonly fixtureResetStrategy: string
  readonly permittedEffects: readonly string[]
  readonly configDigest: string
  readonly expiresAt: string | null
  readonly revoked: boolean
  readonly supersededBy: string | null
  readonly expired: boolean
  readonly usable: boolean
}

export interface JourneyVersion {
  readonly journeyVersionId: string
  readonly name: string
  readonly platform: string
  readonly journeyDigest: string
  readonly assertionSetDigest: string
  readonly fixtureDigest: string
  readonly navigatorPolicyDigest: string
  readonly reviewerSummary: Record<string, unknown>
  readonly supersedes: string | null
  readonly supersededBy: string | null
  readonly createdAt: string
}

export interface RunnerProfile {
  readonly readerName?: string
  readonly readerVersion?: string
  readonly browser?: string
  readonly [key: string]: unknown
}

export interface Runner {
  readonly runnerId: string
  readonly name: string
  readonly status: string
  readonly platform: string
  readonly profile: RunnerProfile
  readonly leaseEpoch: number
  readonly quarantineReason: string | null
  readonly revoked: boolean
  /** Null means no preflight has ever passed. Not false: "never proved" is its own state. */
  readonly preflightPassedAt: string | null
  readonly resetCount: number
  readonly hasActiveLease: boolean
  readonly createdAt: string
}

export interface RunnerInventory extends Page<Runner> {
  readonly readinessMeaning: string
}

export interface Run {
  readonly runId: string
  readonly status: string
  readonly outcome: string
  readonly revision: number
  readonly leaseEpoch: number
  readonly manifestDigest: string
  readonly cancellationRequestedAt: string | null
  readonly stopAcknowledgedAt: string | null
  readonly ambiguityReason: string | null
  readonly quarantined: boolean
  readonly retryOf: string | null
}

export interface Attempt {
  readonly attemptId: string
  readonly leaseEpoch: number
  readonly startedAt: string
  /** Null means no recorded end — not "still running". */
  readonly endedAt: string | null
}

export interface TimelineEvent {
  readonly sequence: number
  readonly eventId: string
  readonly eventType: string
  readonly leaseEpoch: number
  readonly sourceTime: string
  readonly receivedTime: string
  readonly payloadDigest: string
  readonly previousEventHash: string
  readonly payload: unknown
  readonly producerId: string | null
  readonly producerSequence: number | null
  readonly sourceRecordDigest: string | null
}

export interface Timeline {
  readonly events: readonly TimelineEvent[]
  readonly nextAfterSequence: number
  readonly exhausted: boolean
  readonly orderingMeaning: string
}

export interface ProducerStream {
  readonly producerId: string
  readonly admittedThrough: number
  readonly closedAt: number | null
}

export interface Completeness {
  readonly reasons: readonly string[]
  readonly contiguous: boolean
  readonly producersClosed: boolean
  readonly artifactsPresent: boolean
  readonly lifecycleBounded: boolean
  readonly producers: readonly ProducerStream[]
  readonly meaning: string
}

export interface Finding {
  readonly findingId: string
  readonly runId: string
  readonly assertionId: string
  readonly status: string
  readonly summary: string
  readonly revision: number
  readonly createdAt: string
}

export interface ReviewRequest {
  readonly reviewRequestId: string
  readonly patchDigest: string
  readonly verificationDigest: string
  readonly journeyVersionId: string
  readonly environmentDigest: string
  readonly requestedBy: string
  readonly requestedOf: string | null
  readonly requestedAt: string
  /** Zero means somebody was asked and nothing about whether they looked. */
  readonly reviewCount: number
}

export interface Review {
  readonly reviewId: string
  readonly reviewRequestId: string | null
  readonly reviewerId: string
  readonly reviewerRole: string
  readonly verdict: string
  readonly observations: string
  readonly limitations: string
  readonly usedAssistiveTechnology: boolean
  readonly assistiveTechnologyDetail: string | null
  readonly boundTo: {
    readonly patchDigest: string
    readonly verificationDigest: string
    readonly journeyVersionId: string
    readonly environmentDigest: string
  }
  readonly supersedes: string | null
  readonly supersededBy: string | null
  readonly submittedAt: string
  /** Served by the server so a client cannot compose a shorter list. */
  readonly meansNothingAbout: readonly string[]
}

export interface ExportRecord {
  readonly exportId: string
  readonly runId: string
  readonly attemptId: string
  readonly bundleDigest: string
  readonly trustLevel: string
  readonly signingKeyId: string
  readonly retentionAtExport: unknown
  readonly createdAt: string
  readonly expiresAt: string
  readonly verifyWith: string
  readonly limitations: readonly string[]
}

export interface JourneyCapabilities {
  readonly allowedActions: readonly string[]
  readonly allowedKeyChordsByPlatform: Readonly<Record<string, readonly string[]>>
  readonly allowedEffects: readonly string[]
  readonly assertionKinds: readonly string[]
  readonly unknownReasons: readonly string[]
  readonly maxActions: number
  readonly maxWallTimeSeconds: number
  readonly effectsMeaning: string
}

export interface FrozenVersion {
  readonly journeyVersionId: string
  readonly journeyDigest: string
  readonly assertionSetDigest: string
  readonly fixtureDigest: string
  readonly navigatorPolicyDigest: string
  readonly reviewerSummary: Record<string, unknown>
  readonly supersedes: string | null
  readonly meaning: string
}

export interface RunRequested {
  readonly runId: string
  readonly status: string
  readonly outcome: string
}

export interface CancellationRequested {
  readonly stopAcknowledged: boolean
  readonly cancellationRequestedAt: string | null
  readonly meaning: string
}

export interface SealedManifest {
  readonly sealedManifestId: string
  readonly manifestDigest: string
  readonly journeyDigest: string
  readonly assertionSetDigest: string
  readonly fixtureDigest: string
  readonly runnerProfileDigest: string
  readonly navigatorPolicyDigest: string
  readonly environmentConfigDigest: string
  readonly environmentName: string | null
  readonly evaluatorVersion: string
  readonly modelConfigDigest: string
  readonly sourceCommitSha: string | null
  readonly sourceTreeDigest: string | null
  readonly buildArtifactDigest: string | null
  readonly runId: string | null
  readonly createdAt: string
}

const base = (workspaceId: string): string => `/v1/workspaces/${encodeURIComponent(workspaceId)}`

/** How many pages a draining read will follow before it stops and says it stopped. */
const MAX_PAGES = 20

/**
 * Follow `nextCursor` until the server runs out, and combine the items.
 *
 * Every listing on these screens is presented as an inventory — "the runners", "the versions" — and
 * a single page rendered under that heading is a claim that there are no more. The bound exists so
 * a pathological dataset cannot turn one screen into an unbounded number of requests; when it is
 * reached, `complete` is false and the caller has to say so rather than quietly showing a prefix.
 */
const drain = async <T>(
  read: (cursor: string | null) => Promise<ApiOutcome<Page<T>>>,
): Promise<ApiOutcome<DrainedPage<T>>> => {
  const items: T[] = []
  let cursor: string | null = null
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const outcome: ApiOutcome<Page<T>> = await read(cursor)
    switch (outcome.kind) {
      case 'ok':
        break
      case 'accepted':
        // A read answered with 202 is not the resource that was asked for, and there is no later
        // for a read. Reported as a problem rather than combined into a page.
        return {
          kind: 'problem',
          problem: {
            code: 'UNRECOGNISED',
            title: 'Unexpected response',
            detail: 'The server accepted this read for later processing. A read has no later.',
            status: 202,
            requestId: null,
          },
        }
      default:
        return outcome
    }
    items.push(...outcome.value.items)
    cursor = outcome.value.nextCursor
    if (cursor === null) {
      return { kind: 'ok', value: { items, nextCursor: null, complete: true }, status: 200 }
    }
  }
  return { kind: 'ok', value: { items, nextCursor: cursor, complete: false }, status: 200 }
}

export const listProjects = (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<DrainedPage<Project>>> =>
  drain((cursor) =>
    client.request<Page<Project>>(
      `${base(workspaceId)}/projects` +
        (cursor === null ? '' : `?after=${encodeURIComponent(cursor)}`),
      { signal },
    ),
  )

export const createProject = (
  client: ApiClient,
  workspaceId: string,
  body: { readonly name: string; readonly repositoryUrl?: string; readonly repositoryAuthorizedBy?: string },
): Promise<ApiOutcome<{ readonly projectId: string }>> =>
  client.request(`${base(workspaceId)}/projects`, { method: 'POST', body })

export const listMembers = (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<{ readonly items: readonly Member[] }>> =>
  client.request(`${base(workspaceId)}/members`, { signal })

export const getProject = (
  client: ApiClient,
  workspaceId: string,
  projectId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<Project>> =>
  client.request<Project>(`${base(workspaceId)}/projects/${encodeURIComponent(projectId)}`, {
    signal,
  })

export const listEnvironments = (
  client: ApiClient,
  workspaceId: string,
  projectId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<DrainedPage<Environment>>> =>
  drain((cursor) =>
    client.request<Page<Environment>>(
      `${base(workspaceId)}/projects/${encodeURIComponent(projectId)}/environments` +
        (cursor === null ? '' : `?after=${encodeURIComponent(cursor)}`),
      { signal },
    ),
  )

export const registerEnvironment = (
  client: ApiClient,
  workspaceId: string,
  projectId: string,
  body: Record<string, unknown>,
): Promise<ApiOutcome<{ readonly environmentId: string; readonly configDigest: string }>> =>
  client.request(`${base(workspaceId)}/projects/${encodeURIComponent(projectId)}/environments`, {
    method: 'POST',
    body,
  })

export const listJourneyVersions = (
  client: ApiClient,
  workspaceId: string,
  projectId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<DrainedPage<JourneyVersion>>> =>
  drain((cursor) =>
    client.request<Page<JourneyVersion>>(
      `${base(workspaceId)}/projects/${encodeURIComponent(projectId)}/journeys` +
        (cursor === null ? '' : `?after=${encodeURIComponent(cursor)}`),
      { signal },
    ),
  )

export const listSealedManifests = (
  client: ApiClient,
  workspaceId: string,
  projectId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<DrainedPage<SealedManifest>>> =>
  drain((cursor) =>
    client.request<Page<SealedManifest>>(
      `${base(workspaceId)}/projects/${encodeURIComponent(projectId)}/manifests` +
        (cursor === null ? '' : `?after=${encodeURIComponent(cursor)}`),
      { signal },
    ),
  )

export const getJourneyVersion = (
  client: ApiClient,
  workspaceId: string,
  journeyVersionId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<JourneyVersion>> =>
  client.request(`${base(workspaceId)}/journeys/${encodeURIComponent(journeyVersionId)}`, {
    signal,
  })

export const getNavigatorPolicy = (
  client: ApiClient,
  workspaceId: string,
  journeyVersionId: string,
  signal: AbortSignal,
): Promise<
  ApiOutcome<{
    readonly navigatorPolicy: Record<string, unknown>
    readonly navigatorPolicyDigest: string
  }>
> =>
  client.request(`${base(workspaceId)}/journeys/${encodeURIComponent(journeyVersionId)}/policy`, {
    signal,
  })

export const getJourneyCapabilities = (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<JourneyCapabilities>> =>
  client.request(`${base(workspaceId)}/journey-capabilities`, { signal })

export const freezeJourneyVersion = (
  client: ApiClient,
  workspaceId: string,
  body: Record<string, unknown>,
): Promise<ApiOutcome<FrozenVersion>> =>
  client.request(`${base(workspaceId)}/journeys`, { method: 'POST', body })

/**
 * The runner inventory, drained across pages.
 *
 * `readinessMeaning` comes from the first page and is carried through — it is the server's statement
 * about what a status means, and dropping it while combining pages would take the sentence off the
 * screen that most needs it.
 */
export const listRunners = async (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<RunnerInventory & DrainedPage<Runner>>> => {
  let meaning = ''
  const drained = await drain<Runner>(async (cursor) => {
    const outcome = await client.request<RunnerInventory>(
      `${base(workspaceId)}/runners` + (cursor === null ? '' : `?after=${encodeURIComponent(cursor)}`),
      { signal },
    )
    if (outcome.kind === 'ok' && meaning === '') meaning = outcome.value.readinessMeaning
    return outcome
  })
  if (drained.kind === 'ok') {
    return { kind: 'ok', value: { ...drained.value, readinessMeaning: meaning }, status: 200 }
  }
  // `drain` never answers `accepted` — it converts a 202 into a problem — but the union still
  // carries the case, and narrowing it here keeps that fact checked rather than asserted.
  return drained.kind === 'accepted'
    ? {
        kind: 'problem',
        problem: {
          code: 'UNRECOGNISED',
          title: 'Unexpected response',
          detail: 'The server accepted this read for later processing. A read has no later.',
          status: 202,
          requestId: null,
        },
      }
    : drained
}

export const listRuns = (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<DrainedPage<Run>>> =>
  drain((cursor) =>
    client.request<Page<Run>>(
      `${base(workspaceId)}/runs` + (cursor === null ? '' : `?after=${encodeURIComponent(cursor)}`),
      { signal },
    ),
  )

export const requestRun = (
  client: ApiClient,
  workspaceId: string,
  body: Record<string, unknown>,
  idempotencyKey: string,
): Promise<ApiOutcome<RunRequested>> =>
  client.request(`${base(workspaceId)}/runs`, { method: 'POST', body, idempotencyKey })

export const getRun = (
  client: ApiClient,
  workspaceId: string,
  runId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<Run>> =>
  client.request<Run>(`${base(workspaceId)}/runs/${encodeURIComponent(runId)}`, { signal })

export const listAttempts = (
  client: ApiClient,
  workspaceId: string,
  runId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<{ readonly items: readonly Attempt[] }>> =>
  client.request(`${base(workspaceId)}/runs/${encodeURIComponent(runId)}/attempts`, { signal })

/**
 * One page of the canonical chain.
 *
 * Deliberately *not* drained. Every other listing in this client follows its cursor because a page
 * rendered as an inventory is a claim that there is no more; a replay is the opposite case — the
 * chain can be very long, the reader moves through it deliberately, and UI-UX section 5 asks for a
 * paginated reading mode rather than one enormous list. `exhausted` is what says whether the end
 * has been reached, and it is true only for a short page.
 */
export const readTimeline = (
  client: ApiClient,
  workspaceId: string,
  runId: string,
  attemptId: string,
  afterSequence: number,
  signal: AbortSignal,
): Promise<ApiOutcome<Timeline>> =>
  client.request<Timeline>(
    `${base(workspaceId)}/runs/${encodeURIComponent(runId)}/timeline` +
      `?attempt_id=${encodeURIComponent(attemptId)}&after_sequence=${afterSequence}`,
    { signal },
  )

export const readCompleteness = (
  client: ApiClient,
  workspaceId: string,
  runId: string,
  attemptId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<Completeness>> =>
  client.request<Completeness>(
    `${base(workspaceId)}/runs/${encodeURIComponent(runId)}/completeness` +
      `?attempt_id=${encodeURIComponent(attemptId)}`,
    { signal },
  )

export const listFindings = (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<DrainedPage<Finding>>> =>
  drain((cursor) =>
    client.request<Page<Finding>>(
      `${base(workspaceId)}/findings` +
        (cursor === null ? '' : `?after=${encodeURIComponent(cursor)}`),
      { signal },
    ),
  )

/**
 * One finding, with machine outcome and human assessment kept separately attributable.
 *
 * Two top-level fields, never merged. The machine outcome is what the evidence established; a human
 * assessment is what a person said about it. A view that flattened them would let a reviewer's
 * ACCEPT read as the system having verified something (INV-12).
 */
export interface FindingDetail {
  readonly machineOutcome: {
    readonly runId: string
    readonly runStatus: string
    readonly runOutcome: string
    readonly assertionId: string
    readonly establishedBy: string
  }
  readonly humanAssessments: readonly {
    readonly reviewId: string
    readonly reviewerId: string
    readonly verdict: string
    readonly observations: string
    readonly limitations: string
    readonly usedAssistiveTechnology: boolean
    readonly submittedAt: string
    readonly establishedBy: string
  }[]
  readonly findingStatus: string
  readonly summary: string
  readonly history: readonly {
    readonly fromStatus: string | null
    readonly toStatus: string
    readonly actorId: string
    readonly reason: string
    readonly reviewId: string | null
    readonly occurredAt: string
  }[]
}

export const getFinding = (
  client: ApiClient,
  workspaceId: string,
  findingId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<FindingDetail>> =>
  client.request<FindingDetail>(`${base(workspaceId)}/findings/${encodeURIComponent(findingId)}`, {
    signal,
  })

/**
 * Every review that has been asked for, across pages.
 *
 * `meaning` comes from the first page and is carried through, for the same reason
 * `readinessMeaning` is: it is the server's statement about how to read the list, and dropping it
 * while combining pages would take the sentence off the screen that needs it.
 */
export const listReviewRequests = async (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<DrainedPage<ReviewRequest> & { readonly meaning: string }>> => {
  let meaning = ''
  const drained = await drain<ReviewRequest>(async (cursor) => {
    const outcome = await client.request<Page<ReviewRequest> & { meaning: string }>(
      `${base(workspaceId)}/review-requests` +
        (cursor === null ? '' : `?after=${encodeURIComponent(cursor)}`),
      { signal },
    )
    if (outcome.kind === 'ok' && meaning === '') meaning = outcome.value.meaning
    return outcome
  })
  if (drained.kind === 'ok') {
    return { kind: 'ok', value: { ...drained.value, meaning }, status: 200 }
  }
  return drained.kind === 'accepted'
    ? {
        kind: 'problem',
        problem: {
          code: 'UNRECOGNISED',
          title: 'Unexpected response',
          detail: 'The server accepted this read for later processing. A read has no later.',
          status: 202,
          requestId: null,
        },
      }
    : drained
}

export const getReview = (
  client: ApiClient,
  workspaceId: string,
  reviewId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<Review>> =>
  client.request<Review>(`${base(workspaceId)}/reviews/${encodeURIComponent(reviewId)}`, { signal })

export const submitReview = (
  client: ApiClient,
  workspaceId: string,
  body: Record<string, unknown>,
): Promise<ApiOutcome<{ readonly reviewId: string; readonly verdict: string }>> =>
  client.request(`${base(workspaceId)}/reviews`, { method: 'POST', body })

export const getExport = (
  client: ApiClient,
  workspaceId: string,
  exportId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<ExportRecord>> =>
  client.request<ExportRecord>(`${base(workspaceId)}/exports/${encodeURIComponent(exportId)}`, {
    signal,
  })

export const requestExport = (
  client: ApiClient,
  workspaceId: string,
  body: { readonly runId: string; readonly attemptId: string; readonly includeArtifactBytes: boolean },
  idempotencyKey: string,
): Promise<ApiOutcome<Record<string, unknown>>> =>
  client.request(`${base(workspaceId)}/exports`, { method: 'POST', body, idempotencyKey })

export const requestCancellation = (
  client: ApiClient,
  workspaceId: string,
  runId: string,
  revision: number,
): Promise<ApiOutcome<CancellationRequested>> =>
  client.request(`${base(workspaceId)}/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    body: {},
    ifMatch: revision,
  })
