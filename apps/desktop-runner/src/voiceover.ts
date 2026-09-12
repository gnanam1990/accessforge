/** Attach the module-08 VoiceOver adapter to module 07's durable supervisor gate. */

import type {
  ActionContext,
  ActionRequest,
  AdapterDispatchResult,
  RawObservation,
  UnknownObservation,
} from '@accessforge/at-voiceover';

import type { ActionCommand } from './supervisor.js';

export interface VoiceOverRuntime {
  perform(request: ActionRequest, context: ActionContext): Promise<AdapterDispatchResult>;
}

export interface VoiceOverDispatchOptions {
  readonly utc: () => string;
  /**
   * Must retain the observation before resolving. If this rejects, the supervisor records the
   * action as ambiguous and fences the desktop because the OS action may already have happened.
   */
  readonly recordObservation: (
    observation: RawObservation | UnknownObservation,
  ) => Promise<void>;
}

/**
 * Construct the only callback by which module 07 may reach VoiceOver.
 *
 * The supervisor has already flushed ACTION_INTENT before invoking this function. We do not catch
 * adapter or evidence-sink failures: once the callback is entered, either one leaves uncertainty
 * about an OS action and module 07 must turn that into AMBIGUOUS rather than FAILED.
 */
export function createVoiceOverDispatch(
  adapter: VoiceOverRuntime,
  options: VoiceOverDispatchOptions,
): (command: ActionCommand) => Promise<'SUCCEEDED' | 'FAILED'> {
  return async (command) => {
    const request: ActionRequest = {
      action: command.action,
      ...(command.keyChord !== undefined ? { keyChord: command.keyChord } : {}),
      ...(command.text !== undefined ? { text: command.text } : {}),
    };
    const result = await adapter.perform(request, {
      actionId: command.actionId,
      actionSequence: command.sequence,
      capturedAtUtc: options.utc,
    });
    if (result.observation !== undefined) {
      await options.recordObservation(result.observation);
    }
    return result.status;
  };
}
