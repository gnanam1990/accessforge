import type { ApiClient, ApiOutcome } from './client'

export type NavigatorModelProfile = Readonly<Record<string, string | number>>
export interface NavigatorModelScope {
  readonly revision: number
  readonly manifestDigest: string
  readonly modelConfigDigest: string
  readonly modelProfile: NavigatorModelProfile
  readonly maximumCalls: number
  readonly tokensPerCall: number
  readonly maximumExpiresAt: string
  readonly billableCallAcknowledged: false
  readonly disclosure: string
  readonly meaning: 'PREVIEW_NOT_MODEL_CONSENT_OR_INVOCATION'
}
export interface NavigatorInvocation {
  readonly operationId: string
  readonly afterActionSequence: number
  readonly status: 'STARTED' | 'RECORDED' | 'UNCONFIRMED' | 'NOT_CALLED'
  readonly reservedTokens: number
  readonly createdAt: string
  readonly finishedAt: string | null
}
export interface NavigatorConsent {
  readonly consentId: string
  readonly runId: string
  readonly actorId: string
  readonly manifestDigest: string
  readonly modelConfigDigest: string
  readonly modelProfile: NavigatorModelProfile
  readonly maxCalls: number
  readonly tokensPerCall: number
  readonly expiresAt: string
  readonly revokedAt: string | null
  readonly invocations: readonly NavigatorInvocation[]
  readonly disclosure: string
  readonly meaning: 'STORED_MODEL_CONSENT_NOT_INVOCATION_OR_FINANCIAL_CAP'
}
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const integer = (value: unknown, low: number, high: number): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= low && value <= high
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value)
const utc = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value) && Number.isFinite(Date.parse(value))
const identity = { sdk_distribution: 'strands-agents', sdk_version: '1.55.1', provider: 'amazon-bedrock',
  model_id: 'global.anthropic.claude-sonnet-4-6', region_name: 'us-east-1' }
const bounds: Readonly<Record<string, readonly [number, number]>> = {
  temperature: [0, 0], provider_max_tokens: [64, 4096], invocation_turns: [1, 1],
  invocation_output_tokens: [64, 4096], invocation_total_tokens: [512, 50000],
  max_context_characters: [1000, 100000], call_timeout_seconds: [1, 120], model_attempts: [1, 3],
  retry_initial_delay_seconds: [1, 5], retry_max_delay_seconds: [1, 10],
}
// Display validation only: canonical digest and current authorization are independently enforced
// by the API. Unknown provider fields are never rendered or copied into a consent mutation.
const profile = (value: unknown): value is NavigatorModelProfile => object(value) &&
  Object.keys(value).length === Object.keys(identity).length + Object.keys(bounds).length &&
  Object.entries(identity).every(([key, expected]) => value[key] === expected) &&
  Object.entries(bounds).every(([key, [low, high]]) => integer(value[key], low, high)) &&
  Number(value.retry_max_delay_seconds) >= Number(value.retry_initial_delay_seconds)
const shared = (value: unknown): value is Record<string, unknown> => object(value) &&
  hash(value.manifestDigest) && hash(value.modelConfigDigest) && profile(value.modelProfile) &&
  integer(value.tokensPerCall, 1, 150000) && value.tokensPerCall ===
  Number(value.modelProfile.invocation_total_tokens) * Number(value.modelProfile.model_attempts) &&
  typeof value.disclosure === 'string' && value.disclosure.length > 0 && value.disclosure.length <= 2000

export function parseNavigatorScope(value: unknown): NavigatorModelScope | null {
  return shared(value) && integer(value.revision, 0, Number.MAX_SAFE_INTEGER) &&
    integer(value.maximumCalls, 1, 500) && utc(value.maximumExpiresAt) && value.billableCallAcknowledged === false &&
    value.meaning === 'PREVIEW_NOT_MODEL_CONSENT_OR_INVOCATION' ? value as unknown as NavigatorModelScope : null
}
export function parseNavigatorConsent(value: unknown, runId: string): NavigatorConsent | null {
  if (!shared(value) || value.runId !== runId || !uuid(value.consentId) || !uuid(value.actorId) ||
      !integer(value.maxCalls, 1, 500) || !utc(value.expiresAt) || !(value.revokedAt === null || utc(value.revokedAt)) ||
      value.meaning !== 'STORED_MODEL_CONSENT_NOT_INVOCATION_OR_FINANCIAL_CAP' ||
      !Array.isArray(value.invocations) || value.invocations.length > value.maxCalls) return null
  const ids = new Set<string>()
  for (const call of value.invocations) {
    if (!object(call) || !uuid(call.operationId) || ids.has(call.operationId) ||
        !integer(call.afterActionSequence, 0, 499) || !utc(call.createdAt) ||
        !['STARTED', 'RECORDED', 'UNCONFIRMED', 'NOT_CALLED'].includes(String(call.status)) ||
        call.reservedTokens !== value.tokensPerCall ||
        !(call.status === 'STARTED' ? call.finishedAt === null : utc(call.finishedAt))) return null
    ids.add(call.operationId)
  }
  return value as unknown as NavigatorConsent
}
const base = (workspaceId: string, runId: string): string => `/v1/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/navigator-model-consent`
const validate = <T,>(outcome: ApiOutcome<unknown>, parse: (value: unknown) => T | null): ApiOutcome<T> => {
  if (outcome.kind !== 'ok') return outcome.kind === 'accepted' ? invalid() : outcome
  const parsed = parse(outcome.value)
  return parsed === null ? invalid() : { ...outcome, value: parsed }
}
const invalid = (): ApiOutcome<never> => ({ kind: 'problem', problem: { code: 'UNRECOGNISED',
  title: 'Navigator consent response unavailable', detail: 'The response does not match the requested consent contract. Read the stored decision to reconcile; do not assume a mutation failed or repeat it.', status: 502, requestId: null } })
export const readNavigatorConsent = async (client: ApiClient, workspaceId: string, runId: string, signal: AbortSignal): Promise<ApiOutcome<NavigatorConsent>> =>
  validate(await client.request(base(workspaceId, runId), { signal }), value => parseNavigatorConsent(value, runId))
export const reviewNavigatorConsent = async (client: ApiClient, workspaceId: string, runId: string, signal: AbortSignal): Promise<ApiOutcome<NavigatorModelScope>> =>
  validate(await client.request(`${base(workspaceId, runId)}/scope`, { signal }), parseNavigatorScope)
export const issueNavigatorConsent = async (client: ApiClient, workspaceId: string, runId: string, scope: NavigatorModelScope, maxCalls: number, expiresAt: string, key: string, signal: AbortSignal): Promise<ApiOutcome<NavigatorConsent>> =>
  validate(await client.request(base(workspaceId, runId), { method: 'POST', signal, ifMatch: scope.revision, idempotencyKey: key,
    body: { manifestDigest: scope.manifestDigest, modelProfile: scope.modelProfile, maxCalls, expiresAt, billableCallAcknowledged: true } }), value => {
      const consent = parseNavigatorConsent(value, runId)
      return consent !== null && consent.manifestDigest === scope.manifestDigest && consent.modelConfigDigest === scope.modelConfigDigest &&
        Object.keys(scope.modelProfile).every(key => consent.modelProfile[key] === scope.modelProfile[key]) &&
        consent.maxCalls === maxCalls && Date.parse(consent.expiresAt) === Date.parse(expiresAt) ? consent : null
    })
export const revokeNavigatorConsent = async (client: ApiClient, workspaceId: string, consent: NavigatorConsent, signal: AbortSignal): Promise<ApiOutcome<NavigatorConsent>> =>
  validate(await client.request(`${base(workspaceId, consent.runId)}/revocation`, { method: 'POST', signal, body: { consentId: consent.consentId } }), value => {
    const next = parseNavigatorConsent(value, consent.runId)
    return next !== null && next.consentId === consent.consentId && next.revokedAt !== null ? next : null
  })
