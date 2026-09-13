import type { ApiClient, ApiOutcome } from './client'

export type DeliveryPhase = 'OPEN' | 'CLOSED_UNUSED' | 'UNCONFIRMED' | 'DELIVERY_RECORD_MISSING' | 'RESPONSE_RETAINED'
export interface EffectDelivery {
  readonly permitId: string
  readonly actionId: string
  readonly attemptId: string
  readonly actionSequence: number
  readonly action: string
  readonly actionResult: 'SUCCEEDED' | 'FAILED' | 'AMBIGUOUS' | null
  readonly phase: DeliveryPhase
  readonly grantedAt: string
  readonly expiresAt: string
  readonly consumedAt: string | null
  readonly responseRecordedAt: string | null
  readonly requestDigest: string | null
  readonly responseDigest: string | null
  readonly responseStatus: number | null
  readonly actionResultAt: string | null
  readonly leaseId: string
  readonly leaseReleasedAt: string | null
  readonly stopAcknowledgedAt: string | null
  readonly runnerQuarantined: boolean
  readonly endpointState: string | null
  readonly endpointCleanupConfirmed: boolean | null
  readonly requiresInvestigation: boolean
}
export interface EffectRecovery {
  readonly runId: string
  readonly observedAt: string
  readonly runStatus: string
  readonly runOutcome: string
  readonly runQuarantined: boolean
  readonly items: readonly EffectDelivery[]
  readonly nextCursor: string | null
  readonly providesRetryAuthority: false
  readonly providesResetAuthority: false
  readonly meaning: 'FORM_TRANSPORT_HISTORY_NOT_EFFECT_PROOF'
}

export const RECOVERY_PAGE_SIZE = 20
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v)
const hash = (v: unknown): boolean => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v)
const time = (v: unknown): boolean => text(v) && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v) && Number.isFinite(Date.parse(v))
const nullable = (v: unknown, check: (v: unknown) => boolean): boolean => v === null || check(v)

/** Display validation, not cryptographic verification or authority to repeat an effect. */
export function parseEffectRecovery(value: unknown, runId: string, after: string | null): EffectRecovery | null {
  if (!object(value) || value.runId !== runId || !uuid(value.runId) || !time(value.observedAt) ||
      !text(value.runStatus) || !text(value.runOutcome) || typeof value.runQuarantined !== 'boolean' ||
      value.meaning !== 'FORM_TRANSPORT_HISTORY_NOT_EFFECT_PROOF' || value.providesRetryAuthority !== false ||
      value.providesResetAuthority !== false || !nullable(value.nextCursor, uuid) ||
      !Array.isArray(value.items) || value.items.length > RECOVERY_PAGE_SIZE) return null
  let previous = after
  for (const item of value.items) {
    if (!object(item) || !uuid(item.permitId) || (previous !== null && item.permitId <= previous) ||
        !uuid(item.actionId) || !uuid(item.attemptId) || !uuid(item.leaseId) ||
        !Number.isSafeInteger(item.actionSequence) || (item.actionSequence as number) < 1 || !text(item.action) ||
        !nullable(item.actionResult, (v) => text(v) && ['SUCCEEDED', 'FAILED', 'AMBIGUOUS'].includes(v)) ||
        !text(item.phase) || !['OPEN', 'CLOSED_UNUSED', 'UNCONFIRMED', 'DELIVERY_RECORD_MISSING', 'RESPONSE_RETAINED'].includes(item.phase) ||
        !time(item.grantedAt) || !time(item.expiresAt) ||
        !['consumedAt', 'responseRecordedAt', 'actionResultAt', 'leaseReleasedAt', 'stopAcknowledgedAt'].every((key) => nullable(item[key], time)) ||
        !nullable(item.requestDigest, hash) || !nullable(item.responseDigest, hash) ||
        !nullable(item.responseStatus, (v) => typeof v === 'number' && [200, 201, 404, 409, 422].includes(v)) ||
        typeof item.runnerQuarantined !== 'boolean' || typeof item.requiresInvestigation !== 'boolean' ||
        !nullable(item.endpointState, text) || !nullable(item.endpointCleanupConfirmed, (v) => typeof v === 'boolean')) return null
    const consumed = item.phase !== 'OPEN' && item.phase !== 'CLOSED_UNUSED'
    const response = item.phase === 'RESPONSE_RETAINED'
    const request = response || item.phase === 'UNCONFIRMED'
    if (consumed !== (item.consumedAt !== null) || request !== (item.requestDigest !== null) ||
        response !== (item.responseDigest !== null) || response !== (item.responseStatus !== null) ||
        response !== (item.responseRecordedAt !== null) ||
        (item.actionResult !== null) !== (item.actionResultAt !== null) ||
        item.requiresInvestigation !== (['UNCONFIRMED', 'DELIVERY_RECORD_MISSING'].includes(item.phase) || item.actionResult === 'AMBIGUOUS')) return null
    previous = item.permitId
  }
  if (value.nextCursor !== null && (value.items.length !== RECOVERY_PAGE_SIZE || value.nextCursor !== previous)) return null
  return value as unknown as EffectRecovery
}

export async function readEffectRecovery(
  client: ApiClient, workspaceId: string, runId: string, after: string | null, signal: AbortSignal,
): Promise<ApiOutcome<EffectRecovery>> {
  const query = new URLSearchParams({ limit: String(RECOVERY_PAGE_SIZE) })
  if (after !== null) query.set('after', after)
  const response = await client.request<unknown>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/effect-deliveries?${query}`, { signal },
  )
  if (response.kind !== 'ok' && response.kind !== 'accepted') return response
  const value = parseEffectRecovery(response.value, runId, after)
  if (value === null) return { kind: 'problem', problem: {
    code: 'UNRECOGNISED', title: 'Transport history cannot be displayed', status: 502, requestId: null,
    detail: 'The response is malformed, unsupported or does not match this run and page. No effect or recovery permission has been inferred.',
  } }
  return response.kind === 'accepted' ? { kind: 'accepted', value } : { ...response, value }
}
