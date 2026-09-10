/**
 * The repair workspace, which does not exist yet — stated rather than mocked.
 *
 * This screen would show the base source, the exact diff, the reproduced finding, the isolated
 * build, the matched comparison and the `PATCH_APPLY` approval. It shows none of them, because none
 * of them exists: the patch sandbox is module 14 and the candidate verifier is module 15, and
 * neither is built. There is no `patch` table, no `verification` table and no route for either.
 *
 * A screen with an inert diff viewer and a disabled Approve button would be indistinguishable, to
 * anyone looking at it, from one waiting for data. This one says what is missing and what each
 * absent piece would have to establish before an approval control could honestly appear.
 */

import type { JSX } from 'react'

import { RouteHeading } from '../a11y/RouteHeading'
import { Notice } from '../components/Notice'

export const PatchScreen = (): JSX.Element => (
  <>
    <RouteHeading>Proposed repair</RouteHeading>

    <Notice tone="information" heading="No repair can exist in this build" headingLevel={2}>
      <p>
        A proposed repair is produced by the patch sandbox and proved by the candidate verifier.
        Neither is implemented, so there is no patch to inspect, no diff to read and nothing that
        could be approved.
      </p>
    </Notice>

    <section className="af-stack">
      <h2>What each missing piece would have to establish</h2>
      <dl>
        <dt>The diff, and its base</dt>
        <dd>
          The exact source revision it applies to and the digest of the change itself. A diff shown
          without its base is a change to something unstated.
        </dd>
        <dt>The reproduced finding</dt>
        <dd>
          A complete, valid, failed run that showed the defect again. Reviewer agreement is not a
          substitute for reproduction.
        </dd>
        <dt>The isolated build</dt>
        <dd>
          That the candidate was built and served from a controlled endpoint bound to its artifact.
          A marker served by the application is not deployment provenance.
        </dd>
        <dt>The matched comparison</dt>
        <dd>
          A reproduced baseline failure and a candidate pass under the same frozen journey,
          assertions, fixture, reader profile, evaluator, policy and budgets. Anything else is two
          runs, not a comparison.
        </dd>
        <dt>The protected checks</dt>
        <dd>
          That the change did not remove validation, authorization or working behaviour. A green
          accessibility assertion does not override a failing functional requirement.
        </dd>
        <dt>The approval</dt>
        <dd>
          Scope <code>PATCH_APPLY</code>, with its expiry, the base revision and the patch digest,
          rechecked at the moment of application rather than when the button was drawn. It permits
          an isolated candidate build and nothing else: it does not merge, deploy or publish.
        </dd>
      </dl>
    </section>

    <Notice tone="warning" heading="Why this is not a placeholder screen" headingLevel={2}>
      <p>
        An inert diff viewer beside a disabled Approve button would look the same as one waiting for
        data, and somebody would eventually wire it to something. There is nothing here to wire.
      </p>
    </Notice>
  </>
)
