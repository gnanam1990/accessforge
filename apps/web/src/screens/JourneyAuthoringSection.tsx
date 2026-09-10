/**
 * Authoring a journey version and freezing it.
 *
 * Five decisions shape this form, and each is a way the obvious version misleads someone.
 *
 * **Saving is freezing, and freezing is not running.** The button says "Freeze version", and the
 * confirmation says what freezing committed to. A "Save" that also dispatched would make every
 * edit an execution, and UI-UX section 4 requires the two to be separate actions.
 *
 * **Editing a frozen version creates a successor.** The form carries `supersedes` when it was
 * opened from an existing version. Runs already sealed against the old one keep it — the server
 * enforces that with a trigger, and this form does not pretend otherwise by offering an edit.
 *
 * **The vocabulary comes from the server.** Actions, key chords, assertion kinds and the budget
 * ceilings are read from `/journey-capabilities`. A list hard-coded here would be a second
 * definition of the policy, and an author offered a control the server refuses is being invited to
 * fail.
 *
 * **A refusal lands on the field it is about.** The server names the field for a malformed value and
 * gives a capability code for an unsupported one; both go into the error summary, and the summary
 * links to the control. A message with nowhere to go is a message somebody has to guess at.
 *
 * **Unsupported is different from invalid.** A 422 says AccessForge cannot do the thing that was
 * asked for; a 400 says the request was written wrongly. They are rendered differently because they
 * send an author to different places.
 */

import { useId, useState } from 'react'
import type { JSX } from 'react'

import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { ErrorSummary } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { freezeJourneyVersion, getJourneyCapabilities } from '../api/resources'
import type { FrozenVersion, JourneyCapabilities } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'

interface AssertionRow {
  readonly assertionId: string
  readonly kind: string
  readonly description: string
  readonly required: boolean
  readonly unknownReasons: readonly string[]
}

const STARTING_ASSERTIONS: readonly AssertionRow[] = [
  {
    assertionId: 'task-complete',
    kind: 'TASK_COMPLETION',
    description: '',
    required: true,
    unknownReasons: ['OBSERVER_UNREACHABLE', 'OBSERVATION_MISSING'],
  },
]

const toggle = (values: readonly string[], value: string): string[] =>
  values.includes(value) ? values.filter((v) => v !== value) : [...values, value]

export const JourneyAuthoringSection = ({
  workspaceId,
  projectId,
  supersedes,
  onFrozen,
}: {
  readonly workspaceId: string
  readonly projectId: string
  /** Set when this edit continues an existing version's lineage. */
  readonly supersedes?: string
  readonly onFrozen: () => void
}): JSX.Element => {
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const capabilities = useResource<JourneyCapabilities>(
    (signal) => getJourneyCapabilities(client, workspaceId, signal),
    [client, workspaceId],
  )

  const nameId = useId()
  const summaryId = useId()
  const startUrlId = useId()
  const successId = useId()
  const templateId = useId()
  const navigatorValuesId = useId()
  const observerConfigId = useId()
  const maxActionsId = useId()
  const wallTimeId = useId()
  const actionsId = useId()
  const chordsId = useId()
  const assertionsId = useId()

  const [name, setName] = useState('')
  const [summary, setSummary] = useState('')
  const [startUrl, setStartUrl] = useState('')
  const [successCondition, setSuccessCondition] = useState('')
  const [platform, setPlatform] = useState('darwin')
  const [template, setTemplate] = useState('')
  const [navigatorValues, setNavigatorValues] = useState('')
  const [observerConfig, setObserverConfig] = useState('')
  const [maxActions, setMaxActions] = useState('40')
  const [wallTime, setWallTime] = useState('300')
  const [actions, setActions] = useState<readonly string[]>(['NEXT', 'ACTIVATE', 'READ_CURRENT'])
  const [chords, setChords] = useState<readonly string[]>([])
  const [effects, setEffects] = useState<readonly string[]>(['FIXTURE_SUBMIT'])
  const [assertions, setAssertions] = useState<readonly AssertionRow[]>(STARTING_ASSERTIONS)

  const [errors, setErrors] = useState<readonly { fieldId: string; message: string }[]>([])
  const [unsupported, setUnsupported] = useState<{ code: string; detail: string } | null>(null)
  const [frozen, setFrozen] = useState<FrozenVersion | null>(null)
  const [submissionId, setSubmissionId] = useState(0)
  const [busy, setBusy] = useState(false)

  /**
   * Parse `key: value` lines.
   *
   * A textarea rather than a key/value widget, because a bespoke repeating-row control is a custom
   * keyboard pattern and this form already has one obligation too many. The format is stated in the
   * field's hint, and a malformed line is reported against the field rather than dropped.
   */
  const parsePairs = (
    text: string,
    fieldId: string,
    found: { fieldId: string; message: string }[],
  ): Record<string, string> => {
    const pairs: Record<string, string> = {}
    for (const raw of text.split('\n')) {
      const line = raw.trim()
      if (line === '') continue
      const separator = line.indexOf(':')
      if (separator <= 0) {
        found.push({
          fieldId,
          message: `"${line}" is not a name and a value. Write one pair per line, as name: value.`,
        })
        continue
      }
      pairs[line.slice(0, separator).trim()] = line.slice(separator + 1).trim()
    }
    return pairs
  }

  const submit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSubmissionId((current) => current + 1)
    setUnsupported(null)
    setFrozen(null)

    // The ceilings come from the server, which is also the only thing that can enforce them. The
    // form is not rendered until they have been read, so this branch is unreachable in practice —
    // and returning rather than defaulting keeps it that way, because a default here would be a
    // second, quieter copy of the policy.
    if (capabilities.state.kind !== 'ready') return
    const limits = capabilities.state.value

    const found: { fieldId: string; message: string }[] = []
    if (name.trim() === '') found.push({ fieldId: nameId, message: 'A journey needs a name.' })
    if (summary.trim() === '') {
      found.push({ fieldId: summaryId, message: 'Say what a person is trying to accomplish.' })
    }
    if (startUrl.trim() === '') {
      found.push({ fieldId: startUrlId, message: 'A journey needs somewhere to start.' })
    }
    if (successCondition.trim() === '') {
      found.push({ fieldId: successId, message: 'Say what would count as having succeeded.' })
    }
    if (template.trim() === '') {
      found.push({ fieldId: templateId, message: 'Name the fixture template this journey uses.' })
    }
    if (actions.length === 0) {
      found.push({
        fieldId: actionsId,
        message: 'Choose at least one action. A journey that may take none cannot do anything.',
      })
    }
    assertions.forEach((assertion, index) => {
      if (assertion.description.trim() === '') {
        found.push({
          fieldId: assertionsId,
          message: `Assertion ${index + 1} needs a description a reviewer can read.`,
        })
      }
    })

    // Checked here rather than left to `min` and `max`: the form is `noValidate`, so the browser
    // enforces neither. `Number('')` is 0 and `Number('1e')` is NaN, which `JSON.stringify` writes
    // as `null` — so an empty budget field became a request for zero actions, and a half-typed one
    // became a request for none at all, both refused by the server with a message about a value
    // the author could have corrected here.
    const budgetNumber = (
      raw: string,
      fieldId: string,
      label: string,
      ceiling: number,
    ): number | null => {
      const value = Number(raw)
      if (raw.trim() === '' || !Number.isInteger(value) || value < 1 || value > ceiling) {
        found.push({
          fieldId,
          message: `${label} must be a whole number between 1 and ${ceiling}.`,
        })
        return null
      }
      return value
    }
    const actionCeiling = budgetNumber(maxActions, maxActionsId, 'Maximum actions', limits.maxActions)
    const timeCeiling = budgetNumber(
      wallTime,
      wallTimeId,
      'The wall-clock budget',
      limits.maxWallTimeSeconds,
    )

    const navigator = parsePairs(navigatorValues, navigatorValuesId, found)
    const observer = parsePairs(observerConfig, observerConfigId, found)

    setErrors(found)
    if (found.length > 0 || actionCeiling === null || timeCeiling === null) return

    setBusy(true)
    const outcome = await freezeJourneyVersion(client, workspaceId, {
      projectId,
      name: name.trim(),
      platform,
      intent: {
        summary: summary.trim(),
        startUrl: startUrl.trim(),
        successCondition: successCondition.trim(),
      },
      assertions: assertions.map((assertion) => ({
        assertionId: assertion.assertionId,
        kind: assertion.kind,
        description: assertion.description.trim(),
        required: assertion.required,
        unknownReasons: assertion.unknownReasons,
      })),
      fixture: {
        templateId: template.trim(),
        navigatorValues: navigator,
        resetValues: {},
        observerConfig: observer,
      },
      budget: { maxActions: actionCeiling, wallTimeSeconds: timeCeiling },
      allowedActions: actions,
      allowedKeyChords: chords,
      allowedEffects: effects,
      ...(supersedes === undefined ? {} : { supersedes }),
    })
    setBusy(false)

    switch (outcome.kind) {
      case 'ok':
      case 'accepted':
        setFrozen(outcome.value)
        announce(`Version frozen. Digest ${outcome.value.journeyDigest.slice(0, 12)}.`)
        onFrozen()
        break
      case 'problem': {
        if (outcome.problem.code === 'UNSUPPORTED_CAPABILITY') {
          // Not a field error. The request is well formed and AccessForge cannot do what it asks,
          // which is a different conversation from a typing mistake.
          setUnsupported({
            code: String((outcome.problem as { capabilityCode?: unknown }).capabilityCode ?? ''),
            detail: outcome.problem.detail,
          })
          setErrors([])
          break
        }
        const field = (outcome.problem as { field?: unknown }).field
        const target =
          field === 'intent'
            ? summaryId
            : field === 'fixture'
              ? navigatorValuesId
              : field === 'budget'
                ? maxActionsId
                : field === 'assertions'
                  ? assertionsId
                  : nameId
        setErrors([{ fieldId: target, message: outcome.problem.detail }])
        break
      }
      case 'offline':
        setErrors([
          {
            fieldId: nameId,
            message: 'The request did not reach the server, so no version was frozen.',
          },
        ])
        break
      case 'cancelled':
      case 'stale':
      case 'unauthenticated':
        break
    }
  }

  return (
    <section className="af-stack">
      <h2>{supersedes === undefined ? 'Create a journey version' : 'Create a successor version'}</h2>

      {supersedes !== undefined && (
        <Notice tone="information" heading="This creates a new version" headingLevel={3}>
          <p>
            The version you started from stays exactly as it is, and every run already sealed against
            it keeps it. Freezing this form produces a successor with its own digest.
          </p>
        </Notice>
      )}

      <ResourceView resource={capabilities} what="what a journey may ask for">
        {(policy) => (
          <div className="af-panel af-stack">
            <ErrorSummary submissionId={submissionId} errors={errors} />

            {unsupported !== null && (
              <Notice
                tone="warning"
                heading="AccessForge cannot do what this journey asks"
                headingLevel={3}
                live
              >
                <p>{unsupported.detail}</p>
                <p className="af-secondary">
                  Capability code <code>{unsupported.code}</code>. This is not a mistake in what you
                  typed: the request is well formed and the capability does not exist.
                </p>
              </Notice>
            )}

            {frozen !== null && (
              <Notice tone="information" heading="Version frozen" headingLevel={3} live>
                <p>{frozen.meaning}</p>
                <dl>
                  <dt>Journey digest</dt>
                  <dd>
                    <code>{frozen.journeyDigest}</code>
                  </dd>
                  <dt>Assertion set digest</dt>
                  <dd>
                    <code>{frozen.assertionSetDigest}</code>
                  </dd>
                  <dt>Fixture digest</dt>
                  <dd>
                    <code>{frozen.fixtureDigest}</code>
                  </dd>
                  <dt>Navigator policy digest</dt>
                  <dd>
                    <code>{frozen.navigatorPolicyDigest}</code>
                  </dd>
                </dl>
              </Notice>
            )}

            <form onSubmit={(event) => void submit(event)} noValidate>
              <FormField id={nameId} label="Journey name" required>
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    value={name}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setName(event.target.value)}
                  />
                )}
              </FormField>

              <FormField
                id={summaryId}
                label="What is the person trying to do?"
                hint="In their words, as a goal. Not steps, and not a selector: the navigator is never given the page's structure."
                required
              >
                {({ id, describedBy, invalid }) => (
                  <textarea
                    id={id}
                    rows={2}
                    value={summary}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setSummary(event.target.value)}
                  />
                )}
              </FormField>

              <FormField
                id={startUrlId}
                label="Start address"
                hint="Must be inside an origin this project has authorized."
                required
              >
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    value={startUrl}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setStartUrl(event.target.value)}
                  />
                )}
              </FormField>

              <FormField
                id={successId}
                label="What would count as having succeeded?"
                hint="Judged independently of the agent's own report."
                required
              >
                {({ id, describedBy, invalid }) => (
                  <textarea
                    id={id}
                    rows={2}
                    value={successCondition}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setSuccessCondition(event.target.value)}
                  />
                )}
              </FormField>

              <FormField label="Platform" required>
                {({ id, describedBy }) => (
                  <select
                    id={id}
                    value={platform}
                    aria-describedby={describedBy}
                    onChange={(event) => {
                      setPlatform(event.target.value)
                      // Chords are per-platform. Keeping a chord that the new platform does not
                      // permit would produce a refusal about a control the author cannot see.
                      setChords([])
                    }}
                  >
                    {Object.keys(policy.allowedKeyChordsByPlatform).map((value) => (
                      <option key={value} value={value}>
                        {value === 'darwin' ? 'macOS (VoiceOver)' : 'Windows (NVDA)'}
                      </option>
                    ))}
                  </select>
                )}
              </FormField>

              <fieldset id={actionsId} style={{ border: 0, padding: 0, margin: 0 }}>
                <legend>Permitted actions</legend>
                <p className="af-secondary">
                  The complete vocabulary, read from the server. A journey cannot invent a
                  capability.
                </p>
                {policy.allowedActions.map((action) => (
                  <label key={action} className="af-row" style={{ gap: 'var(--af-space-2)' }}>
                    <input
                      type="checkbox"
                      checked={actions.includes(action)}
                      onChange={() => setActions((current) => toggle(current, action))}
                    />
                    {action}
                  </label>
                ))}
              </fieldset>

              <fieldset id={chordsId} style={{ border: 0, padding: 0, margin: 0 }}>
                <legend>Permitted key chords</legend>
                <p className="af-secondary">
                  Scoped to reader and browser navigation. Anything reaching the operating system,
                  the address bar, developer tools or the clipboard is absent from this list, not
                  merely unchecked.
                </p>
                {(policy.allowedKeyChordsByPlatform[platform] ?? []).map((chord) => (
                  <label key={chord} className="af-row" style={{ gap: 'var(--af-space-2)' }}>
                    <input
                      type="checkbox"
                      checked={chords.includes(chord)}
                      onChange={() => setChords((current) => toggle(current, chord))}
                    />
                    {chord}
                  </label>
                ))}
              </fieldset>

              <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                <legend>Permitted effects</legend>
                <p className="af-secondary">{policy.effectsMeaning}</p>
                {policy.allowedEffects.map((effect) => (
                  <label key={effect} className="af-row" style={{ gap: 'var(--af-space-2)' }}>
                    <input
                      type="checkbox"
                      checked={effects.includes(effect)}
                      onChange={() => setEffects((current) => toggle(current, effect))}
                    />
                    {effect}
                  </label>
                ))}
              </fieldset>

              <FormField id={templateId} label="Fixture template" required>
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    value={template}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setTemplate(event.target.value)}
                  />
                )}
              </FormField>

              <FormField
                id={navigatorValuesId}
                label="Values the navigator may type"
                hint="One per line, as name: value. Synthetic only — a credential here would travel into every export that cites this journey."
              >
                {({ id, describedBy, invalid }) => (
                  <textarea
                    id={id}
                    rows={3}
                    value={navigatorValues}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setNavigatorValues(event.target.value)}
                  />
                )}
              </FormField>

              <FormField
                id={observerConfigId}
                label="What the independent observer expects"
                hint="One per line, as name: value. This is the answer key; it is never given to the navigator, and a name used here cannot also be a navigator value."
              >
                {({ id, describedBy, invalid }) => (
                  <textarea
                    id={id}
                    rows={3}
                    value={observerConfig}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setObserverConfig(event.target.value)}
                  />
                )}
              </FormField>

              <FormField
                id={maxActionsId}
                label="Maximum actions"
                hint={`Between 1 and ${policy.maxActions}. There is no unlimited: an unbounded action count is an open-ended licence to drive a desktop.`}
                required
              >
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    type="number"
                    min={1}
                    max={policy.maxActions}
                    value={maxActions}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setMaxActions(event.target.value)}
                  />
                )}
              </FormField>

              <FormField
                id={wallTimeId}
                label="Wall-clock budget in seconds"
                hint={`Between 1 and ${policy.maxWallTimeSeconds}.`}
                required
              >
                {({ id, describedBy, invalid }) => (
                  <input
                    id={id}
                    type="number"
                    min={1}
                    max={policy.maxWallTimeSeconds}
                    value={wallTime}
                    aria-describedby={describedBy}
                    aria-invalid={invalid || undefined}
                    onChange={(event) => setWallTime(event.target.value)}
                  />
                )}
              </FormField>

              <fieldset id={assertionsId} style={{ border: 0, padding: 0, margin: 0 }}>
                <legend>Required assertions</legend>
                <p className="af-secondary">
                  A journey must include a required completion assertion decided by the independent
                  observer. A phrase announced on screen is not evidence that anything was recorded.
                </p>
                {assertions.map((assertion, index) => (
                  <div key={assertion.assertionId} className="af-stack">
                    <FormField
                      label={`Assertion ${index + 1} description`}
                      hint={`Kind: ${assertion.kind}. This sentence is the truth condition a reviewer reads.`}
                      required
                    >
                      {({ id, describedBy, invalid }) => (
                        <input
                          id={id}
                          value={assertion.description}
                          aria-describedby={describedBy}
                          aria-invalid={invalid || undefined}
                          onChange={(event) =>
                            setAssertions((current) =>
                              current.map((row, position) =>
                                position === index
                                  ? { ...row, description: event.target.value }
                                  : row,
                              ),
                            )
                          }
                        />
                      )}
                    </FormField>
                  </div>
                ))}
                <Button
                  onClick={() =>
                    setAssertions((current) => [
                      ...current,
                      {
                        assertionId: `announcement-${current.length}`,
                        kind: 'REQUIRED_ANNOUNCEMENT',
                        description: '',
                        required: true,
                        unknownReasons: ['READER_UNAVAILABLE', 'AMBIGUOUS_LANGUAGE'],
                      },
                    ])
                  }
                >
                  Add a reader announcement assertion
                </Button>
              </fieldset>

              <Button type="submit" variant="primary" busy={busy}>
                Freeze version
              </Button>
              <p className="af-secondary">
                Freezing seals this journey and authorizes nothing to execute. Requesting a run is a
                separate action with its own authorization.
              </p>
            </form>
          </div>
        )}
      </ResourceView>
    </section>
  )
}
