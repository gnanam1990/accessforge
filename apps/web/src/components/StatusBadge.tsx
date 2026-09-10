/**
 * Run status and outcome, as text with a shape, never as a colour.
 *
 * The status vocabulary is the server's. This component maps each value to an emphasis colour and a
 * monospace mark, and both are reinforcement: the label carries the meaning, and a reader who
 * cannot distinguish the colours — or who is listening rather than looking — gets the same
 * information from the text alone.
 *
 * There is deliberately **no** mapping that produces a success appearance from a status. UI-UX
 * section 5 requires status and outcome to stay separate fields, and a badge that turned `RUNNING`
 * green would be the UI deriving a verdict, which is the server's job and nobody else's.
 */

import type { JSX } from 'react'

export type RunStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'FINALIZING'
  | 'COMPLETED'
  | 'INTERRUPTED'
  | 'CANCELLED'

export type RunOutcome = 'NOT_EVALUATED' | 'PASS' | 'FAIL' | 'INCONCLUSIVE'

type Tone = 'neutral' | 'progress' | 'pass' | 'fail' | 'inconclusive' | 'interrupted'

const STATUS_TONE: Record<RunStatus, Tone> = {
  QUEUED: 'neutral',
  RUNNING: 'progress',
  FINALIZING: 'progress',
  // Not a tone of its own. "Ended" says nothing about what was established, and the outcome badge
  // beside it is where that lives.
  COMPLETED: 'neutral',
  INTERRUPTED: 'interrupted',
  CANCELLED: 'neutral',
}

const OUTCOME_TONE: Record<RunOutcome, Tone> = {
  NOT_EVALUATED: 'neutral',
  PASS: 'pass',
  FAIL: 'fail',
  INCONCLUSIVE: 'inconclusive',
}

const MARK: Record<Tone, string> = {
  neutral: '–',
  progress: '↻',
  pass: '✓',
  fail: '✕',
  inconclusive: '?',
  interrupted: '!',
}

export interface StatusBadgeProps {
  /** The word the server used. Rendered verbatim; this component never rephrases a server value. */
  readonly children: string
  readonly tone: Tone
  /** What kind of value this is, announced before it. Without this a reader hears two bare words
   * and has to infer which one was the status. */
  readonly kind: string
}

export const StatusBadge = ({ children, tone, kind }: StatusBadgeProps): JSX.Element => (
  <span className={`af-status af-status--${tone}`}>
    <span aria-hidden="true" className="af-status__mark">
      {MARK[tone]}
    </span>
    <span className="af-visually-hidden">{kind}: </span>
    {children}
  </span>
)

export const RunStatusBadge = ({ status }: { readonly status: RunStatus }): JSX.Element => (
  <StatusBadge tone={STATUS_TONE[status]} kind="Status">
    {status}
  </StatusBadge>
)

export const RunOutcomeBadge = ({ outcome }: { readonly outcome: RunOutcome }): JSX.Element => (
  <StatusBadge tone={OUTCOME_TONE[outcome]} kind="Outcome">
    {outcome}
  </StatusBadge>
)
