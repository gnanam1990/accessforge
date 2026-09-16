/** Provider discovery never guesses an authentication mode or accepts a redirect URL. */
import { useEffect, useId, useState } from 'react'
import type { JSX, ReactNode } from 'react'

import { Button } from '../components/Button'
import { Notice } from '../components/Notice'
import { useSession } from './SessionProvider'

type Provider = 'none' | 'local-development' | 'github'
type Discovery = { readonly status: 'loading' } | { readonly status: 'failed' }
  | { readonly status: 'ready'; readonly provider: Provider }

const providerFrom = (value: unknown): Provider | null => {
  if (value === null || typeof value !== 'object' || Array.isArray(value)
      || Object.keys(value).length !== 1 || !('provider' in value)) return null
  return value.provider === 'none' || value.provider === 'local-development'
    || value.provider === 'github' ? value.provider : null
}

export const SignInProviderGate = ({ children }: { readonly children: ReactNode }): JSX.Element => {
  const { client } = useSession()
  const [discovery, setDiscovery] = useState<Discovery>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const descriptionId = useId()

  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    setDiscovery({ status: 'loading' })
    void client.request<unknown>('/v1/auth/options', { signal: controller.signal }).then((outcome) => {
      if (disposed || controller.signal.aborted) return
      const provider = outcome.kind === 'ok' ? providerFrom(outcome.value) : null
      setDiscovery(provider === null ? { status: 'failed' } : { status: 'ready', provider })
    })
    return () => { disposed = true; controller.abort() }
  }, [client, attempt])

  if (discovery.status === 'loading') return <p role="status">Loading sign-in options…</p>
  if (discovery.status === 'failed') return (
    <Notice tone="warning" heading="Sign-in options are unavailable" headingLevel={2} live>
      <p>We could not confirm how this deployment signs users in. No credentials have been sent.</p>
      <Button onClick={() => setAttempt((value) => value + 1)}>Retry sign-in options</Button>
    </Notice>
  )
  if (discovery.provider === 'none') return (
    <Notice tone="warning" heading="Sign-in is not configured" headingLevel={2}>
      <p>This deployment has no identity provider. Ask the operator to configure sign-in;
        entering credentials would not resolve it.</p>
    </Notice>
  )
  if (discovery.provider === 'github') return (
    <div className="af-panel af-stack">
      <a className="af-button af-button--primary" href="/v1/auth/github/start"
        aria-describedby={descriptionId}>Continue with GitHub</a>
      <p id={descriptionId}>Continue to GitHub to sign in, then return to AccessForge.
        Your GitHub account must already be linked by an operator; signing in does not grant
        workspace access.</p>
    </div>
  )
  return <>{children}</>
}
