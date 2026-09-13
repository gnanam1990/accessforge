import { useEffect, useId, useRef, useState, type JSX } from 'react'
import type { DiagnosisProfile, DiagnosisScope } from '../api/diagnosisRequests'
import type { RunEvaluation } from '../api/evaluation'
import type { Run } from '../api/resources'
import { Button } from '../components/Button'
import { ErrorSummary, type FieldError } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'

export const DiagnosisRequestForm = ({ run, evaluation, profile, busy, submit }: {
  readonly run: Run; readonly evaluation: RunEvaluation; readonly profile: DiagnosisProfile
  readonly busy: boolean; readonly submit: (scope: DiagnosisScope) => void
}): JSX.Element => {
  const prefix = useId()
  const [name, setName] = useState('')
  const [assertion, setAssertion] = useState('')
  const [previous, setPrevious] = useState('')
  const [rows, setRows] = useState([{ id: 1, path: '', first: '1', last: '20' }])
  const nextRow = useRef(2)
  const addButton = useRef<HTMLButtonElement>(null)
  const [errors, setErrors] = useState<readonly FieldError[]>([])
  const [attempt, setAttempt] = useState(0)
  const [reviewed, setReviewed] = useState<DiagnosisScope | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const reviewHeading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { setReviewed(null); setAcknowledged(false) },
    [run.manifestDigest, run.outcome, run.status, evaluation.snapshotDigest, profile.modelProfileDigest])
  useEffect(() => { if (reviewed !== null) reviewHeading.current?.focus() }, [reviewed])
  const field = (suffix: string) => `${prefix}-${suffix}`
  const error = (id: string): { error?: string } => {
    const message = errors.find((item) => item.fieldId === id)?.message
    return message === undefined ? {} : { error: message }
  }
  const eligible = run.status === 'COMPLETED' && ['FAIL', 'INCONCLUSIVE'].includes(run.outcome ?? '') &&
    evaluation.snapshot.runId === run.runId && evaluation.snapshot.manifestDigest === run.manifestDigest &&
    evaluation.snapshot.outcome === run.outcome
  const review = () => {
    const problems: FieldError[] = []
    const add = (suffix: string, message: string) => problems.push({ fieldId: field(suffix), message })
    if (!name.trim() || name.length > 300) add('name', 'Name the component in 1–300 characters.')
    if (!evaluation.snapshot.assertions.some((item) => item.assertionId === assertion)) add('assertion', 'Select an original assertion.')
    if (previous !== '' && !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(previous)) add('previous', 'Use the exact lowercase UUID of the previous diagnosis, or leave it empty.')
    for (const row of rows) {
      if (!row.path.trim() || row.path.length > 500 || row.path.startsWith('/') || /[\\\u0000]/.test(row.path) ||
          row.path.split('/').some((part) => part === '' || part === '.' || part === '..')) add(`path-${row.id}`, 'Use an exact relative file path without traversal or empty segments.')
      const first = Number(row.first), last = Number(row.last)
      if (!/^[1-9]\d*$/.test(row.first) || !Number.isSafeInteger(first)) add(`first-${row.id}`, 'First line must be a positive whole number.')
      if (!/^[1-9]\d*$/.test(row.last) || !Number.isSafeInteger(last) || last < first || last >= first + 200) add(`last-${row.id}`, 'Choose a final line covering 1–200 lines from the first line.')
    }
    setAttempt((value) => value + 1); setErrors(problems)
    if (problems.length > 0 || !eligible) return
    const scope: DiagnosisScope = { manifestDigest: run.manifestDigest, evaluationDigest: evaluation.snapshotDigest,
      modelProfileDigest: profile.modelProfileDigest, assertionId: assertion, componentName: name.trim(),
      componentPath: rows[0]!.path, supersedes: previous || null, billableCallAcknowledged: true,
      excerpts: rows.map((row) => ({ path: row.path, lineStart: Number(row.first), lineEnd: Number(row.last) })) }
    if (new TextEncoder().encode(JSON.stringify(scope)).length > 28000) {
      setErrors([{ fieldId: field('path-1'), message: 'Combined scope is too large; reduce the excerpt paths.' }]); return
    }
    setReviewed(scope)
    setAcknowledged(false)
  }
  const input = (suffix: string, label: string, value: string, change: (value: string) => void, hint?: string) =>
    <FormField id={field(suffix)} label={label} required={suffix !== 'previous'} {...error(field(suffix))} {...(hint ? { hint } : {})}>
      {({ id, describedBy, invalid }) => <input id={id} value={value} disabled={busy}
        aria-describedby={describedBy} aria-invalid={invalid || undefined} onChange={(event) => change(event.target.value)} />}
    </FormField>
  if (!eligible) return <p>The current run and original evaluation do not support a diagnosis request. Reload the run to reconcile them.</p>
  if (reviewed !== null) return <div className="af-stack">
    <h3 tabIndex={-1} ref={reviewHeading}>Review diagnosis disclosure and scope</h3>
    <p>{profile.meaning}</p>
    <dl>{Object.entries(profile.profile).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value)}</dd></div>)}</dl>
    <dl>
      <dt>Assertion</dt><dd>{reviewed.assertionId}</dd>
      <dt>Component</dt><dd>{reviewed.componentName}</dd>
      <dt>Primary file</dt><dd><code>{JSON.stringify(reviewed.componentPath)}</code></dd>
      <dt>Previous diagnosis</dt><dd>{reviewed.supersedes ?? 'None — new occurrence'}</dd>
    </dl>
    <ul>{reviewed.excerpts.map((row, index) => <li key={index}><code>{JSON.stringify(row.path)}</code>: lines {row.lineStart}–{row.lineEnd}</li>)}</ul>
    <details><summary>Exact reviewed identities</summary><dl>
      <dt>Run</dt><dd><code>{run.runId}</code></dd>
      <dt>Manifest</dt><dd><code>{reviewed.manifestDigest}</code></dd>
      <dt>Original evaluation</dt><dd><code>{reviewed.evaluationDigest}</code></dd>
      <dt>Model profile</dt><dd><code>{reviewed.modelProfileDigest}</code></dd>
    </dl></details>
    <p>This records a one-hour request. An operator must dispatch it separately. It does not start a reader, apply a repair or change the machine outcome.</p>
    <label><input type="checkbox" checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} />{' '}
      I approve sending these bounded excerpts and retained evidence to this provider, including potential provider charges.</label>
    <div className="af-row"><Button busy={busy} disabled={!acknowledged} onClick={() => { if (acknowledged && eligible) submit(reviewed) }}>Record diagnosis request</Button>
      <Button disabled={busy} onClick={() => { setReviewed(null); setAcknowledged(false) }}>Edit diagnosis scope</Button></div>
  </div>
  return <form noValidate className="af-stack" onSubmit={(event) => { event.preventDefault(); review() }}>
    <h3>Scope a diagnosis request</h3>
    <p>Only the selected assertion is diagnosed. UNKNOWN evidence stays uncertain; a hypothesis cannot promote the run outcome.</p>
    <ErrorSummary errors={errors} submissionId={attempt} headingLevel={3} />
    <FormField id={field('assertion')} label="Original assertion" required {...error(field('assertion'))}>
      {({ id, describedBy, invalid }) => <select id={id} value={assertion} disabled={busy} aria-describedby={describedBy}
        aria-invalid={invalid || undefined} onChange={(event) => setAssertion(event.target.value)}>
        <option value="">Select an assertion</option>
        {evaluation.snapshot.assertions.map((item) => <option key={item.assertionId} value={item.assertionId}>{item.assertionId} — {item.condition}</option>)}
      </select>}
    </FormField>
    {input('name', 'Component name', name, setName)}
    {input('previous', 'Previous diagnosis ID (follow-up only)', previous, setPrevious, 'Copy the current predecessor from the finding history. Leave empty for a new occurrence.')}
    {rows.map((row, index) => <fieldset key={row.id} disabled={busy} className="af-stack">
      <legend>{index === 0 ? 'Primary component excerpt' : `Supporting excerpt ${index + 1}`}</legend>
      {input(`path-${row.id}`, `Excerpt ${index + 1} relative file path`, row.path, (value) => setRows((old) => old.map((item) => item.id === row.id ? { ...item, path: value } : item)))}
      {input(`first-${row.id}`, `Excerpt ${index + 1} first line`, row.first, (value) => setRows((old) => old.map((item) => item.id === row.id ? { ...item, first: value } : item)))}
      {input(`last-${row.id}`, `Excerpt ${index + 1} last line`, row.last, (value) => setRows((old) => old.map((item) => item.id === row.id ? { ...item, last: value } : item)))}
      {index > 0 && <Button onClick={() => { setRows((old) => old.filter((item) => item.id !== row.id)); addButton.current?.focus() }}>Remove supporting excerpt {index + 1}</Button>}
    </fieldset>)}
    <button type="button" className="af-button af-button--secondary" ref={addButton} disabled={busy || rows.length >= 40} onClick={() => setRows((old) => [...old, { id: nextRow.current++, path: '', first: '1', last: '20' }])}>Add supporting excerpt</button>
    <p>Up to 40 excerpts; each covers at most 200 lines. The operator's separate frozen-source allowlist must also permit every file.</p>
    <Button type="submit" disabled={busy}>Review diagnosis request</Button>
  </form>
}
