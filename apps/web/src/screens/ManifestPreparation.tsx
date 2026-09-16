import { useId, useState } from 'react'
import type { JSX } from 'react'
import type { ApiOutcome } from '../api/client'
import { createExecutionSeal, getNavigationProfile, listEnvironments, registerBuild } from '../api/resources'
import type { Environment, JourneyVersion } from '../api/resources'
import { useResource } from '../api/useResource'
import { Button } from '../components/Button'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { membershipFor, useSession } from '../session/SessionProvider'
import { OBSERVATION_MAX_LENGTH, parseBuildObservation, parseDirtyPaths } from './buildObservation'

/** Keep identity and payload together until a write has a definitive outcome. */
function useRecordedWrite<T>(request: (body: Record<string, unknown>, key: string) => Promise<ApiOutcome<T>>) {
  const [pending, setPending] = useState<{ body: Record<string, unknown>; key: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<T | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const submit = async (body: Record<string, unknown>): Promise<T | null> => {
    if (busy || result !== null) return null
    const operation = pending ?? { body, key: crypto.randomUUID() }
    setPending(operation)
    setBusy(true)
    const outcome = await request(operation.body, operation.key)
    setBusy(false)
    if (outcome.kind === 'ok' || outcome.kind === 'accepted') {
      setResult(outcome.value)
      setMessage(null)
      setPending(null)
      return outcome.value
    }
    if (outcome.kind === 'problem' && outcome.problem.status < 500) {
      setPending(null)
      setMessage(outcome.problem.detail)
    } else if (outcome.kind === 'offline' || outcome.kind === 'problem') {
      setMessage('The outcome is unknown. Inputs are locked; retry reuses the same payload and operation key. Do not reload or start another operation to retry.')
    }
    return null
  }
  return { busy, result, message, submit, retrying: pending !== null, locked: busy || pending !== null || result !== null }
}

const Field = ({ label, value, onChange, hint, required = true }: {
  readonly label: string; readonly value: string; readonly onChange: (value: string) => void
  readonly hint?: string; readonly required?: boolean
}): JSX.Element => <FormField label={label} {...(hint === undefined ? {} : { hint })} required={required}>
  {({ id, describedBy }) => <input id={id} value={value} aria-describedby={describedBy} aria-required={required}
    onChange={(event) => onChange(event.target.value)} />}
</FormField>

export const ManifestPreparation = ({ workspaceId, projectId, journey, onSealed }: {
  readonly workspaceId: string; readonly projectId: string; readonly journey: JourneyVersion; readonly onSealed: () => void
}): JSX.Element => {
  const { state } = useSession()
  const role = membershipFor(state, workspaceId)?.role
  if (role !== 'OWNER' && role !== 'MAINTAINER') return <p>A workspace owner or maintainer prepares execution manifests.</p>
  return <PreparationForm key={`${workspaceId}:${projectId}:${journey.journeyVersionId}`}
    workspaceId={workspaceId} projectId={projectId} journey={journey} onSealed={onSealed} />
}

const PreparationForm = ({ workspaceId, projectId, journey, onSealed }: {
  readonly workspaceId: string; readonly projectId: string; readonly journey: JourneyVersion; readonly onSealed: () => void
}): JSX.Element => {
  const { client } = useSession()
  const [buildId, setBuildId] = useState('')
  const environments = useResource((signal) => listEnvironments(client, workspaceId, projectId, signal),
    [client, workspaceId, projectId])
  return <section className="af-stack">
    <h2>Prepare this journey for execution</h2>
    <p>Record an actual build, choose its authorized environment, then seal bounded execution inputs.
      These steps do not build or deploy source, start a reader, call a model, or approve execution.</p>
    <details><summary>1. Register source and build identity</summary>
      <BuildRegistration workspaceId={workspaceId} projectId={projectId} onRegistered={setBuildId} />
    </details>
    <ResourceView resource={environments} what="environments for manifest preparation">{(page) => <>
      {!page.complete && <p>This is a partial environment list. Refresh before concluding an environment is absent.</p>}
      {page.items.filter((environment) => environment.usable).length === 0 ?
        <><p>No usable environment exists. Add or renew an authorized environment on the project page first.</p>
          <Button onClick={environments.reload}>Refresh environments</Button></> :
        <SealForm workspaceId={workspaceId} projectId={projectId} journey={journey}
          environments={page.items.filter((environment) => environment.usable)}
          registeredBuildId={buildId} onSealed={onSealed} />}
    </>}</ResourceView>
  </section>
}

const BuildRegistration = ({ workspaceId, projectId, onRegistered }: {
  readonly workspaceId: string; readonly projectId: string; readonly onRegistered: (id: string) => void
}): JSX.Element => {
  const { client } = useSession()
  const [values, setValues] = useState({ commitSha: '', treeDigest: '', requestedRevision: '', artifactDigest: '', dirtyPaths: '[]' })
  const [observation, setObservation] = useState('')
  const [importError, setImportError] = useState<string | null>(null)
  const [imported, setImported] = useState(false)
  const [observable, setObservable] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const operation = useRecordedWrite((body, key) => registerBuild(client, workspaceId, projectId, body, key))
  const id = useId()
  const errorId = useId()
  return <form className="af-panel af-stack" onSubmit={(event) => {
    event.preventDefault()
    if (!operation.retrying && (!/^[a-f0-9]{40}$/.test(values.commitSha) ||
      !/^[a-f0-9]{64}$/.test(values.treeDigest) || !/^[a-f0-9]{64}$/.test(values.artifactDigest) || !values.requestedRevision.trim())) {
      setError('Provide a full 40-character commit SHA, 64-character source and artifact digests, and the requested revision.')
      return
    }
    let dirtyPaths: string[]
    try { dirtyPaths = parseDirtyPaths(values.dirtyPaths) } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Invalid changed source paths.')
      return
    }
    setError(null)
    void operation.submit({ ...values, requestedRevision: values.requestedRevision.trim(), dirtyPaths,
      dirty: dirtyPaths.length > 0, identityObservable: observable }).then((result) => {
      if (result !== null) onRegistered(result.buildId)
    })
  }}>
    <p>Copy identities produced by your trusted build process. Do not invent hashes. Registering
      these observations does not independently verify them or upload the source/artifact bytes.</p>
    {(error ?? operation.message) !== null && <p id={errorId} role="alert">{error ?? operation.message}</p>}
    <fieldset disabled={operation.locked} aria-describedby={(error ?? operation.message) !== null ? errorId : undefined}>
      <legend>Observed build inputs</legend>
      <details><summary>Import offline observation JSON</summary>
        <p>Paste the output of <code>accessforge_persistence.build_observation</code>.
          Loading replaces the draft below, resets target identity confirmation, and sends nothing to the server.</p>
        <FormField label="Offline observation JSON" {...(importError === null ? {} : { error: importError })}>
          {({ id: fieldId, describedBy }) => <textarea id={fieldId} aria-describedby={describedBy}
            aria-invalid={importError !== null} rows={8} value={observation}
            onChange={(event) => { setObservation(event.target.value); setImported(false); setImportError(null) }} />}
        </FormField>
        <p>Maximum {OBSERVATION_MAX_LENGTH.toLocaleString('en-US')} characters. Do not include credentials.</p>
        <Button onClick={() => {
          if (operation.locked) return
          try {
            const parsed = parseBuildObservation(observation)
            setValues({ ...parsed, dirtyPaths: JSON.stringify(parsed.dirtyPaths, null, 2) })
            setObservable(false); setError(null); setImportError(null); setImported(true)
          } catch (cause) {
            setImported(false)
            setImportError(cause instanceof Error ? cause.message : 'Invalid observation.')
          }
        }}>Load observation into draft</Button>
        {importError !== null && <p role="alert">{importError}</p>}
        {imported && <p role="status">Observation loaded into the draft. Review the inputs below, then select Record observed build to register. Nothing has been sent.</p>}
      </details>
      {([['commitSha', 'Source commit SHA'], ['treeDigest', 'Source tree SHA-256'],
        ['requestedRevision', 'Requested source revision'], ['artifactDigest', 'Build artifact SHA-256']] as const)
        .map(([key, label]) => <Field key={key} label={label} value={values[key]}
          onChange={(value) => setValues((old) => ({ ...old, [key]: value }))} />)}
      <FormField label="Changed source paths" hint={'JSON array, for example ["src/form.tsx"]. Use [] only for a clean tree. Escaped newlines and spaces in filenames are preserved.'}>
        {({ id: fieldId, describedBy }) => <textarea id={fieldId} aria-describedby={describedBy}
          value={values.dirtyPaths} onChange={(event) => setValues((old) => ({ ...old, dirtyPaths: event.target.value }))} />}
      </FormField>
      <label htmlFor={id}><input id={id} type="checkbox" checked={observable}
        onChange={(event) => setObservable(event.target.checked)} />The target deployment exposes this exact artifact identity.</label>
      <p>Leave unchecked if this has not been observed. Canonical sealing requires an observable build.</p>
    </fieldset>
    {operation.result === null ? <Button type="submit" busy={operation.busy}>
      {operation.retrying ? 'Retry same build registration' : 'Record observed build'}
    </Button> : <Notice tone="information" heading="Build identity recorded" headingLevel={3} live>
      <p>Build ID: <code>{operation.result.buildId}</code>. Save this ID for later use.</p>
      <p>{operation.result.meaning}</p>
    </Notice>}
  </form>
}

const SealForm = ({ workspaceId, projectId, journey, environments, registeredBuildId, onSealed }: {
  readonly workspaceId: string; readonly projectId: string; readonly journey: JourneyVersion
  readonly environments: readonly Environment[]; readonly registeredBuildId: string; readonly onSealed: () => void
}): JSX.Element => {
  const { client } = useSession()
  const [existingBuildId, setExistingBuildId] = useState('')
  const [environmentId, setEnvironmentId] = useState('')
  const [effects, setEffects] = useState<string[]>([])
  const [values, setValues] = useState({ runnerProfileDigest: '', modelConfigDigest: '', evaluatorVersion: '',
    expiresAt: '', actionBudget: '', wallTimeBudgetSeconds: '' })
  const [error, setError] = useState<string | null>(null)
  const errorId = useId()
  const operation = useRecordedWrite((body, key) => createExecutionSeal(client, workspaceId, projectId, body, key))
  const environment = environments.find((entry) => entry.environmentId === environmentId)
  const buildId = existingBuildId.trim() || registeredBuildId
  return <form className="af-panel af-stack" onSubmit={(event) => {
    event.preventDefault()
    const actionBudget = Number(values.actionBudget), seconds = Number(values.wallTimeBudgetSeconds)
    const expiry = Date.parse(values.expiresAt)
    if (!operation.retrying && (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(buildId) || environment === undefined ||
      !/^[a-f0-9]{64}$/.test(values.runnerProfileDigest) || !/^[a-f0-9]{64}$/.test(values.modelConfigDigest) ||
      !values.evaluatorVersion.trim() || !Number.isSafeInteger(actionBudget) || actionBudget < 1 ||
      !Number.isSafeInteger(seconds) || seconds < 1 || !Number.isFinite(expiry) || expiry <= Date.now() ||
      !/(Z|[+-]\d{2}:\d{2})$/.test(values.expiresAt) || effects.some((effect) => !environment.permittedEffects.includes(effect)))) {
      setError('Choose a build and usable environment, provide exact profile/config digests and evaluator version, positive whole-number budgets, and a future expiry with timezone.')
      return
    }
    setError(null)
    void operation.submit({ buildId, environmentId, journeyDigest: journey.journeyDigest,
      assertionSetDigest: journey.assertionSetDigest, fixtureDigest: journey.fixtureDigest,
      navigatorPolicyDigest: journey.navigatorPolicyDigest, runnerProfileDigest: values.runnerProfileDigest,
      modelConfigDigest: values.modelConfigDigest, evaluatorVersion: values.evaluatorVersion.trim(),
      execution: { journeyVersionId: journey.journeyVersionId, expiresAt: Number.isFinite(expiry) ? new Date(expiry).toISOString() : '',
        actionBudget, wallTimeBudgetSeconds: seconds, permittedEffects: effects } }).then((result) => {
      if (result !== null) onSealed()
    })
  }}>
    <h3>2. Seal execution inputs</h3>
    <p>The frozen journey digests are reused unchanged. The server validates the build, environment,
      effects and budgets together. Sealing reserves identity only; approval is a separate step.</p>
    {(error ?? operation.message) !== null && <p id={errorId} role="alert">{error ?? operation.message}</p>}
    <fieldset disabled={operation.locked} aria-describedby={(error ?? operation.message) !== null ? errorId : undefined}>
      <legend>Execution manifest inputs</legend>
      {registeredBuildId && <p>Newly registered build: <code>{registeredBuildId}</code></p>}
      <Field label="Existing build ID" value={existingBuildId} required={false}
        hint="Use a previously recorded build ID, or leave blank to use the build registered above."
        onChange={setExistingBuildId} />
      <label>Authorized execution environment<select value={environmentId} onChange={(event) => {
        setEnvironmentId(event.target.value); setEffects([])
      }}><option value="">Choose an environment</option>{environments.map((entry) =>
        <option key={entry.environmentId} value={entry.environmentId}>{entry.name}</option>)}</select></label>
      {environment && <><p>Allowed origins: {environment.allowedOrigins.join(', ')}</p>
        <p>Select only effects required by this journey. None are selected automatically.</p>
        {environment.permittedEffects.map((effect) => <label key={effect}><input type="checkbox"
          checked={effects.includes(effect)} onChange={(event) => setEffects((old) => event.target.checked
            ? [...old, effect] : old.filter((value) => value !== effect))} />{effect}</label>)}</>}
      <details><summary>Choose the default navigation model configuration</summary>
        <NavigationProfileChoice workspaceId={workspaceId} locked={operation.locked}
          onChoose={(modelConfigDigest) => setValues((old) => ({ ...old, modelConfigDigest }))} />
      </details>
      {([['runnerProfileDigest', 'Runner profile SHA-256'], ['modelConfigDigest', 'Model configuration SHA-256'],
        ['evaluatorVersion', 'Evaluator version'], ['expiresAt', 'Manifest expires at'],
        ['actionBudget', 'Execution action limit'], ['wallTimeBudgetSeconds', 'Execution seconds limit']] as const)
        .map(([key, label]) => <Field key={key} label={label} value={values[key]}
          onChange={(value) => setValues((old) => ({ ...old, [key]: value }))} />)}
      <p>Expiry requires a timezone, for example 2026-09-18T10:00:00Z. Use profile/config digests from
        the actual runner and model configuration, not hashes copied from another project.</p>
    </fieldset>
    {operation.result === null ? <Button type="submit" busy={operation.busy}>
      {operation.retrying ? 'Retry same manifest seal' : 'Seal execution manifest'}
    </Button> : <Notice tone="information" heading="Execution manifest created" headingLevel={3} live>
      <p>{operation.result.meaning}</p><p>Manifest: <code>{operation.result.manifestDigest}</code></p>
      <p>Select this manifest under Run this version to review its exact scope and separately approve it.</p>
    </Notice>}
  </form>
}

const NavigationProfileChoice = ({ workspaceId, locked, onChoose }: {
  readonly workspaceId: string; readonly locked: boolean; readonly onChoose: (digest: string) => void
}): JSX.Element => {
  const { client } = useSession()
  const profile = useResource((signal) => getNavigationProfile(client, workspaceId, signal), [client, workspaceId])
  return <ResourceView resource={profile} what="the default navigation configuration">{(preview) => {
    const valid = preview !== null && typeof preview === 'object' &&
      preview.meaning === 'CONFIGURATION_PREVIEW_NOT_RUNTIME_EVIDENCE_OR_MODEL_CONSENT' &&
      typeof preview.modelConfigDigest === 'string' && /^[a-f0-9]{64}$/.test(preview.modelConfigDigest) &&
      preview.profile !== null && typeof preview.profile === 'object' && !Array.isArray(preview.profile) &&
      preview.profile['provider'] === 'codex-chatgpt' && typeof preview.disclosure === 'string'
    if (!valid) return <p role="alert">The server did not return a supported configuration preview. No digest was selected.</p>
    return <>
      <p>{preview.disclosure}</p>
      <pre>{JSON.stringify(preview.profile, null, 2)}</pre>
      <p>Configuration SHA-256: <code>{preview.modelConfigDigest}</code></p>
      <p>Choosing this copies its digest into the draft. It does not verify runtime readiness, approve disclosure, or call a model.</p>
      <Button disabled={locked} onClick={() => { if (!locked) onChoose(preview.modelConfigDigest) }}>Use this configuration digest</Button>
    </>
  }}</ResourceView>
}
