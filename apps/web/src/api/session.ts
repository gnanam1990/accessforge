/**
 * The three session calls the shell makes, and the shapes they return.
 *
 * Deliberately narrow. The shell needs to know who the person is and which workspaces they may
 * enter; it does not need, and must not hold, anything resembling a credential.
 */

import type { ApiClient, ApiOutcome } from './client'

export type WorkspaceRole = 'OWNER' | 'MAINTAINER' | 'REVIEWER' | 'VIEWER'

export interface WorkspaceMembership {
  readonly workspaceId: string
  readonly name: string
  readonly role: WorkspaceRole
}

export interface SessionContextPayload {
  readonly userId: string
  readonly email: string
  readonly workspaces: readonly WorkspaceMembership[]
}

export const readSession = (
  client: ApiClient,
  signal?: AbortSignal,
): Promise<ApiOutcome<SessionContextPayload>> =>
  client.request<SessionContextPayload>('/v1/session', signal === undefined ? {} : { signal })

export interface SignInResult {
  readonly userId: string
  readonly expiresAt: string
  readonly signInMode: string
}

export const signIn = (client: ApiClient, email: string): Promise<ApiOutcome<SignInResult>> =>
  client.request<SignInResult>('/v1/sessions', { method: 'POST', body: { email } })

export const signOut = (client: ApiClient): Promise<ApiOutcome<void>> =>
  client.request<void>('/v1/session', { method: 'DELETE' })
