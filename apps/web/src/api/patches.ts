import type { ApiClient, ApiOutcome } from './client'

export interface PatchChange {
  readonly path: string; readonly operation: 'MODIFY' | 'DELETE'; readonly content: string | null
  readonly mode: string | null; readonly binary: boolean
}
export interface PatchApproval {
  readonly approvalId: string; readonly scope: string; readonly actorId: string
  readonly targetId: string; readonly targetDigest: string; readonly expectedRevision: number
  readonly expiresAt: string; readonly revokedAt: string | null
}
export interface Patch {
  readonly patchId: string; readonly findingId: string; readonly status: string
  readonly baseManifestDigest: string; readonly baseSourceDigest: string; readonly patchDigest: string
  readonly changedPaths: readonly string[]; readonly changes: readonly PatchChange[]
  readonly separatelyReviewedPaths: readonly string[]; readonly approvalId: string | null
  readonly approval?: PatchApproval | null; readonly proposedBy: string; readonly rationale: string
  readonly revision: number; readonly createdAt: string; readonly meaning: string
}
export interface Verification {
  readonly verificationId: string; readonly patchId: string; readonly baselineRunId: string
  readonly candidateRunId: string | null; readonly state: string; readonly conclusion: string | null
  readonly reasons: readonly string[]; readonly revision: number; readonly createdAt: string; readonly meaning: string
}
const object = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0
const hash = (v: unknown): boolean => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v)
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(text)
const positive = (v: unknown): boolean => typeof v === 'number' && Number.isSafeInteger(v) && v > 0
const timestamp = (v: unknown): boolean => text(v) && Number.isFinite(Date.parse(v))

export const parsePatch = (v: unknown, patchId?: string, findingId?: string): Patch | null => {
  if (!object(v) || !text(v.patchId) || (patchId !== undefined && v.patchId !== patchId) ||
      !text(v.findingId) || (findingId !== undefined && v.findingId !== findingId) || !text(v.status) ||
      !hash(v.baseManifestDigest) || !hash(v.baseSourceDigest) || !hash(v.patchDigest) ||
      !strings(v.changedPaths) || !strings(v.separatelyReviewedPaths) || !Array.isArray(v.changes) ||
      v.changes.length === 0 || !(v.approvalId === null || text(v.approvalId)) ||
      !text(v.proposedBy) || !text(v.rationale) || !positive(v.revision) || !timestamp(v.createdAt) || !text(v.meaning)) return null
  const paths = new Set<string>()
  for (const c of v.changes) {
    if (!object(c) || !text(c.path) || paths.has(c.path) || typeof c.binary !== 'boolean' ||
        !(c.mode === null || typeof c.mode === 'string') ||
        !((c.operation === 'DELETE' && c.content === null) || (c.operation === 'MODIFY' && typeof c.content === 'string'))) return null
    paths.add(c.path)
  }
  if (v.changedPaths.length !== paths.size || !v.changedPaths.every((p) => paths.has(p)) ||
      new Set(v.changedPaths).size !== paths.size || !v.separatelyReviewedPaths.every((p) => paths.has(p))) return null
  if (v.approval !== undefined && v.approval !== null) {
    const a = v.approval
    if (!object(a) || a.approvalId !== v.approvalId || !text(a.scope) || !text(a.actorId) ||
        a.targetId !== v.patchId || a.targetDigest !== v.patchDigest || !positive(a.expectedRevision) ||
        !timestamp(a.expiresAt) || !(a.revokedAt === null || timestamp(a.revokedAt))) return null
  }
  return v as unknown as Patch
}
const checked = <T,>(r: ApiOutcome<unknown>, parse: (v: unknown) => T | null): ApiOutcome<T> => {
  if (r.kind !== 'ok' && r.kind !== 'accepted') return r
  const value = parse(r.value)
  return value === null ? { kind: 'problem', problem: { code: 'UNRECOGNISED', status: 502, requestId: null,
    title: 'Repair response unavailable', detail: 'The response is incomplete or belongs to another repair. Read the original record again; no decision is confirmed.' } } : { ...r, value }
}
const base = (ws: string): string => `/v1/workspaces/${encodeURIComponent(ws)}`
export const getPatch = async (client: ApiClient, ws: string, id: string, signal: AbortSignal): Promise<ApiOutcome<Patch>> =>
  checked(await client.request(`${base(ws)}/patches/${encodeURIComponent(id)}`, { signal }), (v) => parsePatch(v, id))
export const listPatches = async (client: ApiClient, ws: string, finding: string, signal: AbortSignal): Promise<ApiOutcome<readonly Patch[]>> =>
  checked(await client.request(`${base(ws)}/findings/${encodeURIComponent(finding)}/patches`, { signal }), (v) => {
    if (!object(v) || !Array.isArray(v.items)) return null
    const items = v.items.map((p) => parsePatch(p, undefined, finding))
    return items.every((p): p is Patch => p !== null) && new Set(items.map((p) => p.patchId)).size === items.length ? items : null
  })
export const listVerifications = async (client: ApiClient, ws: string, patch: string, signal: AbortSignal): Promise<ApiOutcome<readonly Verification[]>> =>
  checked(await client.request(`${base(ws)}/patches/${encodeURIComponent(patch)}/verifications`, { signal }), (v) => {
    if (!object(v) || !Array.isArray(v.items) || !v.items.every((r: unknown) => object(r) && text(r.verificationId) &&
        r.patchId === patch && text(r.baselineRunId) && (r.candidateRunId === null || text(r.candidateRunId)) &&
        text(r.state) && (r.conclusion === null || text(r.conclusion)) && strings(r.reasons) && positive(r.revision) && timestamp(r.createdAt) && text(r.meaning))) return null
    const items = v.items as unknown as Verification[]
    return new Set(items.map((r) => r.verificationId)).size === items.length ? items : null
  })
export const decidePatch = async (client: ApiClient, ws: string, patch: Patch,
  decision: { readonly kind: 'approval'; readonly seconds: number } | { readonly kind: 'rejection'; readonly reason: string },
  signal: AbortSignal): Promise<ApiOutcome<Patch>> => checked(await client.request(
    `${base(ws)}/patches/${encodeURIComponent(patch.patchId)}/${decision.kind}`,
    { method: 'POST', ifMatch: patch.revision, signal,
      body: decision.kind === 'approval' ? { expiresInSeconds: decision.seconds } : { reason: decision.reason } }), (v) => {
      const p = parsePatch(v, patch.patchId, patch.findingId)
      return p !== null && p.patchDigest === patch.patchDigest && p.baseSourceDigest === patch.baseSourceDigest &&
        p.baseManifestDigest === patch.baseManifestDigest && p.revision > patch.revision &&
        p.status === (decision.kind === 'approval' ? 'APPROVED' : 'REJECTED') ? p : null
    })
