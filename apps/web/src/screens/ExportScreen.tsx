/**
 * One evidence export: what it contains, what it proves, and what it does not.
 *
 * The limits are the product here. An export is the artefact most likely to be read by somebody who
 * was not present — a regulator, an auditor, a customer — and the failure mode is a bundle that
 * looks like proof of accessibility. So every limitation the server records is displayed, in full,
 * above the download instruction rather than below it.
 *
 * **Trust level is displayed as attribution, not validity.** A signature attributes a bundle to a
 * named issuer; it does not make the bundle's contents true. A verifying key that travelled inside
 * the bundle attributes it to nobody, because a forger signs with their own key and embeds it.
 *
 * **Retention is a snapshot.** The state shown is what the server held when the bundle was made,
 * not what it holds now, and the screen says so — otherwise an old export reads as a statement
 * about the present.
 *
 * **Nothing is published.** Requesting an export produces a private record. There is no share link
 * and no publication control: publication to GitHub is module 20's separately authorized workflow,
 * and module 20 does not exist.
 */

import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { Notice } from '../components/Notice'
import { ResourceView } from '../components/ResourceView'
import { StatusBadge } from '../components/StatusBadge'
import { getExport } from '../api/resources'
import { useResource } from '../api/useResource'
import { useSession } from '../session/SessionProvider'
import { useWorkspaceId } from './useWorkspaceId'
import { useExportId } from './useExportId'

/**
 * What each trust level actually claims, in words rather than as a colour.
 *
 * The values are module 17's, and the first version of this map guessed at two that do not exist —
 * a real bundle came back `INCOMPLETE` and fell through to the unrecognised branch. The fallback
 * behaved correctly, which is why the mistake was visible rather than silent, and these are now the
 * levels the exporter actually produces.
 */
const TRUST_MEANING: Record<string, string> = {
  FULLY_VERIFIABLE:
    'Every input the verifier needs is present and intact, so somebody holding this bundle can ' +
    'recompute the verdict rather than take the issuer’s word for it.',
  LIMITED_DISCLOSURE:
    'Inputs were redacted or withheld on purpose. The chain and the identities check out; the ' +
    'verdict cannot be recomputed from what is here, and this bundle does not claim it can.',
  INCOMPLETE:
    'Required evidence is missing rather than deliberately withheld. This is not a disclosure ' +
    'decision: something that should exist does not, and the bundle supports correspondingly less.',
}

/** A signature attributes; it never establishes that the contents are true. */
const SIGNATURE_MEANING =
  'Where a bundle is signed, the signature attributes the manifest to the named issuer. It does ' +
  'not make the manifest’s contents true, does not establish that any person could use the ' +
  'application, and does not independently verify the events the evidence describes.'

export const ExportScreen = (): JSX.Element => {
  const workspaceId = useWorkspaceId()
  const exportId = useExportId()
  const { client } = useSession()
  const record = useResource(
    (signal) => getExport(client, workspaceId, exportId, signal),
    [client, workspaceId, exportId],
  )

  return (
    <ResourceView resource={record} what="this export">
      {(value) => (
        <>
          <RouteHeading>Export {value.exportId.slice(0, 8)}</RouteHeading>

          <Notice tone="warning" heading="What this bundle does not establish" headingLevel={2}>
            <ul>
              {value.limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
              <li>
                It describes the runs and attempts it names, on the profiles they used. It is not
                evidence that the application is usable by people with disabilities in general, and
                it is not a certification of anything.
              </li>
            </ul>
          </Notice>

          <section className="af-stack">
            <h2>What it is</h2>
            <dl>
              <dt>Run</dt>
              <dd>
                <code>{value.runId}</code>
              </dd>
              <dt>Attempt</dt>
              <dd>
                <code>{value.attemptId}</code>
              </dd>
              <dt>Bundle digest</dt>
              <dd>
                <code>{value.bundleDigest}</code>
              </dd>
              <dt>Trust level</dt>
              <dd>
                <StatusBadge tone="neutral" kind="Trust">
                  {value.trustLevel}
                </StatusBadge>
                <p className="af-secondary">
                  {TRUST_MEANING[value.trustLevel] ??
                    'This build does not recognise that trust level, so it is shown as reported.'}
                </p>
                <p className="af-secondary">{SIGNATURE_MEANING}</p>
              </dd>
              <dt>Signing key</dt>
              <dd>
                <code>{value.signingKeyId}</code>
              </dd>
              <dt>Created</dt>
              <dd>
                <time dateTime={value.createdAt}>{value.createdAt}</time>
              </dd>
              <dt>Expires</dt>
              <dd>
                {/* Shown before any download instruction: an export that has expired is a link that
                    will fail, and finding that out afterwards wastes somebody's afternoon. */}
                <time dateTime={value.expiresAt}>{value.expiresAt}</time>
              </dd>
            </dl>
          </section>

          <section className="af-stack">
            <h2>Retention when this was made</h2>
            <p className="af-secondary">
              What the server held at export time, not what it holds now. An artifact deleted since
              is still absent from this bundle, and one deleted before it was made was already
              absent.
            </p>
            <pre className="af-mono">{JSON.stringify(value.retentionAtExport, null, 2)}</pre>
          </section>

          <section className="af-stack">
            <h2>Checking it</h2>
            <p>
              <code>{value.verifyWith}</code>
            </p>
            <p className="af-secondary">
              Obtain the verifying key from the issuer, separately from the bundle. A key that
              travelled inside it attributes the bundle to nobody: a forger signs with their own key
              and embeds it.
            </p>
          </section>

          <section className="af-stack">
            <h2>Sharing it</h2>
            <Notice tone="information" heading="This export is private" headingLevel={3}>
              <p>
                Nothing has been published and there is no share link. Publication to a repository is
                a separately authorized action under <code>GITHUB_PUBLISH</code>, and that workflow
                belongs to module 20, which does not exist in this build.
              </p>
            </Notice>
          </section>
        </>
      )}
    </ResourceView>
  )
}
