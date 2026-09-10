/**
 * Workspace settings: membership, usage against the configured allowance, and retention.
 *
 * The rules this screen exists to keep visible:
 *
 * **Measured, estimated and unavailable are three different numbers.** They are three columns, not
 * one total. "This system counted it", "a provider reported it about itself" and "the count could
 * not be obtained" lead an operator to different decisions, and an unavailable count shown as zero
 * reads as nothing having happened.
 *
 * **Nothing here is a cost.** No price, no currency, no computed saving. R1 measures usage and
 * enforces a limit somebody set; it collects no money. A test asserts the response carries no
 * field with a name of that kind.
 *
 * **Read-only is a real view.** A person without `WORKSPACE_CONFIGURE` sees every value and no
 * form. The server decides on every request regardless — permission inferred from what the UI
 * showed is not permission — but hiding a control somebody cannot use is honest, and disabling one
 * with no explanation is not.
 *
 * **A limit is changed against the revision it was read at.** Two administrators raising a limit at
 * the same moment is exactly when a silent last-writer-wins discards a decision without telling
 * either of them.
 *
 * **Deleting evidence costs something, and the screen says what.** Every retention class declares
 * whether deleting under it makes a completeness claim untrue, and that flag comes from what the
 * class *is* rather than from configuration — an administrator cannot set it to false, and setting
 * it to false would not make deletion stop breaking anything.
 */

import { useId, useState } from 'react'
import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { useAnnouncer } from '../a11y/Announcer'
import { Button } from '../components/Button'
import { DataTable } from '../components/DataTable'
import { ErrorSummary } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import {
  configureEntitlement,
  listMembers,
  readRetention,
  readUsage,
} from '../api/resources'
import type { Member, RetentionClass, UsageRow } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession, membershipFor } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'

/** The roles that may change a limit or a retention policy. Owner only, per module 03's matrix. */
const MAY_CONFIGURE = new Set(['OWNER'])

const KIND_LABEL: Record<string, string> = {
  RUN_ADMITTED: 'Runs admitted',
  ACTION_DISPATCHED: 'Actions dispatched',
  WALL_SECONDS: 'Wall-clock seconds',
  MODEL_TOKENS: 'Model tokens',
}

const EntitlementForm = ({
  workspaceId,
  revision,
  current,
  onSaved,
}: {
  readonly workspaceId: string
  readonly revision: number
  readonly current: Record<string, number>
  readonly onSaved: () => void
}): JSX.Element => {
  const { client } = useSession()
  const { announce } = useAnnouncer()
  const reasonId = useId()
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(current).map(([key, value]) => [key, String(value)])),
  )
  const [reason, setReason] = useState('')
  const [errors, setErrors] = useState<readonly { fieldId: string; message: string }[]>([])
  const [submissionId, setSubmissionId] = useState(0)
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const fieldIds = useId()

  const idFor = (key: string): string => `${fieldIds}-${key}`

  const submit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSubmissionId((current2) => current2 + 1)
    setRefusal(null)

    const found: { fieldId: string; message: string }[] = []
    const numbers: Record<string, number> = {}
    for (const [key, raw] of Object.entries(values)) {
      const parsed = Number(raw)
      if (raw.trim() === '' || !Number.isInteger(parsed) || parsed < 0) {
        found.push({
          fieldId: idFor(key),
          message: `${key} must be a whole number of zero or more. There is no value meaning unlimited.`,
        })
      } else {
        numbers[key] = parsed
      }
    }
    if (reason.trim() === '') {
      found.push({
        fieldId: reasonId,
        message: 'Say why this limit is what it is. A limit with no reason is one nobody can be asked about later.',
      })
    }
    setErrors(found)
    if (found.length > 0) return

    setBusy(true)
    const outcome = await configureEntitlement(
      client,
      workspaceId,
      { ...numbers, reason: reason.trim() },
      revision,
    )
    setBusy(false)

    switch (outcome.kind) {
      case 'ok':
      case 'accepted':
        announce(`Allowance saved as revision ${outcome.value.revision}.`)
        setReason('')
        onSaved()
        break
      case 'problem':
        setRefusal(outcome.problem.detail)
        break
      case 'offline':
        setRefusal('The server did not answer, so whether the limit was changed is unknown.')
        break
      case 'cancelled':
      case 'stale':
      case 'unauthenticated':
        break
    }
  }

  return (
    <div className="af-panel af-stack">
      <h3>Change the allowance</h3>
      <p className="af-secondary">
        Saving appends a revision. The current one is unchanged, so a run admitted under it stays
        explainable.
      </p>

      <ErrorSummary submissionId={submissionId} errors={errors} />

      {refusal !== null && (
        <Notice tone="problem" heading="The allowance was not changed" headingLevel={4} live>
          <p>{refusal}</p>
        </Notice>
      )}

      <form onSubmit={(event) => void submit(event)} noValidate>
        {Object.keys(current).map((key) => (
          <FormField key={key} id={idFor(key)} label={key} required>
            {({ id, describedBy, invalid }) => (
              <input
                id={id}
                type="number"
                min={0}
                value={values[key] ?? ''}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                onChange={(event) =>
                  setValues((all) => ({ ...all, [key]: event.target.value }))
                }
              />
            )}
          </FormField>
        ))}

        <FormField
          id={reasonId}
          label="Why this limit"
          hint="Recorded with the revision, so somebody can be asked about it later."
          required
        >
          {({ id, describedBy, invalid }) => (
            <input
              id={id}
              value={reason}
              aria-describedby={describedBy}
              aria-invalid={invalid || undefined}
              onChange={(event) => setReason(event.target.value)}
            />
          )}
        </FormField>

        <Button type="submit" variant="primary" busy={busy}>
          Save allowance
        </Button>
      </form>
    </div>
  )
}

export const SettingsScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const { client, state } = useSession()
  const [saved, setSaved] = useState(0)

  const membership = membershipFor(state, workspaceId)
  const mayConfigure = membership !== null && MAY_CONFIGURE.has(membership.role)

  const members = useResource(
    (signal) => listMembers(client, workspaceId, signal),
    [client, workspaceId],
  )
  const usage = useResource(
    (signal) => readUsage(client, workspaceId, signal),
    [client, workspaceId, saved],
  )
  const retention = useResource(
    (signal) => readRetention(client, workspaceId, signal),
    [client, workspaceId],
  )

  return (
    <>
      <RouteHeading>Workspace settings</RouteHeading>

      {!mayConfigure && (
        <Notice tone="information" heading="You can read these settings but not change them" headingLevel={2}>
          <p>
            Changing an allowance or a retention policy needs the owner role. Every value below is
            shown in full — a setting somebody cannot change is still one they may need to know.
          </p>
        </Notice>
      )}

      <section className="af-stack">
        <h2>Membership</h2>
        <ResourceView resource={members} what="this workspace's members">
          {(page) => (
            <DataTable<Member>
              caption="People with an active membership in this workspace, and what each may do"
              rows={page.items}
              rowKey={(member) => member.userId}
              columns={[
                { key: 'email', header: 'Person', isRowHeader: true, cell: (m) => m.email },
                { key: 'role', header: 'Role', cell: (m) => m.role },
              ]}
            />
          )}
        </ResourceView>
        <p className="af-secondary">
          Membership is granted and revoked by an owner. Revocation takes effect on the next
          request, not on the next sign-in.
        </p>
      </section>

      <section className="af-stack">
        <h2>Usage and allowance</h2>
        <ResourceView resource={usage} what="this workspace's usage">
          {(value) => (
            <>
              <Notice tone="information" heading="What these numbers are" headingLevel={3}>
                <p>{value.meaning}</p>
              </Notice>
              <p className="af-secondary">
                Measured over a {value.window}. Allowance revision {value.entitlementRevision}, set
                by <code>{value.configuredBy}</code> — {value.reason}
              </p>

              <DataTable<UsageRow>
                caption="Consumption in the current window, by how each number was obtained"
                rows={value.usage}
                rowKey={(row) => row.kind}
                columns={[
                  {
                    key: 'kind',
                    header: 'Kind',
                    isRowHeader: true,
                    cell: (row) => KIND_LABEL[row.kind] ?? row.kind,
                  },
                  { key: 'measured', header: 'Measured', cell: (row) => String(row.measured) },
                  {
                    key: 'estimated',
                    header: 'Estimated',
                    cell: (row) => (
                      <>
                        {String(row.estimated)}
                        {row.estimated > 0 && (
                          <div className="af-secondary">reported by a provider about itself</div>
                        )}
                      </>
                    ),
                  },
                  {
                    key: 'unavailable',
                    header: 'Unmeasurable events',
                    cell: (row) =>
                      row.unavailableEvents === 0 ? (
                        <span className="af-secondary">—</span>
                      ) : (
                        <>
                          {String(row.unavailableEvents)}
                          {/* Not zero usage: the quantity could not be obtained. */}
                          <div className="af-secondary">quantity could not be obtained</div>
                        </>
                      ),
                  },
                  { key: 'limit', header: 'Limit', cell: (row) => String(row.limit) },
                  {
                    key: 'remaining',
                    header: 'Remaining',
                    cell: (row) => (
                      <StatusBadge tone={row.remaining === 0 ? 'fail' : 'neutral'} kind="Remaining">
                        {String(row.remaining)}
                      </StatusBadge>
                    ),
                  },
                ]}
              />

              <p>
                {value.concurrentRuns} of {value.maxConcurrentRuns} concurrent runs in progress.
              </p>

              {mayConfigure && (
                <EntitlementForm
                  workspaceId={workspaceId}
                  revision={value.entitlementRevision}
                  current={{
                    maxRunsPerDay:
                      value.usage.find((row) => row.kind === 'RUN_ADMITTED')?.limit ?? 0,
                    maxActionsPerDay:
                      value.usage.find((row) => row.kind === 'ACTION_DISPATCHED')?.limit ?? 0,
                    maxWallSecondsPerDay:
                      value.usage.find((row) => row.kind === 'WALL_SECONDS')?.limit ?? 0,
                    maxModelTokensPerDay:
                      value.usage.find((row) => row.kind === 'MODEL_TOKENS')?.limit ?? 0,
                    maxConcurrentRuns: value.maxConcurrentRuns,
                  }}
                  onSaved={() => setSaved((current) => current + 1)}
                />
              )}
            </>
          )}
        </ResourceView>
      </section>

      <section className="af-stack">
        <h2>Retention</h2>
        <ResourceView resource={retention} what="this workspace's retention policy">
          {(policy) => (
            <>
              {policy.revision === 0 ? (
                <Notice tone="warning" heading="These are defaults, not choices" headingLevel={3}>
                  <p>
                    Nobody has configured retention for this workspace, so the periods below are
                    what applies in the absence of a decision. "No policy" would otherwise mean
                    keeping everything indefinitely, which is a decision made by omission.
                  </p>
                </Notice>
              ) : (
                <p className="af-secondary">Policy revision {policy.revision}.</p>
              )}

              <Notice tone="warning" heading="What deleting evidence costs" headingLevel={3}>
                <ul>
                  {policy.limits.map((limit) => (
                    <li key={limit}>{limit}</li>
                  ))}
                </ul>
              </Notice>

              <DataTable<RetentionClass>
                caption="Evidence classes, how long each is kept, and what deleting one costs"
                rows={policy.classes}
                rowKey={(entry) => entry.evidenceClass}
                columns={[
                  {
                    key: 'class',
                    header: 'Class',
                    isRowHeader: true,
                    cell: (entry) => entry.evidenceClass,
                  },
                  {
                    key: 'days',
                    header: 'Kept for',
                    cell: (entry) =>
                      entry.retainDays === 0 ? 'Not kept beyond the run' : `${entry.retainDays} days`,
                  },
                  {
                    key: 'consent',
                    header: 'Needs consent',
                    cell: (entry) => (entry.consentRequired ? 'Yes' : 'No'),
                  },
                  {
                    key: 'completeness',
                    header: 'Deleting breaks a completeness claim',
                    cell: (entry) => (
                      <StatusBadge
                        tone={entry.invalidatesCompleteness ? 'fail' : 'neutral'}
                        kind="Deletion"
                      >
                        {entry.invalidatesCompleteness ? 'Yes' : 'No'}
                      </StatusBadge>
                    ),
                  },
                  { key: 'meaning', header: 'What it holds', cell: (entry) => entry.meaning },
                ]}
              />
            </>
          )}
        </ResourceView>
      </section>
    </>
  )
}
