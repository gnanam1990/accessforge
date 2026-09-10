/**
 * Renders one resource's state, and forces the caller to say what each one looks like.
 *
 * The `ready` case is a function rather than children, so there is no way to render content while
 * the resource is loading or failed — which is how a screen ends up showing an empty table for a
 * request that was refused.
 *
 * `gone` renders nothing at all. The session ended; the shell is already replacing the whole page,
 * and a problem notice flashing on the way out would be noise about something the person cannot act
 * on.
 */

import type { JSX, ReactNode } from 'react'

import { FailureState, LoadingState, OfflineState, PermissionDeniedState } from './states'
import type { Resource } from '../api/useResource'

export interface ResourceViewProps<T> {
  readonly resource: Resource<T>
  /** What is being loaded, in words, for the loading message. */
  readonly what: string
  readonly children: (value: T, loadedAt: string) => ReactNode
}

export const ResourceView = <T,>({
  resource,
  what,
  children,
}: ResourceViewProps<T>): JSX.Element | null => {
  const { state, reload } = resource
  switch (state.kind) {
    case 'loading':
      return <LoadingState what={what} />
    case 'offline':
      return <OfflineState onRetry={reload} />
    case 'gone':
      return null
    case 'problem':
      // Permission denied is its own screen, because "you may not" and "it broke" ask the reader to
      // do completely different things. Not-found is deliberately *not* special-cased here: the
      // server answers 404 for a resource in another workspace, and a UI that explained the
      // difference would rebuild the cross-tenant existence oracle.
      return state.problem.code === 'PERMISSION_DENIED' ? (
        <PermissionDeniedState problem={state.problem} />
      ) : (
        <FailureState problem={state.problem} onRetry={reload} />
      )
    case 'ready':
      return <>{children(state.value, state.loadedAt)}</>
  }
}
