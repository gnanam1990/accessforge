/**
 * The operational states every route must implement, as distinct components.
 *
 * They are in one file on purpose: the defect this guards against is two of them becoming
 * indistinguishable, and that is much easier to notice when they sit next to each other.
 *
 * The specification names the failures directly. UI-UX section 7 requires loading without fake data,
 * first-use empty, populated, partial evidence, validation error, dependency unavailable, stale
 * revision conflict, permission denied, expired session, disconnected stream and recoverable
 * failure — all distinct. Module 21's prompt adds the two that matter most: "A skeleton is not proof
 * that data exists; a failed request must not become an empty result."
 *
 * So `LoadingState` renders no shapes that resemble content, and `EmptyState` takes a `because`
 * parameter that forces the caller to say which kind of nothing this is.
 */

import type { JSX } from 'react'

import type { ReactNode } from 'react'

import { Button } from './Button'
import { Notice } from './Notice'
import type { Problem } from '../api/problem'

/**
 * Work in progress.
 *
 * Text, not a skeleton. A grey rectangle in the shape of a table is a claim that a table is coming,
 * and it is indistinguishable from one that arrived empty or failed — which is the precise confusion
 * the prompt names. The live region is polite and says what is being waited for.
 */
export const LoadingState = ({ what }: { readonly what: string }): JSX.Element => (
  <p role="status" className="af-secondary">
    Loading {what}…
  </p>
)

/**
 * A successful request that returned nothing.
 *
 * `because` is required so the caller cannot render this for a failure. There are two honest
 * reasons: nothing has been created yet, or a filter excluded everything — and they need different
 * next steps, because clearing a filter is not the same as creating the first project.
 */
export const EmptyState = ({
  heading,
  because,
  children,
  action,
}: {
  readonly heading: string
  readonly because: 'nothing-created-yet' | 'filtered-out'
  readonly children?: ReactNode
  readonly action?: ReactNode
}): JSX.Element => (
  <div className="af-panel af-stack">
    <h2 className="af-notice__heading">{heading}</h2>
    <p className="af-secondary">
      {because === 'nothing-created-yet'
        ? 'The server answered, and there is nothing here yet.'
        : 'The server answered, and the current filters exclude every record.'}
    </p>
    {children}
    {action}
  </div>
)

/**
 * A request that failed. Never an empty list.
 *
 * The problem's `requestId` is shown because it is the only identifier the server puts in an error
 * body, and it is what turns "it broke" into something an operator can find in a log.
 *
 * `onRetry` is accepted only for reads. A mutation is not offered a retry button here: a retried
 * approval is a second approval, and the decision to repeat one belongs to the person, stated in
 * words, on a screen that names the consequence.
 */
export const FailureState = ({
  problem,
  onRetry,
}: {
  readonly problem: Problem
  readonly onRetry?: () => void
}): JSX.Element => (
  <Notice
    tone="problem"
    heading={problem.title}
    headingLevel={2}
    live
    actions={
      onRetry === undefined ? undefined : (
        <Button variant="secondary" onClick={onRetry}>
          Try loading this again
        </Button>
      )
    }
  >
    <p>{problem.detail}</p>
    <p className="af-secondary">
      Problem code <code>{problem.code}</code>
      {problem.requestId !== null && (
        <>
          {' · request '}
          <code>{problem.requestId}</code>
        </>
      )}
    </p>
  </Notice>
)

/**
 * The caller is authenticated and lacks the permission.
 *
 * Distinct from not-found on purpose, and *only* shown when the server said `PERMISSION_DENIED`. The
 * server answers 404 for a resource in another workspace, and the UI must not improve on that: a
 * "you do not have permission to see this" rendered for a 404 would reconstruct the cross-tenant
 * existence oracle the API spent module 18 closing.
 */
export const PermissionDeniedState = ({
  problem,
}: {
  readonly problem: Problem
}): JSX.Element => (
  <Notice tone="warning" heading="You do not have permission to do this" headingLevel={2} live>
    <p>{problem.detail}</p>
    <p className="af-secondary">
      A workspace owner can change your role. Permission is decided by the server on every request,
      so a control that looks available may still be refused.
    </p>
  </Notice>
)

export const NotFoundState = (): JSX.Element => (
  <Notice tone="warning" heading="Not available" headingLevel={2} live>
    <p>
      No such resource is available to you. The server gives the same answer for something that does
      not exist and something that belongs to another workspace, so this does not tell you which
      applies.
    </p>
  </Notice>
)

/** The session ended: expired, revoked, or signed out in another tab. */
export const ExpiredSessionState = ({
  onSignInAgain,
}: {
  readonly onSignInAgain: () => void
}): JSX.Element => (
  <Notice
    tone="warning"
    heading="Your session has ended"
    headingLevel={2}
    live
    actions={
      <Button variant="primary" onClick={onSignInAgain}>
        Sign in again
      </Button>
    }
  >
    <p>
      Anything you had not submitted was not saved. Nothing you were looking at is shown any more,
      because it belonged to a session the server no longer recognises.
    </p>
  </Notice>
)

/** A dependency the server needs is unavailable. Not the user's fault and not retryable by them. */
export const DependencyUnavailableState = ({
  problem,
}: {
  readonly problem: Problem
}): JSX.Element => (
  <Notice tone="problem" heading="A service this depends on is unavailable" headingLevel={2} live>
    <p>{problem.detail}</p>
    <p className="af-secondary">
      This is a server-side dependency rather than anything about your request. Retrying the same
      request will not change the answer until it is restored.
    </p>
  </Notice>
)

/** The browser could not reach the server. */
export const OfflineState = ({ onRetry }: { readonly onRetry: () => void }): JSX.Element => (
  <Notice
    tone="warning"
    heading="No connection to the server"
    headingLevel={2}
    live
    actions={
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    }
  >
    <p>
      The request did not reach the server, so nothing was changed by it. What is on screen may be
      out of date.
    </p>
  </Notice>
)

/**
 * Shown above content that is known to be older than the server's.
 *
 * A banner rather than a replacement. Removing the content would discard the last thing the person
 * actually knew; UI-UX section 5 requires the last known state to be preserved with a stale banner,
 * and is explicit that reconnecting does not imply the work completed.
 */
export const StaleDataBanner = ({
  asOf,
  reason,
}: {
  readonly asOf: string
  readonly reason: string
}): JSX.Element => (
  <Notice tone="warning" heading="This may be out of date" headingLevel={2}>
    <p>
      Last confirmed with the server at <time dateTime={asOf}>{asOf}</time>. {reason}
    </p>
    <p className="af-secondary">
      Reconnecting does not mean work finished. A run's state is whatever the server reports when it
      is next read, not what was on screen when the connection dropped.
    </p>
  </Notice>
)

/**
 * A route whose screens belong to a module that has not been built.
 *
 * Explicit, and naming the module. The alternative — a plausible-looking screen with invented rows —
 * is the thing module 21's acceptance gate forbids: "No placeholder route or static data is
 * presented as a completed feature from later modules." An empty state would be a lie of a
 * different kind, since it would claim the server was asked and answered.
 */
export const NotBuiltYetState = ({
  screen,
  ownedByModule,
}: {
  readonly screen: string
  readonly ownedByModule: number
}): JSX.Element => (
  <Notice tone="information" heading={`${screen} is not built yet`} headingLevel={2}>
    <p>
      This route exists so the navigation, breadcrumbs and deep links are real. The screen itself
      belongs to module {ownedByModule} and has not been implemented.
    </p>
    <p className="af-secondary">
      Nothing is requested from the server here, and nothing shown on this page is data. A mockup in
      its place could be mistaken for a working feature.
    </p>
  </Notice>
)
