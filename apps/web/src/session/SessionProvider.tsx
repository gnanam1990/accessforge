/**
 * Who is signed in, which workspaces they may enter, and how that ends.
 *
 * The session is a state machine with six states, and they are distinct because the screens they
 * produce are distinct. Collapsing "still checking" into "signed out" is what produces a sign-in
 * form that flashes on every page load; collapsing "the server is unreachable" into "signed out" is
 * what produces a sign-in form nobody can use and no explanation of why.
 *
 *   checking → anonymous | authenticated | unreachable | signInUnavailable
 *   authenticated → anonymous (sign-out, revocation, expiry)
 *
 * **Sign-out clears tenant data before anything else.** `client.endSession()` bumps the request
 * epoch, so every response already in flight is discarded rather than rendered. Module 21's prompt
 * names this failure directly: do not "show cached tenant data after logout". Clearing the React
 * state alone would not be enough — the dangerous data is the response that has not arrived yet.
 *
 * **Membership is re-read, not remembered.** `refresh()` re-reads `/v1/session`, and the workspace
 * navigation is built from the answer. A membership revoked while the tab was open disappears on
 * the next read instead of remaining as a link that leads to a 404 the person cannot explain.
 */

import type { JSX } from 'react'

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { ApiClient } from '../api/client'
import type { Problem } from '../api/problem'
import type { SessionContextPayload, WorkspaceMembership } from '../api/session'
import { readSession, signIn as postSession, signOut as deleteSession } from '../api/session'

export type SessionState =
  | { readonly status: 'checking' }
  | { readonly status: 'anonymous'; readonly endedBecause: 'never-signed-in' | 'session-ended' }
  | {
      readonly status: 'authenticated'
      readonly userId: string
      readonly email: string
      readonly workspaces: readonly WorkspaceMembership[]
    }
  /** The browser could not reach the server. Distinct from anonymous: the person may well be
   * signed in, and offering them a sign-in form would be answering a question they did not ask. */
  | { readonly status: 'unreachable' }
  /** The deployment has no identity provider, so nobody can sign in here. */
  | { readonly status: 'signInUnavailable'; readonly problem: Problem }

export interface SessionApi {
  readonly state: SessionState
  readonly client: ApiClient
  readonly refresh: () => Promise<void>
  readonly signIn: (email: string) => Promise<Problem | null>
  readonly signOut: () => Promise<void>
}

const SessionContext = createContext<SessionApi | null>(null)

export const SessionProvider = ({
  children,
  client,
}: {
  readonly children: ReactNode
  readonly client: ApiClient
}): JSX.Element => {
  const [state, setState] = useState<SessionState>({ status: 'checking' })
  const inFlight = useRef<AbortController | null>(null)

  const applyPayload = useCallback((payload: SessionContextPayload) => {
    setState({
      status: 'authenticated',
      userId: payload.userId,
      email: payload.email,
      workspaces: payload.workspaces,
    })
  }, [])

  const refresh = useCallback(async () => {
    // One session read at a time. Two overlapping reads can resolve in either order, and the loser
    // would overwrite the winner with an older answer — which for a membership list means a
    // workspace reappearing after it was revoked.
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller

    const outcome = await readSession(client, controller.signal)
    if (controller.signal.aborted) return

    switch (outcome.kind) {
      case 'ok':
        applyPayload(outcome.value)
        break
      case 'unauthenticated':
        setState({ status: 'anonymous', endedBecause: 'session-ended' })
        break
      case 'offline':
        setState({ status: 'unreachable' })
        break
      case 'cancelled':
      case 'stale':
        break
      case 'accepted':
      case 'problem':
        setState({ status: 'anonymous', endedBecause: 'never-signed-in' })
        break
    }
  }, [applyPayload, client])

  useEffect(() => {
    void refresh()
    return () => inFlight.current?.abort()
  }, [refresh])

  const signIn = useCallback(
    async (email: string): Promise<Problem | null> => {
      const outcome = await postSession(client, email)
      switch (outcome.kind) {
        case 'ok':
        case 'accepted':
          await refresh()
          return null
        case 'unauthenticated':
          return outcome.problem
        case 'problem':
          if (outcome.problem.code === 'DEPENDENCY_UNAVAILABLE') {
            // Not a rejected credential. The deployment cannot sign anyone in at all, and the
            // screen has to say that rather than inviting a second attempt.
            setState({ status: 'signInUnavailable', problem: outcome.problem })
          }
          return outcome.problem
        case 'offline':
          setState({ status: 'unreachable' })
          return null
        case 'cancelled':
        case 'stale':
          return null
      }
    },
    [client, refresh],
  )

  const signOut = useCallback(async () => {
    // The local state is cleared first and unconditionally. If the request fails the person is still
    // signed out here, which is the safe direction: leaving a shared machine showing a workspace
    // because a sign-out request timed out is the failure that actually hurts someone.
    inFlight.current?.abort()
    setState({ status: 'anonymous', endedBecause: 'session-ended' })
    client.endSession()
    await deleteSession(client)
  }, [client])

  const api = useMemo(
    () => ({ state, client, refresh, signIn, signOut }),
    [state, client, refresh, signIn, signOut],
  )

  return <SessionContext.Provider value={api}>{children}</SessionContext.Provider>
}

export const useSession = (): SessionApi => {
  const api = useContext(SessionContext)
  if (api === null) throw new Error('useSession requires a SessionProvider above it')
  return api
}

/**
 * The membership for one workspace, or null.
 *
 * Returns null for a workspace the person is not a member of, including one they were a member of a
 * moment ago. Callers render the same "not available" as they would for a workspace that does not
 * exist — the server gives one answer for both, and the UI must not be more informative than the
 * server chose to be.
 */
export const membershipFor = (
  state: SessionState,
  workspaceId: string,
): WorkspaceMembership | null => {
  if (state.status !== 'authenticated') return null
  return state.workspaces.find((workspace) => workspace.workspaceId === workspaceId) ?? null
}
