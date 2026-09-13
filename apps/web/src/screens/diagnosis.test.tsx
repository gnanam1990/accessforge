/** CI-only UI contracts with an inert API; no reader, provider or actual finding is invoked. */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { App } from '../App'
import { ApiClient } from '../api/client'
import type { DiagnosisAnalysis, RetainedDiagnosis } from '../api/diagnosis'
import { createFakeServer } from '../test/fakeServer'

const analysis: DiagnosisAnalysis = {
  support: 'SOURCE_LINKED', missing_information: [],
  hypothesis: {
    observed_obstacle: '<script>untrusted model wording</script>',
    affected_task_step: 'Submit the form', uncertainty: 'A timing issue remains possible',
    compliance_assessment: 'NOT_ASSESSED', supporting_evidence_ids: ['event-1'],
    alternative_explanations: ['The observation may have ended before the announcement'],
    source_location: { path: 'src/form.ts', file_digest: 'a'.repeat(64), line_start: 4, line_end: 6 },
  },
  repair_brief: {
    allowed_files: ['src/form.ts'], intended_behavior: 'Announce the inline error',
    functional_constraints: ['Keep one submission'], protected_surfaces: ['Frozen assertions'],
    stop_recommendation: 'Stop if authentication would need changing',
  },
}
const original: RetainedDiagnosis = {
  diagnosisId: 'diagnosis-1', runId: 'run-1', requestedBy: 'requester-1',
  recordedAt: '2026-09-13T00:00:00Z', deletedAt: null, supersedes: null,
  evaluationDigest: 'b'.repeat(64), projectionDigest: 'c'.repeat(64),
  modelProfileDigest: 'd'.repeat(64), payloadDigest: 'e'.repeat(64),
  establishedBy: 'MODEL_HYPOTHESIS_NOT_MACHINE_VERDICT', analysis,
}

const setup = (item: RetainedDiagnosis | undefined, overview = false) => {
  const server = createFakeServer({ userId: 'u-1', email: 'reviewer@example.test',
    workspaces: [{ workspaceId: 'ws-1', name: 'Alder', role: 'REVIEWER' }] })
  server.data.findingRows.push({ findingId: 'finding-1', runId: 'run-1', assertionId: 'assertion-1',
    status: 'CANDIDATE', summary: 'Retained form finding', revision: 1, createdAt: original.recordedAt })
  server.data.findings = {
    findingStatus: 'CANDIDATE', summary: 'Retained form finding', history: [], humanAssessments: [],
    machineOutcome: { runId: 'run-1', runStatus: 'COMPLETED', runOutcome: 'INCONCLUSIVE',
      assertionId: 'assertion-1', establishedBy: 'DETERMINISTIC_EVALUATION' },
    ...(item === undefined ? {} : { diagnoses: { items: [item], complete: false } }),
  }
  const client = new ApiClient({ fetchImpl: server.fetch, cookieSource: () => '' })
  render(<MemoryRouter initialEntries={[overview ? '/w/ws-1' : '/w/ws-1/findings/finding-1']}>
    <App client={client} />
  </MemoryRouter>)
  return server
}

describe('retained diagnosis delivery to reviewers', () => {
  it('opens a finding from overview, keeps model data inert and shows original repair scope', async () => {
    const server = setup(original, true)
    await userEvent.setup().click(await screen.findByRole('link', { name: /Retained form finding/ }))
    const section = await screen.findByRole('region', { name: 'What the model proposed' })
    expect(within(section).getByText('<script>untrusted model wording</script>')).toBeVisible()
    expect(section.querySelector('script')).toBeNull()
    expect(within(section).getByText('A timing issue remains possible')).toBeVisible()
    expect(within(section).getByText('Announce the inline error')).toBeVisible()
    expect(within(section).getByText('Stop if authentication would need changing')).toBeVisible()
    expect(within(section).getByRole('heading', { name: 'Diagnosis history is not complete' })).toBeVisible()
    expect(screen.getByText('INCONCLUSIVE')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'What people said' })).toBeVisible()
    expect(server.calls.filter((call) => !call.startsWith('GET'))).toEqual([])
  })

  it.each(['deleted', 'unsupported', 'malformed', 'old-server'] as const)(
    'keeps %s analysis distinct from a usable supported repair brief', async (mode) => {
      const item = mode === 'old-server' ? undefined : {
        ...original, supersedes: 'earlier-diagnosis',
        ...(mode === 'deleted' ? { deletedAt: '2026-09-13T01:00:00Z' } : {}),
        analysis: mode === 'malformed' ? { support: 'SOURCE_LINKED' } : mode === 'unsupported'
          ? { ...analysis, support: 'UNSUPPORTED', repair_brief: null, missing_information: ['No false assertion'] }
          : analysis,
      }
      setup(item)
      const section = await screen.findByRole('region', { name: 'What the model proposed' })
      expect(within(section).queryByText('Announce the inline error')).not.toBeInTheDocument()
      const expected = {
        deleted: /Diagnosis text was removed/, unsupported: /No supported repair brief/,
        malformed: /Original analysis is unavailable/, 'old-server': /Diagnosis history is unavailable/,
      }
      expect(within(section).getByText(expected[mode])).toBeVisible()
      if (mode === 'deleted') {
        expect(within(section).queryByText('<script>untrusted model wording</script>')).not.toBeInTheDocument()
        expect(within(section).getByText(original.payloadDigest)).toBeInTheDocument()
      }
      expect(screen.getByText('INCONCLUSIVE')).toBeVisible()
    },
  )
})
