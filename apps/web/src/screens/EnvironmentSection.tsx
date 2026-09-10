/**
 * The environments a project's journeys may reach, and adding one.
 *
 * The screen's job is to make the scope somebody authorized visible and exact: which origins, which
 * reset strategy, which effects, and when the authorization stops being valid. Two details carry
 * most of the weight.
 *
 * **Unusable is never a bare flag.** Expired, revoked and superseded are three different situations
 * with three different next steps, and each is named. A single "unusable" would send an operator to
 * none of them.
 *
 * **Credential values never appear.** The form takes *references* — the name of a profile in a
 * secret store — and the listing shows neither the reference nor the value. A journey author needs
 * to know that a reset strategy exists and which origins are in scope, not which credential
 * performs it.
 */

import { useId, useState } from 'react'
import type { JSX } from 'react'

import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { ErrorSummary } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { EmptyState } from '../components/states'
import { listEnvironments, registerEnvironment } from '../api/resources'
import type { Environment } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'

/** Why an environment cannot be used, in the order that matters to an operator. */
const unusableBecause = (environment: Environment): string | null => {
  if (environment.revoked) return 'Revoked'
  if (environment.supersededBy !== null) return 'Superseded by a later version'
  if (environment.expired) return 'Authorization expired'
  return null
}

export const EnvironmentSection = ({
  workspaceId,
  projectId,
}: {
  readonly workspaceId: string
  readonly projectId: string
}): JSX.Element => {
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const environments = useResource(
    (signal) => listEnvironments(client, workspaceId, projectId, signal),
    [client, workspaceId, projectId],
  )

  const nameId = useId()
  const originsId = useId()
  const resetId = useId()
  const observerId = useId()
  const resetCredentialId = useId()
  const expiresId = useId()

  const [name, setName] = useState('')
  const [origins, setOrigins] = useState('')
  const [resetStrategy, setResetStrategy] = useState('TRUNCATE_AND_SEED')
  const [observerRef, setObserverRef] = useState('')
  const [resetRef, setResetRef] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [errors, setErrors] = useState<readonly { fieldId: string; message: string }[]>([])
  const [submissionId, setSubmissionId] = useState(0)
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSubmissionId((current) => current + 1)

    const found: { fieldId: string; message: string }[] = []
    if (name.trim() === '') found.push({ fieldId: nameId, message: 'An environment needs a name.' })
    const originList = origins
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line !== '')
    if (originList.length === 0) {
      found.push({
        fieldId: originsId,
        message:
          'List at least one origin. This is the scope somebody authorized, not a place the ' +
          'system discovered it could reach.',
      })
    }
    // Both are required, and checked before the equality rule. Two blank references are equal, so
    // the server refused them with the "not an independent observer" message — accurate about the
    // comparison and misleading about the cause. One blank reference passed the domain entirely and
    // was persisted empty.
    if (observerRef.trim() === '') {
      found.push({
        fieldId: observerId,
        message: 'Name the credential profile the independent observer reads with.',
      })
    }
    if (resetRef.trim() === '') {
      found.push({
        fieldId: resetCredentialId,
        message: 'Name the credential profile that resets fixture state.',
      })
    }
    if (observerRef.trim() !== '' && observerRef.trim() === resetRef.trim()) {
      found.push({
        fieldId: observerId,
        message:
          'The observer and reset credentials must be different references. One identity that can ' +
          'both set up the answer and attest to it is not an independent observer.',
      })
    }
    if (expiresAt.trim() === '') {
      found.push({ fieldId: expiresId, message: 'Say when this authorization stops being valid.' })
    }
    setErrors(found)
    if (found.length > 0) return

    setBusy(true)
    const outcome = await registerEnvironment(client, workspaceId, projectId, {
      name: name.trim(),
      allowedOrigins: originList,
      fixtureResetStrategy: resetStrategy,
      observerCredentialRef: observerRef.trim(),
      resetCredentialRef: resetRef.trim(),
      permittedEffects: ['FIXTURE_SUBMIT', 'FIXTURE_RESET'],
      expiresAt: new Date(expiresAt).toISOString(),
    })
    setBusy(false)

    switch (outcome.kind) {
      case 'ok':
      case 'accepted':
        announce(`Environment ${name.trim()} authorized.`)
        setName('')
        setOrigins('')
        setObserverRef('')
        setResetRef('')
        environments.reload()
        break
      case 'problem':
        // The server names the field where it can. Where it cannot, the message still lands on the
        // summary, which is better than a message with nowhere to go.
        setErrors([
          {
            fieldId:
              typeof (outcome.problem as { field?: unknown }).field === 'string'
                ? originsId
                : nameId,
            message: outcome.problem.detail,
          },
        ])
        break
      case 'offline':
        setErrors([
          {
            fieldId: nameId,
            message: 'The request did not reach the server, so nothing was authorized.',
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
      <h2>Environments</h2>

      <ResourceView resource={environments} what="this project's environments">
        {(page) =>
          page.items.length === 0 ? (
            <EmptyState heading="No environment is authorized" because="nothing-created-yet">
              <p className="af-secondary">
                A journey can only reach an origin somebody listed here. Without one, nothing in
                this project can run.
              </p>
            </EmptyState>
          ) : (
            <DataTable<Environment>
              caption="Authorized environments, their exact scope and whether each is still usable"
              rows={page.items}
              rowKey={(environment) => environment.environmentId}
              columns={[
                {
                  key: 'name',
                  header: 'Environment',
                  isRowHeader: true,
                  cell: (environment) => environment.name,
                },
                {
                  key: 'origins',
                  header: 'Permitted origins',
                  cell: (environment) => (
                    <ul>
                      {environment.allowedOrigins.map((origin) => (
                        <li key={origin}>
                          <code>{origin}</code>
                        </li>
                      ))}
                    </ul>
                  ),
                },
                {
                  key: 'reset',
                  header: 'Reset strategy',
                  cell: (environment) => environment.fixtureResetStrategy,
                },
                {
                  key: 'effects',
                  header: 'Permitted effects',
                  cell: (environment) => environment.permittedEffects.join(', '),
                },
                {
                  key: 'expiry',
                  header: 'Authorization expires',
                  cell: (environment) =>
                    environment.expiresAt === null ? (
                      <span className="af-secondary">Not recorded</span>
                    ) : (
                      <time dateTime={environment.expiresAt}>{environment.expiresAt}</time>
                    ),
                },
                {
                  key: 'usable',
                  header: 'Usable',
                  cell: (environment) => {
                    const because = unusableBecause(environment)
                    return because === null ? (
                      <StatusBadge tone="pass" kind="Environment">
                        Usable
                      </StatusBadge>
                    ) : (
                      // Named, not a bare "no". Expired, revoked and superseded lead to three
                      // different actions.
                      <StatusBadge tone="fail" kind="Environment">
                        {because}
                      </StatusBadge>
                    )
                  },
                },
              ]}
            />
          )
        }
      </ResourceView>

      <div className="af-panel af-stack">
        <h3>Authorize an environment</h3>
        <Notice
          tone="information"
          heading="This records permission; it does not test a connection"
          headingLevel={4}
        >
          <p>
            The origins below are the only ones a journey in this project may reach. A successful
            connection to somewhere else would not make that somewhere else authorized.
          </p>
          <p className="af-secondary">
            Credentials are given as <em>references</em> to a secret profile. No credential value is
            accepted by this form, stored, digested or exported.
          </p>
        </Notice>

        <ErrorSummary submissionId={submissionId} errors={errors} />

        <form onSubmit={(event) => void submit(event)} noValidate>
          <FormField id={nameId} label="Environment name" required>
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
            id={originsId}
            label="Permitted origins"
            hint="One per line, scheme and host exactly as they will be reached."
            required
          >
            {({ id, describedBy, invalid }) => (
              <textarea
                id={id}
                rows={3}
                value={origins}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(event) => setOrigins(event.target.value)}
              />
            )}
          </FormField>

          <FormField id={resetId} label="Fixture reset strategy" required>
            {({ id, describedBy }) => (
              <select
                id={id}
                value={resetStrategy}
                aria-describedby={describedBy}
                onChange={(event) => setResetStrategy(event.target.value)}
              >
                <option value="TRUNCATE_AND_SEED">Truncate and seed</option>
                <option value="TRANSACTIONAL_ROLLBACK">Transactional rollback</option>
                <option value="DEDICATED_TENANT">Dedicated tenant per run</option>
              </select>
            )}
          </FormField>

          <FormField
            id={observerId}
            label="Observer credential reference"
            hint="The identity that reads application state to decide completion. Read-only."
            required
          >
            {({ id, describedBy, invalid }) => (
              <input
                id={id}
                value={observerRef}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(event) => setObserverRef(event.target.value)}
              />
            )}
          </FormField>

          <FormField
            id={resetCredentialId}
            label="Reset credential reference"
            hint="The identity that rewrites fixture state. Must differ from the observer."
            required
          >
            {({ id, describedBy, invalid }) => (
              <input
                id={id}
                value={resetRef}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(event) => setResetRef(event.target.value)}
              />
            )}
          </FormField>

          <FormField
            id={expiresId}
            label="Authorization expires"
            hint="After this, journeys in this project stop being dispatchable to this environment."
            required
          >
            {({ id, describedBy, invalid }) => (
              <input
                id={id}
                type="datetime-local"
                value={expiresAt}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(event) => setExpiresAt(event.target.value)}
              />
            )}
          </FormField>

          <Button type="submit" variant="primary" busy={busy}>
            Authorize environment
          </Button>
        </form>
      </div>
    </section>
  )
}
