import type { ApiClient, ApiOutcome } from './client'

export interface ConsentIdentity {
  readonly runId: string
  readonly runnerId: string
  readonly manifestDigest: string
  readonly desktopSessionKey: string
  readonly runnerProfileDigest: string
  readonly effectsDigest: string
}
export interface ReaderConsentScope extends ConsentIdentity {
  readonly revision: number
  readonly maximumExpiresAt: string
  readonly meaning: 'REVIEW_SCOPE_ONLY_NOT_STARTUP_CONSENT'
  readonly effects: {
    readonly sdk: string
    readonly sdkVersion: string
    readonly reader: string
    readonly effects: readonly string[]
    readonly requiresDedicatedDesktop: true
    readonly doesNotAuthorize: readonly string[]
  }
}
export interface ReaderConsent extends ConsentIdentity {
  readonly consentId: string
  readonly actorId: string
  readonly expiresAt: string
  readonly revokedAt: string | null
  readonly boundSessionId: string | null
  readonly meaning: 'STORED_OPERATOR_STARTUP_CONSENT_NOT_PHYSICAL_PROOF'
}
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value)
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
const utc = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value) && Number.isFinite(Date.parse(value))
const texts = (value: unknown): value is readonly string[] => Array.isArray(value) && value.length <= 20 && value.every((item) => typeof item === 'string' && item.length <= 200)
const identity = (value: unknown, runId: string): value is Record<string, unknown> => object(value) && value.runId === runId && uuid(value.runnerId) &&
  ['manifestDigest', 'desktopSessionKey', 'runnerProfileDigest', 'effectsDigest'].every((key) => typeof value[key] === 'string' && /^[0-9a-f]{64}$/.test(value[key] as string))

export function parseReaderConsentScope(value: unknown, runId: string, runnerId: string): ReaderConsentScope | null {
  if (!identity(value, runId) || value.runnerId !== runnerId || value.meaning !== 'REVIEW_SCOPE_ONLY_NOT_STARTUP_CONSENT' ||
      !Number.isSafeInteger(value.revision) || (value.revision as number) < 0 || !utc(value.maximumExpiresAt) || !object(value.effects)) return null
  const effects = value.effects
  if (effects.sdk !== '@guidepup/guidepup' || effects.sdkVersion !== '0.34.0' || effects.reader !== 'VoiceOver' ||
      effects.requiresDedicatedDesktop !== true || !texts(effects.effects) || effects.effects.length === 0 || !texts(effects.doesNotAuthorize)) return null
  return value as unknown as ReaderConsentScope
}
export function parseReaderConsent(value: unknown, runId: string): ReaderConsent | null {
  return identity(value, runId) && uuid(value.consentId) && uuid(value.actorId) && utc(value.expiresAt) &&
    (value.revokedAt === null || utc(value.revokedAt)) && (value.boundSessionId === null || uuid(value.boundSessionId)) &&
    value.meaning === 'STORED_OPERATOR_STARTUP_CONSENT_NOT_PHYSICAL_PROOF' ? value as unknown as ReaderConsent : null
}
const base = (workspaceId: string, runId: string): string => `/v1/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/reader-startup-consent`
const validate = <T,>(outcome: ApiOutcome<unknown>, parse: (value: unknown) => T | null): ApiOutcome<T> => {
  if (outcome.kind !== 'ok' && outcome.kind !== 'accepted') return outcome
  const value = parse(outcome.value)
  return value === null ? { kind: 'problem', problem: { code: 'UNRECOGNISED', title: 'Consent response unavailable',
    detail: 'The response does not match this run or its consent contract. Nothing has been authorized by this screen.', status: 502, requestId: null } } : { ...outcome, value }
}
export const readReaderConsent = async (client: ApiClient, workspaceId: string, runId: string, signal: AbortSignal): Promise<ApiOutcome<ReaderConsent>> =>
  validate(await client.request(base(workspaceId, runId), { signal }), (value) => parseReaderConsent(value, runId))
export const reviewReaderConsent = async (client: ApiClient, workspaceId: string, runId: string, runnerId: string, signal: AbortSignal): Promise<ApiOutcome<ReaderConsentScope>> =>
  validate(await client.request(`${base(workspaceId, runId)}/scope?runnerId=${encodeURIComponent(runnerId)}`, { signal }), (value) => parseReaderConsentScope(value, runId, runnerId))
export const issueReaderConsent = async (client: ApiClient, workspaceId: string, scope: ReaderConsentScope, expiresAt: string, idempotencyKey: string, signal: AbortSignal): Promise<ApiOutcome<ReaderConsent>> =>
  validate(await client.request(base(workspaceId, scope.runId), { method: 'POST', ifMatch: scope.revision, idempotencyKey, signal,
    body: { runnerId: scope.runnerId, manifestDigest: scope.manifestDigest, desktopSessionKey: scope.desktopSessionKey,
      runnerProfileDigest: scope.runnerProfileDigest, effectsDigest: scope.effectsDigest, expiresAt, dedicatedDesktopAcknowledged: true } }), (value) => parseReaderConsent(value, scope.runId))
export const revokeReaderConsent = async (client: ApiClient, workspaceId: string, consent: ReaderConsent, signal: AbortSignal): Promise<ApiOutcome<ReaderConsent>> =>
  validate(await client.request(`${base(workspaceId, consent.runId)}/revocation`, { method: 'POST', signal,
    body: { consentId: consent.consentId } }), (value) => parseReaderConsent(value, consent.runId))
