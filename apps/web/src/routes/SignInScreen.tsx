/**
 * Sign-in, and the honest failure when there is nothing to sign in with.
 *
 * Two distinct screens behind one route:
 *
 * **A deployment with an identity provider** shows a form with one field. The field has a real
 * label, not a placeholder, and the submit path uses the error-summary pattern from UI-UX section 4:
 * on failure, focus moves once to a focusable summary that links to the field, the specific inline
 * error stays connected by `aria-describedby`, and the entered value is preserved.
 *
 * **A deployment without one** shows a dependency-unavailable notice and no form at all. This is the
 * default. Presenting a login form that could never succeed would be a placeholder route dressed as
 * a feature, and the person filling it in would conclude their credentials were wrong.
 *
 * The form does not validate the address itself beyond emptiness. Client-side email validation
 * rejects perfectly valid addresses, and the server is the only party that can answer the question
 * anyway.
 */

import type { JSX } from 'react'

import { useEffect, useRef, useState } from 'react'

import { Button } from '../components/Button'
import { ErrorSummary } from '../components/ErrorSummary'
import { FormField } from '../components/FormField'
import { Notice } from '../components/Notice'
import { RouteHeading } from '../a11y/RouteHeading'
import { DependencyUnavailableState } from '../components/states'
import { useSession } from '../session/SessionProvider'
import type { SignInFailure } from '../session/SessionProvider'

export const SignInScreen = (): JSX.Element => {
  const { state, signIn } = useSession()
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submissionId, setSubmissionId] = useState(0)
  const [busy, setBusy] = useState(false)
  const fieldId = useRef<string | null>(null)

  useEffect(() => {
    if (state.status === 'authenticated') setError(null)
  }, [state.status])

  if (state.status === 'signInUnavailable') {
    return (
      <main className="af-stack" style={{ padding: 'var(--af-space-8)' }}>
        <RouteHeading>Sign in to AccessForge</RouteHeading>
        <DependencyUnavailableState problem={state.problem} />
        <p className="af-secondary">
          Authentication is delegated to an identity provider, and this deployment has not been given
          one. No credential would work here, so no sign-in form is offered.
        </p>
      </main>
    )
  }

  const submit = async (event: React.FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSubmissionId((current) => current + 1)

    if (email.trim() === '') {
      setError('Enter the email address of your AccessForge account.')
      return
    }

    setBusy(true)
    setError(null)
    const failure: SignInFailure | null = await signIn(email.trim())
    setBusy(false)
    if (failure === null) return
    setError(
      failure.kind === 'unreachable'
        ? 'The sign-in request did not reach the server, so nothing was changed by it. Check your ' +
            'connection and try again.'
        : failure.problem.detail,
    )
  }

  return (
    <main className="af-stack" style={{ padding: 'var(--af-space-8)', maxWidth: '40rem' }}>
      <RouteHeading>Sign in to AccessForge</RouteHeading>

      <ErrorSummary
        submissionId={submissionId}
        errors={
          error === null || fieldId.current === null
            ? []
            : [{ fieldId: fieldId.current, message: error }]
        }
      />

      <form onSubmit={(event) => void submit(event)} noValidate className="af-panel">
        <FormField
          label="Email address"
          hint="The address your workspace owner used to invite you."
          {...(error === null ? {} : { error })}
          required
        >
          {({ id, describedBy, invalid }) => {
            fieldId.current = id
            return (
              <input
                id={id}
                name="email"
                type="email"
                autoComplete="username"
                value={email}
                aria-describedby={describedBy}
                aria-invalid={invalid || undefined}
                // Validated on submission, never on blur. Validating on blur and moving focus makes
                // a form impossible to complete with a keyboard.
                onChange={(event) => setEmail(event.target.value)}
              />
            )
          }}
        </FormField>

        <Button type="submit" variant="primary" busy={busy}>
          Sign in
        </Button>
      </form>

      {state.status === 'anonymous' && state.signOutUnconfirmed && (
        <Notice tone="warning" heading="Your sign-out was not confirmed" headingLevel={2} live>
          <p>
            This browser has been cleared, so nothing of your workspace is shown here. The server
            never confirmed that the session itself was ended, which means it may still be usable by
            anyone holding a copy of it.
          </p>
          <p className="af-secondary">
            If you are on a machine other people can reach, sign in again and sign out from a working
            connection, or ask an owner to end your sessions.
          </p>
        </Notice>
      )}
    </main>
  )
}
