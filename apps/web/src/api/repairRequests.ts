import type { ApiClient, ApiOutcome } from './client'

export interface RepairScope {
  readonly diagnosisId: string
  readonly projectId: string
  readonly sourceSnapshotId: string
  readonly diagnosisDigest: string
  readonly manifestDigest: string
  readonly sourceTreeDigest: string
  readonly evaluationDigest: string
  readonly repairSurfaceDigest: string
  readonly modelProfileDigest: string
  readonly supersedes: string | null
  readonly billableCallAcknowledged: boolean
  readonly separateReviewAcknowledged: boolean
}
export interface RepairOptions {
  readonly scope: RepairScope
  readonly sourcePaths: readonly string[]
  readonly separatelyReviewedPaths: readonly string[]
  readonly profile: Readonly<Record<string, string | number>>
  readonly disclosure: string
}
export interface RepairDelivery {
  readonly requestId: string
  readonly requestDigest: string
  readonly inputDigest: string
  readonly bindingDigest: string
  readonly outcome: 'PROPOSED' | 'NO_PROPOSAL'
  readonly patchId: string | null
  readonly patchDigest: string | null
  readonly recordedAt: string
}
export interface RepairDecision {
  readonly requestId: string
  readonly findingId: string
  readonly requestedBy: string
  readonly scope: RepairScope
  readonly scopeDigest: string
  readonly createdAt: string
  readonly expiresAt: string
  readonly revokedAt: string | null
  readonly invocationState: 'NOT_STARTED' | 'STARTED' | 'RECORDED' | 'UNCONFIRMED' | 'NOT_CALLED'
  readonly delivery: RepairDelivery | null
}
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0
const uuid = (v: unknown): v is string => text(v) && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(v)
const hash = (v: unknown): boolean => text(v) && /^[0-9a-f]{64}$/.test(v)
const utc = (v: unknown): boolean => text(v) && v.endsWith('Z') && Number.isFinite(Date.parse(v))
const ids = ['diagnosisId', 'projectId', 'sourceSnapshotId'] as const
const digests = ['diagnosisDigest', 'manifestDigest', 'sourceTreeDigest', 'evaluationDigest', 'repairSurfaceDigest', 'modelProfileDigest'] as const
const fields = [...ids, ...digests, 'supersedes', 'billableCallAcknowledged', 'separateReviewAcknowledged'] as const
const scope = (v: unknown): v is RepairScope => object(v) && Object.keys(v).length === fields.length &&
  ids.every((key) => uuid(v[key])) && digests.every((key) => hash(v[key])) &&
  (v.supersedes === null || uuid(v.supersedes)) && typeof v.billableCallAcknowledged === 'boolean' &&
  typeof v.separateReviewAcknowledged === 'boolean'
export const sameRepairScope = (a: RepairScope, b: RepairScope): boolean => fields.every((key) => a[key] === b[key])
const paths = (v: unknown): v is string[] => Array.isArray(v) && v.length <= 20 && v.every((p) =>
  text(p) && p.length <= 500 && !/[\\\u0000]/.test(p) && !p.split('/').some((part) => ['', '.', '..'].includes(part))) && new Set(v).size === v.length

const checked = <T,>(result: ApiOutcome<unknown>, parse: (v: unknown) => T | null): ApiOutcome<T> => {
  if (result.kind !== 'ok' && result.kind !== 'accepted') return result
  const value = parse(result.value)
  return value === null ? { kind: 'problem', problem: { code: 'UNRECOGNISED', status: 502,
    title: 'Repair response unavailable', requestId: null,
    detail: 'The response does not match the requested identity or contract. Read the original operation again; do not create a replacement as a retry.' } } : { ...result, value }
}
const decision = (v: unknown, findingId: string, userId: string): RepairDecision | null => {
  if (!object(v) || !uuid(v.requestId) || v.findingId !== findingId || v.requestedBy !== userId ||
      !scope(v.scope) || !v.scope.billableCallAcknowledged || !hash(v.scopeDigest) || !utc(v.createdAt) || !utc(v.expiresAt) ||
      !(v.revokedAt === null || utc(v.revokedAt)) ||
      v.meaning !== 'HUMAN_REQUEST_NOT_PROPOSAL_APPROVAL_OR_MODEL_COMPLETION' || v.deliveryMode !== 'EXPLICIT_OPERATOR_DISPATCH' ||
      !['NOT_STARTED', 'STARTED', 'RECORDED', 'UNCONFIRMED', 'NOT_CALLED'].includes(String(v.invocationState))) return null
  const d = v.delivery
  // Absence on an older server is not proof that delivery did not occur.
  if (d !== null && d !== undefined) {
    if (!object(d) || d.requestId !== v.requestId || !hash(d.requestDigest) || !hash(d.inputDigest) || !hash(d.bindingDigest) ||
        !utc(d.recordedAt) || d.meaning !== 'MODEL_DELIVERY_NOT_PATCH_APPROVAL_APPLICATION_OR_VERIFICATION' ||
        !['RECORDED', 'NOT_CALLED'].includes(String(v.invocationState))) return null
    if (d.outcome === 'PROPOSED') {
      if (!uuid(d.patchId) || !hash(d.patchDigest) || v.invocationState !== 'RECORDED') return null
    } else if (d.outcome !== 'NO_PROPOSAL' || d.patchId !== null || d.patchDigest !== null) return null
  }
  return { ...v, delivery: d ?? null } as unknown as RepairDecision
}
const base = (workspaceId: string): string => `/v1/workspaces/${encodeURIComponent(workspaceId)}`
const finding = (workspaceId: string, findingId: string): string => `${base(workspaceId)}/findings/${encodeURIComponent(findingId)}`
export const readRepairOptions = async (client: ApiClient, workspaceId: string, findingId: string, diagnosisId: string,
  signal: AbortSignal): Promise<ApiOutcome<RepairOptions>> => checked(await client.request(
  `${finding(workspaceId, findingId)}/repair-options?diagnosisId=${encodeURIComponent(diagnosisId)}`, { signal }), (v) => {
    if (!object(v) || !scope(v.scope) || v.scope.diagnosisId !== diagnosisId || v.scope.billableCallAcknowledged ||
        v.scope.separateReviewAcknowledged || !paths(v.sourcePaths) || v.sourcePaths.length === 0 ||
        !paths(v.separatelyReviewedPaths) || !v.separatelyReviewedPaths.every((p) => (v.sourcePaths as string[]).includes(p)) ||
        !text(v.disclosure) || v.meaning !== 'PREVIEW_NOT_CONSENT_OR_MODEL_INVOCATION' || !object(v.profile)) return null
    const p = v.profile
    if (!['sdk_version', 'model_id', 'region_name'].every((key) => text(p[key])) ||
        !['provider_max_tokens', 'invocation_output_tokens', 'invocation_total_tokens', 'max_context_characters', 'call_timeout_seconds']
          .every((key) => typeof p[key] === 'number' && Number.isFinite(p[key]) && p[key] > 0) ||
        !Object.values(p).every((item) => typeof item === 'string' || typeof item === 'number')) return null
    return v as unknown as RepairOptions
  })
export const requestRepair = async (client: ApiClient, workspaceId: string, findingId: string, userId: string,
  reviewed: RepairScope, key: string, signal: AbortSignal): Promise<ApiOutcome<RepairDecision>> => checked(await client.request(
  `${finding(workspaceId, findingId)}/repair-requests`, { method: 'POST', body: reviewed, idempotencyKey: key, signal }), (v) => {
    const parsed = decision(v, findingId, userId)
    return parsed !== null && sameRepairScope(parsed.scope, reviewed) ? parsed : null
  })
export const recoverRepair = async (client: ApiClient, workspaceId: string, findingId: string, userId: string,
  key: string, signal: AbortSignal): Promise<ApiOutcome<RepairDecision>> => checked(await client.request(
  `${finding(workspaceId, findingId)}/repair-requests/operation?operationKey=${encodeURIComponent(key)}`, { signal }),
  (v) => decision(v, findingId, userId))
export const revokeRepair = async (client: ApiClient, workspaceId: string, record: RepairDecision,
  signal: AbortSignal): Promise<ApiOutcome<RepairDecision>> => checked(await client.request(
  `${base(workspaceId)}/repair-requests/${encodeURIComponent(record.requestId)}/revocation`, { method: 'POST', signal }), (v) => {
    const parsed = decision(v, record.findingId, record.requestedBy)
    return parsed?.requestId === record.requestId && parsed.scopeDigest === record.scopeDigest &&
      sameRepairScope(parsed.scope, record.scope) && parsed.revokedAt !== null ? parsed : null
  })
