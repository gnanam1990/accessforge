import type { JSX } from 'react'
import { isDiagnosisAnalysis } from '../api/diagnosis'
import type { DiagnosisHistory, RetainedDiagnosis } from '../api/diagnosis'
import { Notice } from '../components/Notice'

const TextList = ({ items }: { readonly items: readonly string[] }): JSX.Element => (
  <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
)

const Analysis = ({ item }: { readonly item: RetainedDiagnosis }): JSX.Element => {
  // Retention tombstones take precedence, even if a faulty response also contains stale text.
  if (item.deletedAt !== null) return (
    <p>Diagnosis text was removed with its source evidence at <time dateTime={item.deletedAt}>
      {item.deletedAt}</time>. Provenance below remains; the text cannot be recovered here.</p>
  )
  if (!isDiagnosisAnalysis(item.analysis)) return (
    <p>Original analysis is unavailable in a format this build can display. No explanation was substituted.</p>
  )
  const analysis = item.analysis
  const hypothesis = analysis.hypothesis
  const brief = analysis.repair_brief
  return (
    <>
      <p><strong>Evidence support:</strong> {analysis.support === 'SOURCE_LINKED'
        ? 'Source-linked hypothesis (not a verified cause)' : 'Unsupported hypothesis'}</p>
      {analysis.missing_information.length > 0 && <>
        <h4>Missing information</h4><TextList items={analysis.missing_information} />
      </>}
      {hypothesis !== null && <>
        <dl>
          <dt>Observed obstacle proposed by the model</dt><dd>{hypothesis.observed_obstacle}</dd>
          <dt>Affected task step</dt><dd>{hypothesis.affected_task_step}</dd>
          <dt>Uncertainty</dt><dd>{hypothesis.uncertainty}</dd>
          <dt>Compliance</dt><dd>Not assessed</dd>
        </dl>
        <h4>Alternative explanations</h4><TextList items={hypothesis.alternative_explanations} />
        <h4>Supporting evidence references</h4><TextList items={hypothesis.supporting_evidence_ids} />
        {hypothesis.source_location !== null && <>
          <h4>Frozen source reference</h4>
          <p><code>{hypothesis.source_location.path}</code>, lines {hypothesis.source_location.line_start}
            –{hypothesis.source_location.line_end}</p>
          <p>File digest: <code>{hypothesis.source_location.file_digest}</code></p>
        </>}
      </>}
      <h4>Repair brief</h4>
      {brief === null ? <p>No supported repair brief is retained.</p> : <>
        <p>This is proposed intent and scope, not an applied patch or permission to change files.</p>
        <dl>
          <dt>Intended behavior</dt><dd>{brief.intended_behavior}</dd>
          <dt>Allowed files</dt><dd><TextList items={brief.allowed_files} /></dd>
          <dt>Functional constraints</dt><dd><TextList items={brief.functional_constraints} /></dd>
          <dt>Protected surfaces</dt><dd><TextList items={brief.protected_surfaces} /></dd>
          <dt>Stop recommendation</dt><dd>{brief.stop_recommendation ?? 'None recorded'}</dd>
        </dl>
      </>}
    </>
  )
}

export const FindingDiagnosisSection = ({ history }: {
  readonly history: DiagnosisHistory | undefined
}): JSX.Element => (
  <section className="af-stack" aria-labelledby="diagnosis-heading">
    <h2 id="diagnosis-heading">What the model proposed</h2>
    <p>Original model hypotheses are separate from the machine outcome and human assessments.
      They do not establish a verified cause, compliance, a successful repair or permission to execute.</p>
    {history === undefined ? <p>Diagnosis history is unavailable from this server.</p> : <>
      {!history.complete && <Notice tone="warning" heading="Diagnosis history is not complete" headingLevel={3}>
        <p>Only the latest retained revisions are shown. An original or predecessor may be outside this list.</p>
      </Notice>}
      {history.items.length === 0 ? <p>No diagnosis has been retained for this finding.</p> :
        <ol className="af-stack" aria-label="Retained diagnosis revisions, newest first">
          {history.items.map((item) => (
            <li key={item.diagnosisId} className="af-panel af-stack">
              <h3>{item.supersedes === null ? 'Original diagnosis' : 'Follow-up diagnosis'}</h3>
              <p>Recorded <time dateTime={item.recordedAt}>{item.recordedAt}</time> for requester{' '}
                <code>{item.requestedBy}</code>.</p>
              <Analysis item={item} />
              <details>
                <summary>Provenance for diagnosis {item.diagnosisId}</summary>
                <dl>
                  <dt>Diagnosis</dt><dd><code>{item.diagnosisId}</code></dd>
                  <dt>Run</dt><dd><code>{item.runId}</code></dd>
                  <dt>Supersedes</dt><dd><code>{item.supersedes ?? 'None — original occurrence'}</code></dd>
                  <dt>Evaluation digest</dt><dd><code>{item.evaluationDigest}</code></dd>
                  <dt>Projection digest</dt><dd><code>{item.projectionDigest}</code></dd>
                  <dt>Model profile digest</dt><dd><code>{item.modelProfileDigest}</code></dd>
                  <dt>Payload digest</dt><dd><code>{item.payloadDigest}</code></dd>
                  <dt>Established by</dt><dd>{item.establishedBy}</dd>
                </dl>
              </details>
            </li>
          ))}
        </ol>}
    </>}
  </section>
)
