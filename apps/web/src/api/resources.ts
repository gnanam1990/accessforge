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

const base = (workspaceId: string): string => `/v1/workspaces/${encodeURIComponent(workspaceId)}`

export const listProjects = (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<Page<Project>>> =>
  client.request<Page<Project>>(`${base(workspaceId)}/projects`, { signal })

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
): Promise<ApiOutcome<{ readonly items: readonly Environment[] }>> =>
  client.request(
    `${base(workspaceId)}/projects/${encodeURIComponent(projectId)}/environments`,
    { signal },
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
): Promise<ApiOutcome<Page<JourneyVersion>>> =>
  client.request(`${base(workspaceId)}/projects/${encodeURIComponent(projectId)}/journeys`, {
    signal,
  })

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

export const listRunners = (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<RunnerInventory>> =>
  client.request<RunnerInventory>(`${base(workspaceId)}/runners`, { signal })

export const listRuns = (
  client: ApiClient,
  workspaceId: string,
  signal: AbortSignal,
): Promise<ApiOutcome<Page<Run>>> =>
  client.request<Page<Run>>(`${base(workspaceId)}/runs`, { signal })

export const requestRun = (
  client: ApiClient,
  workspaceId: string,
  body: Record<string, unknown>,
  idempotencyKey: string,
): Promise<ApiOutcome<RunRequested>> =>
  client.request(`${base(workspaceId)}/runs`, { method: 'POST', body, idempotencyKey })

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
