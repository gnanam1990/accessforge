/**
 * The narrow runtime boundary around Guidepup's real VoiceOver implementation.
 *
 * The adapter depends on this deliberately small port instead of the whole Guidepup object. Tests
 * can prove every mapping without pretending the fake is a reader, while the production factory at
 * the bottom imports the pinned package and exposes only the methods AccessForge permits.
 */

import { createRequire } from 'node:module';

import { type ActionRequest, assertActionPermitted } from './actions.js';
import {
  type RawObservation,
  type UnknownObservation,
  captureTimedOut,
} from './projection.js';

export interface VoiceOverClient {
  readonly name: string;
  readonly version: string;
  detect(): boolean;
  start(): Promise<void>;
  stop(): Promise<void>;
  next(): Promise<void>;
  previous(): Promise<void>;
  act(): Promise<void>;
  type(text: string): Promise<void>;
  press(key: string): Promise<void>;
  itemText(): Promise<string>;
  lastSpokenPhrase(): Promise<string>;
}

export const GUIDEPUP_CHORDS = {
  TAB: 'Tab',
  'SHIFT+TAB': 'Shift+Tab',
  ENTER: 'Enter',
  SPACE: 'Space',
  ESCAPE: 'Escape',
  'CTRL+OPT+RIGHT': 'Control+Alt+ArrowRight',
  'CTRL+OPT+LEFT': 'Control+Alt+ArrowLeft',
} as const;

export interface ActionContext {
  readonly actionId: string;
  readonly actionSequence: number;
  readonly capturedAtUtc: () => string;
}

export interface AdapterDispatchResult {
  readonly status: 'SUCCEEDED';
  readonly observation?: RawObservation | UnknownObservation;
}

export interface VoiceOverAdapterOptions {
  readonly idlePollMs?: number;
  readonly idleTimeoutMs?: number;
  readonly idleStableSamples?: number;
  readonly monotonicNow?: () => number;
  readonly sleep?: (ms: number) => Promise<void>;
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

/**
 * Executes already-admitted actions against a real VoiceOver client.
 *
 * It does not decide that a run is authorized or that the profile is verified. The desktop
 * supervisor owns the former and the immutable profile gate owns the latter. This class is also
 * used by the explicit capability-proof command that bootstraps the first trace; that command
 * labels its output CANDIDATE_PROOF and cannot submit it as verified evidence.
 */
export class VoiceOverAdapter {
  private readonly idlePollMs: number;
  private readonly idleTimeoutMs: number;
  private readonly idleStableSamples: number;
  private readonly monotonicNow: () => number;
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(
    private readonly client: VoiceOverClient,
    options: VoiceOverAdapterOptions = {},
  ) {
    this.idlePollMs = options.idlePollMs ?? 100;
    this.idleTimeoutMs = options.idleTimeoutMs ?? 2_000;
    this.idleStableSamples = options.idleStableSamples ?? 2;
    this.monotonicNow = options.monotonicNow ?? (() => performance.now());
    this.sleep = options.sleep ?? defaultSleep;
  }

  async start(): Promise<void> {
    if (!this.client.detect()) {
      throw new Error('Guidepup reports that VoiceOver is not supported on this operating system');
    }
    await this.client.start();
  }

  async stop(): Promise<void> {
    await this.client.stop();
  }

  profile(): { readonly name: string; readonly version: string } {
    return { name: this.client.name, version: this.client.version };
  }

  async perform(request: ActionRequest, context: ActionContext): Promise<AdapterDispatchResult> {
    assertActionPermitted(request);

    if (request.action === 'STOP') {
      await this.stop();
      return { status: 'SUCCEEDED' };
    }

    if (request.action === 'WAIT_FOR_READER_IDLE') {
      return {
        status: 'SUCCEEDED',
        observation: await this.waitForIdle(context),
      };
    }

    let phrase: string;
    switch (request.action) {
      case 'NEXT':
        await this.client.next();
        phrase = await this.client.lastSpokenPhrase();
        break;
      case 'PREVIOUS':
        await this.client.previous();
        phrase = await this.client.lastSpokenPhrase();
        break;
      case 'ACTIVATE':
        await this.client.act();
        phrase = await this.client.lastSpokenPhrase();
        break;
      case 'TYPE_TEXT':
        // assertActionPermitted established that text exists. Keep the narrowing local rather than
        // using a non-null assertion at the call that can reach the keyboard.
        if (request.text === undefined) throw new Error('TYPE_TEXT passed validation without text');
        await this.client.type(request.text);
        phrase = await this.client.lastSpokenPhrase();
        break;
      case 'KEY_CHORD': {
        if (request.keyChord === undefined) {
          throw new Error('KEY_CHORD passed validation without a chord');
        }
        const key = GUIDEPUP_CHORDS[request.keyChord as keyof typeof GUIDEPUP_CHORDS];
        if (key === undefined) throw new Error('allowlisted chord has no Guidepup mapping');
        await this.client.press(key);
        phrase = await this.client.lastSpokenPhrase();
        break;
      }
      case 'READ_CURRENT':
        phrase = await this.client.itemText();
        break;
      default:
        throw new Error(`validated action ${request.action} has no VoiceOver implementation`);
    }

    return { status: 'SUCCEEDED', observation: this.observation(phrase, context) };
  }

  private observation(phrase: string, context: ActionContext): RawObservation {
    return {
      phrase,
      capturedAtUtc: context.capturedAtUtc(),
      actionId: context.actionId,
      actionSequence: context.actionSequence,
    };
  }

  private async waitForIdle(
    context: ActionContext,
  ): Promise<RawObservation | UnknownObservation> {
    const started = this.monotonicNow();
    const baseline = await this.client.lastSpokenPhrase();
    let last: string | undefined;
    let stable = 0;
    let transitioned = false;

    while (this.monotonicNow() - started < this.idleTimeoutMs) {
      const phrase = await this.client.lastSpokenPhrase();
      if (!transitioned) {
        if (phrase === baseline) {
          await this.sleep(this.idlePollMs);
          continue;
        }
        transitioned = true;
      }
      if (phrase === last) {
        stable += 1;
      } else {
        last = phrase;
        stable = 1;
      }
      if (stable >= this.idleStableSamples) {
        return this.observation(phrase, context);
      }
      await this.sleep(this.idlePollMs);
    }

    return captureTimedOut(this.idleTimeoutMs);
  }
}

/** The only production construction of the Guidepup boundary. */
export function createGuidepupVoiceOverAdapter(
  options: VoiceOverAdapterOptions = {},
): VoiceOverAdapter {
  // Guidepup constructs its platform reader while the module is imported. Loading it eagerly would
  // make even policy and projection tests crash on Linux CI before they can inject a fake client.
  // Keep the host probe at the production construction boundary, where an unsupported host should
  // fail, instead of at package import time, where every platform-independent consumer would fail.
  const { voiceOver } = createRequire(import.meta.url)('@guidepup/guidepup') as {
    readonly voiceOver: VoiceOverClient;
  };
  return new VoiceOverAdapter(voiceOver, options);
}
