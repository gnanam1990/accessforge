/**
 * Tester Desk API — agency commissions, tester offers/assignments/capture,
 * review decisions, payables and client reports.
 *
 * Base path: /v1/workspaces/{workspaceId}/tester-desk
 */

import type { ApiClient, ApiOutcome } from './client'

const base = (ws: string) => `/v1/workspaces/${ws}/tester-desk`

export interface Commission {
  readonly commissionId: string
  readonly reference: string
  readonly status: string
  readonly defectKey: string | null
  readonly predecessorCommissionId: string | null
  readonly offerExpiresAt: string | null
  readonly currentBriefRevision: number | null
  readonly createdAt: string
  readonly brief?: {
    readonly releaseLabel: string
    readonly feeMinor: number
    readonly currency: string
    readonly environment: Record<string, string>
  }
}

export interface Offer {
  readonly commissionId: string
  readonly reference: string
  readonly offerExpiresAt: string | null
  readonly brief: {
    readonly releaseLabel: string
    readonly feeMinor: number
    readonly currency: string
    readonly taskSteps: readonly { stepId: string; title: string }[]
    readonly environment: Record<string, string>
    readonly consentTermsVersion: number
    readonly permittedEvidence: readonly string[]
    readonly rubric: { requiredEvidenceKinds?: readonly string[] }
  }
  readonly consentTerms?: { body: string }
}

export interface Assignment {
  readonly assignmentId: string
  readonly commissionId: string
  readonly briefRevision: number
  readonly briefHash: string
  readonly status: string
  readonly expiresAt: string | null
  readonly brief?: Offer['brief']
}

export interface Observation {
  stepId: string
  result: 'PASSED' | 'FAILED' | 'UNREACHABLE' | 'INCONCLUSIVE'
  notes: string | null
  source: 'HUMAN_MANUAL'
  observedAt: string | null
}

export interface Draft {
  readonly assignmentId: string
  readonly draftRevision: number
  readonly observations: readonly Observation[]
  readonly updatedAt: string | null
}

export interface Submission {
  readonly submissionId: string
  readonly revision: number
  readonly submissionHash: string
  readonly taskResult: string
  readonly observations: readonly Observation[]
  readonly attachmentManifest: readonly { attachmentId: string; kind: string; sha256: string }[]
  readonly sealedAt: string
  readonly decision: {
    readonly decision: 'ACCEPTED' | 'NEEDS_REVISION'
    readonly justification: string
  } | null
}

export interface Attachment {
  readonly attachmentId: string
  readonly assignmentId: string
  readonly kind: string
  readonly contentType: string
  readonly filename: string
  readonly sha256: string
  readonly byteSize: number
  readonly createdAt: string
}

export interface Payable {
  readonly payableId: string
  readonly assignmentId: string
  readonly amountMinor: number
  readonly currency: string
  readonly status: string
  readonly providerReference: string | null
  readonly createdAt: string
}

export interface Report {
  readonly reportId: string
  readonly clientId: string
  readonly assignmentId: string
  readonly releaseLabel: string
  readonly revision: number
  readonly reportHash: string
  readonly body: {
    readonly releaseLabel: string
    readonly taskResult: string
    readonly environment: Record<string, string>
    readonly observations: readonly { stepId: string; stepTitle: string | null; result: string; notes: string | null }[]
    readonly attachments: readonly { attachmentId: string; handle: string; kind: string; sha256: string }[]
  }
  readonly generatedAt: string
}

export interface Progress {
  readonly submittable: boolean
  readonly missing: readonly string[]
  readonly taskResult: string
  readonly draftRevision: number
}

// --- agency ----------------------------------------------------------------

export const listCommissions = (c: ApiClient, ws: string, signal: AbortSignal) =>
  c.request<{ items: Commission[] }>(`${base(ws)}/commissions`, { signal })

export const publishCommission = (c: ApiClient, ws: string, commissionId: string, key: string) =>
  c.request<Commission>(`${base(ws)}/commissions/${commissionId}/publish`, {
    method: 'POST', body: {}, idempotencyKey: key,
  })

export const listPayables = (c: ApiClient, ws: string, signal: AbortSignal) =>
  c.request<{ items: Payable[] }>(`${base(ws)}/finance/payables`, { signal })

export const reviewQueue = (c: ApiClient, ws: string, signal: AbortSignal) =>
  c.request<{ items: { assignmentId: string; reference: string; latestSubmission: Submission | null }[] }>(
    `${base(ws)}/review/queue`, { signal })

export const reviewAssignment = (c: ApiClient, ws: string, assignmentId: string, signal: AbortSignal) =>
  c.request<Assignment & { submissions: Submission[] }>(
    `${base(ws)}/review/assignments/${assignmentId}`, { signal })

export const decide = (
  c: ApiClient, ws: string, assignmentId: string,
  submissionDigest: string, decision: 'ACCEPTED' | 'NEEDS_REVISION', justification: string, key: string,
) =>
  c.request<unknown>(`${base(ws)}/review/assignments/${assignmentId}/decision`, {
    method: 'POST',
    body: { submissionDigest, decision, justification },
    idempotencyKey: key,
  })

export const generateReport = (c: ApiClient, ws: string, assignmentId: string, key: string) =>
  c.request<Report>(`${base(ws)}/reports/generate`, {
    method: 'POST', body: { assignmentId }, idempotencyKey: key,
  })

// --- tester ----------------------------------------------------------------

export const listOffers = (c: ApiClient, ws: string, signal: AbortSignal) =>
  c.request<{ items: Offer[] }>(`${base(ws)}/tester/offers`, { signal })

export const claimOffer = (
  c: ApiClient, ws: string, commissionId: string, consentTermsVersion: number, key: string,
) =>
  c.request<Assignment>(`${base(ws)}/tester/offers/${commissionId}/claim`, {
    method: 'POST',
    body: { consentTermsVersion, consentAccepted: true },
    idempotencyKey: key,
  })

export const listAssignments = (c: ApiClient, ws: string, signal: AbortSignal) =>
  c.request<{ items: Assignment[] }>(`${base(ws)}/tester/assignments`, { signal })

export const getAssignment = (c: ApiClient, ws: string, assignmentId: string, signal: AbortSignal) =>
  c.request<Assignment>(`${base(ws)}/tester/assignments/${assignmentId}`, { signal })

export const getDraft = (c: ApiClient, ws: string, assignmentId: string, signal: AbortSignal) =>
  c.request<Draft>(`${base(ws)}/tester/assignments/${assignmentId}/draft`, { signal })

export const saveDraft = (
  c: ApiClient, ws: string, assignmentId: string,
  observations: Observation[], baseRevision: number, key: string,
) =>
  c.request<Draft>(`${base(ws)}/tester/assignments/${assignmentId}/draft`, {
    method: 'PUT',
    body: { observations, baseRevision },
    idempotencyKey: key,
  })

export const getProgress = (c: ApiClient, ws: string, assignmentId: string, signal: AbortSignal) =>
  c.request<Progress>(`${base(ws)}/tester/assignments/${assignmentId}/progress`, { signal })

export const listSubmissions = (c: ApiClient, ws: string, assignmentId: string, signal: AbortSignal) =>
  c.request<{ items: Submission[] }>(`${base(ws)}/tester/assignments/${assignmentId}/submissions`, { signal })

export const submit = (c: ApiClient, ws: string, assignmentId: string, key: string) =>
  c.request<Submission>(`${base(ws)}/tester/assignments/${assignmentId}/submit`, {
    method: 'POST', body: {}, idempotencyKey: key,
  })

export const listAttachments = (c: ApiClient, ws: string, assignmentId: string, signal: AbortSignal) =>
  c.request<{ items: Attachment[] }>(`${base(ws)}/tester/assignments/${assignmentId}/attachments`, { signal })

export const uploadAttachment = (
  c: ApiClient, ws: string, assignmentId: string,
  kind: string, contentType: string, filename: string, contentBase64: string, key: string,
) =>
  c.request<Attachment>(`${base(ws)}/tester/assignments/${assignmentId}/attachments`, {
    method: 'POST',
    body: { kind, contentType, filename, contentBase64 },
    idempotencyKey: key,
  })

export const getPayable = (c: ApiClient, ws: string, assignmentId: string, signal: AbortSignal) =>
  c.request<{ payable: Pick<Payable, 'payableId' | 'amountMinor' | 'currency' | 'status' | 'createdAt'> | null }>(
    `${base(ws)}/tester/assignments/${assignmentId}/payable`, { signal })

// --- client ----------------------------------------------------------------

export const listReports = (c: ApiClient, ws: string, signal: AbortSignal) =>
  c.request<{ items: Report[] }>(`${base(ws)}/client/reports`, { signal })
