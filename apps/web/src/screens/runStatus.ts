/**
 * The exact copy UI-UX section 5 specifies for each situation, in one place.
 *
 * In one place because the failure this guards against is two screens describing the same run
 * differently — and because the copy *is* the specification here. Each sentence was chosen to stop
 * a particular over-reading, and paraphrasing one on a screen would undo the choice.
 *
 * The function takes only server-owned fields. There is no argument it could be given that would
 * let it derive a verdict: `PASS` appears in exactly one branch, and that branch is reached only
 * when the server has already said `COMPLETED` and `PASS`.
 */

import type { Run } from '../api/resources'

export type Situation =
  | 'QUEUED'
  | 'RUNNING'
  | 'FINALIZING'
  | 'PASSED'
  | 'FAILED'
  | 'INCONCLUSIVE'
  | 'INTERRUPTED'
  | 'CANCELLATION_REQUESTED'
  | 'CANCELLED_STOP_ACKNOWLEDGED'
  | 'CANCELLED_BEFORE_DISPATCH'
  | 'ENDED_WITHOUT_A_RECOGNISED_OUTCOME'

export interface SituationCopy {
  readonly situation: Situation
  readonly headline: string
  readonly detail: string
  readonly tone: 'neutral' | 'progress' | 'pass' | 'fail' | 'inconclusive' | 'interrupted'
}

/**
 * Which situation a run is in, from the server's fields alone.
 *
 * Cancellation is checked before the terminal outcomes on purpose. A run whose cancellation was
 * requested and never acknowledged is not described by its status: the status says what the server
 * last recorded, and the thing a person needs to know is that nothing has established the desktop
 * stopped.
 */
export const situationFor = (run: Run): SituationCopy => {
  if (run.cancellationRequestedAt !== null && run.stopAcknowledgedAt === null) {
    return {
      situation: 'CANCELLATION_REQUESTED',
      headline: 'Cancellation requested; waiting for runner acknowledgement',
      detail:
        'The request is recorded. Nothing has established that the desktop stopped, so any action ' +
        'already dispatched may still be running and its effects may still occur.',
      tone: 'interrupted',
    }
  }

  if (run.status === 'CANCELLED') {
    return run.stopAcknowledgedAt !== null
      ? {
          situation: 'CANCELLED_STOP_ACKNOWLEDGED',
          headline: 'Runner stop acknowledged',
          detail:
            'The runner confirmed it stopped. Test effects performed before that point were still ' +
            'performed; this does not undo them.',
          tone: 'neutral',
        }
      : {
          situation: 'CANCELLED_BEFORE_DISPATCH',
          headline: 'Cancelled before dispatch',
          detail: 'Nothing was sent to a desktop, so no test effect was performed.',
          tone: 'neutral',
        }
  }

  if (run.status === 'INTERRUPTED') {
    return {
      situation: 'INTERRUPTED',
      headline: 'Execution interrupted; retry starts a new run',
      detail:
        'This run is terminal and cannot be resumed. A retry is a new run with fresh fixtures and ' +
        'its own authorization. Any ambiguous effect is listed separately — it may have happened.',
      tone: 'interrupted',
    }
  }

  if (run.status === 'QUEUED') {
    return {
      situation: 'QUEUED',
      headline: 'Waiting for an eligible runner',
      detail:
        'Nothing has run. A runner becomes eligible by passing a preflight for the reader this ' +
        "journey requires; until one does, this run stays here.",
      tone: 'neutral',
    }
  }

  if (run.status === 'RUNNING') {
    return {
      situation: 'RUNNING',
      headline: 'Running',
      detail:
        'A desktop is executing admitted actions within the frozen budget. No outcome exists yet, ' +
        'and none will until the evidence is validated.',
      tone: 'progress',
    }
  }

  if (run.status === 'FINALIZING') {
    return {
      situation: 'FINALIZING',
      headline: 'Validating evidence',
      detail:
        'Execution has stopped and the evidence is being checked. Upload progress is not ' +
        'evaluation: a complete upload does not mean the evidence supports anything.',
      tone: 'progress',
    }
  }

  if (run.status === 'COMPLETED') {
    switch (run.outcome) {
      case 'PASS':
        return {
          situation: 'PASSED',
          headline: 'This journey passed on the listed build and profile',
          detail:
            'The frozen assertions held for this one execution, on one pinned reader, browser, ' +
            'locale and keyboard layout, against one build of one environment. Everything outside ' +
            'that is untested.',
          tone: 'pass',
        }
      case 'FAIL':
        return {
          situation: 'FAILED',
          headline: 'A required assertion was false',
          detail:
            'The evidence was valid and a frozen assertion did not hold. This is a result about ' +
            'this execution, established from evidence rather than from the agent’s own report.',
          tone: 'fail',
        }
      case 'INCONCLUSIVE':
        return {
          situation: 'INCONCLUSIVE',
          headline: 'Nothing was established',
          detail:
            'Evidence was missing, invalid or unknown, so no assertion could be decided. This is ' +
            'not a pass and it is not a defect: it is a run that proved nothing, and the reasons ' +
            'below say which part was unusable.',
          tone: 'inconclusive',
        }
      default:
        break
    }
  }

  // `COMPLETED` with `NOT_EVALUATED`, or any status and outcome pair this build does not recognise.
  // Reported as unrecognised rather than mapped to the nearest familiar one: a new server state
  // rendered as an old one is a state nobody notices arriving.
  return {
    situation: 'ENDED_WITHOUT_A_RECOGNISED_OUTCOME',
    headline: 'This run has no outcome this interface recognises',
    detail:
      `The server reports status ${run.status} with outcome ${run.outcome}. That pair is not one ` +
      'this build knows how to describe, so it is shown as it was reported rather than translated ' +
      'into something familiar.',
    tone: 'neutral',
  }
}
