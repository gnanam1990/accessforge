/** Narrow NVDA driver. Host/session ownership and durable action admission belong to the runner. */
import { createRequire } from 'node:module';
import { assertActionPermitted, assertRealReaderProven, type ActionRequest } from './index.js';

export interface NvdaClient {
  detect(): boolean;
  start(): Promise<void>;
  stop(): Promise<void>;
  next(): Promise<void>;
  previous(): Promise<void>;
  act(): Promise<void>;
  type(text: string): Promise<void>;
  press(key: string): Promise<void>;
  reportCurrentFocus(): Promise<void>;
  sayAll(): Promise<void>;
  spokenPhraseLog(): Promise<string[]>;
}

export type NvdaCapture = { readonly status: 'UNKNOWN'; readonly reason: string } | {
  readonly status: 'LOG_DELTA';
  readonly phrase: string;
  readonly meaning: 'SDK_LOG_DELTA_NOT_ACOUSTIC_ATTESTATION';
};

export interface NvdaContext {
  readonly signal: AbortSignal;
  /** Fresh trusted focus/session/permission/lease check, not a model-provided callback. */
  readonly assertReady: () => Promise<void>;
}

const chords: Readonly<Record<string, string>> = Object.freeze({
  TAB: 'Tab', 'SHIFT+TAB': 'Shift+Tab', ENTER: 'Enter', SPACE: 'Space',
  ESCAPE: 'Escape', DOWN: 'ArrowDown', UP: 'ArrowUp',
});
const unknown = (): NvdaCapture => ({ status: 'UNKNOWN',
  reason: 'No distinguishable nonempty speech log delta; no silence or announcement is established.' });

/**
 * Trusted embedding port, also usable with labelled test clients. Returned capture is NOT canonical
 * reader evidence. This class does not supply Windows desktop probes, grant consent, enroll a
 * profile or replace the supervisor journal. Every SDK input requires the supplied fresh guard.
 */
export class NvdaAdapter {
  private state: 'NEW' | 'ACTIVE' | 'UNCERTAIN' | 'CLOSED' = 'NEW';
  private busy = false;
  private fenced = false;
  private startupDispatched = false;
  private readonly text: ReadonlySet<string>;

  constructor(private readonly client: NvdaClient, approvedText: readonly string[]) {
    if (approvedText.some(value => typeof value !== 'string' || value.length > 4096)) {
      throw new Error('bounded synthetic fixture values required');
    }
    this.text = new Set(approvedText);
  }

  private async guard(context: NvdaContext): Promise<void> {
    context.signal.throwIfAborted();
    if (this.fenced) throw new Error('NVDA operation fenced');
    await context.assertReady();
    context.signal.throwIfAborted();
    if (this.fenced) throw new Error('NVDA operation fenced');
  }

  private enter(): void {
    if (this.busy || this.fenced) {
      this.fenced = true;
      throw new Error('overlapping or uncertain NVDA operation; reconcile before reuse');
    }
    this.busy = true;
  }

  async start(context: NvdaContext): Promise<void> {
    if (this.state !== 'NEW') throw new Error('NVDA startup is one-shot');
    this.enter();
    this.state = 'UNCERTAIN';
    try {
      await this.guard(context);
      if (!this.client.detect()) throw new Error('NVDA is unavailable on this host');
      await this.guard(context);
      this.startupDispatched = true;
      await this.client.start();
      await this.guard(context);
      this.state = 'ACTIVE';
    } finally { this.busy = false; }
  }

  /** Separate cleanup: no new consent grant, and never race STOP with unresolved SDK input. */
  async stop(): Promise<void> {
    if (this.busy || !this.startupDispatched || this.state === 'CLOSED') {
      throw new Error('NVDA cleanup unavailable or already attempted');
    }
    this.busy = true;
    this.state = 'CLOSED'; // A rejected stop is uncertain, never automatically replayed.
    try { await this.client.stop(); } finally { this.busy = false; }
  }

  private async log(): Promise<readonly string[] | undefined> {
    try {
      const value: unknown = await this.client.spokenPhraseLog();
      if (!Array.isArray(value) || value.length > 10000 ||
          value.some(item => typeof item !== 'string' || item.length > 16384)) return undefined;
      return [...value] as string[];
    } catch { return undefined; }
  }

  async perform(input: ActionRequest, context: NvdaContext): Promise<NvdaCapture | undefined> {
    const request = structuredClone(input);
    assertActionPermitted(request);
    if (request.action === 'TYPE_TEXT' && !this.text.has(request.text!)) {
      throw new Error('NVDA typing requires an approved synthetic fixture value');
    }
    if (this.state !== 'ACTIVE') throw new Error('NVDA is not active');
    if (request.action === 'STOP') {
      this.enter();
      try {
        await this.guard(context);
        this.state = 'CLOSED';
        await this.client.stop();
        return undefined;
      } catch (error) { this.fenced = true; throw error; }
      finally { this.busy = false; }
    }
    this.enter();
    try {
      await this.guard(context);
      const before = await this.log();
      await this.guard(context);
      switch (request.action) {
        case 'NEXT': await this.client.next(); break;
        case 'PREVIOUS': await this.client.previous(); break;
        case 'ACTIVATE': await this.client.act(); break;
        case 'TYPE_TEXT': await this.client.type(request.text!); break;
        case 'READ_CURRENT': await this.client.reportCurrentFocus(); break;
        case 'KEY_CHORD':
          if (request.keyChord === 'NVDA+DOWN') await this.client.sayAll();
          else await this.client.press(chords[request.keyChord!]!);
          break;
        case 'WAIT_FOR_READER_IDLE':
          // A cached log is not an acoustic idle sensor. No fabricated successful idle observation.
          return { status: 'UNKNOWN', reason: 'NVDA acoustic idle measurement is unavailable.' };
        default: throw new Error('NVDA command unavailable');
      }
      await this.guard(context);
      const after = await this.log();
      await this.guard(context);
      if (before === undefined || after === undefined || after.length <= before.length ||
          before.some((item, index) => after[index] !== item)) return unknown();
      const phrase = after.at(-1);
      if (!phrase?.trim() || phrase === before.at(-1)) return unknown();
      return { status: 'LOG_DELTA', phrase, meaning: 'SDK_LOG_DELTA_NOT_ACOUSTIC_ATTESTATION' };
    } catch (error) {
      this.state = 'UNCERTAIN';
      this.fenced = true;
      throw error;
    } finally { this.busy = false; }
  }
}

/** Real factory stays blocked until a Windows qualification has enrolled the exact matrix. */
export function createGuidepupNvdaAdapter(approvedText: readonly string[]): NvdaAdapter {
  assertRealReaderProven(); // Before importing the SDK (which constructs platform objects).
  if (process.platform !== 'win32') throw new Error('interactive Windows host required');
  const { nvda } = createRequire(import.meta.url)('@guidepup/guidepup') as {
    nvda: Omit<NvdaClient, 'reportCurrentFocus' | 'sayAll'> & {
      keyboardCommands: { reportCurrentFocus: unknown; sayAll: unknown };
      perform(command: unknown): Promise<void>;
    };
  };
  // The pinned SDK commands use INSERT as the NVDA modifier. No CAPSLOCK support is claimed.
  return new NvdaAdapter({
    detect: () => nvda.detect(), start: () => nvda.start(), stop: () => nvda.stop(),
    next: () => nvda.next(), previous: () => nvda.previous(), act: () => nvda.act(),
    type: text => nvda.type(text), press: key => nvda.press(key),
    reportCurrentFocus: () => nvda.perform(nvda.keyboardCommands.reportCurrentFocus),
    sayAll: () => nvda.perform(nvda.keyboardCommands.sayAll),
    spokenPhraseLog: () => nvda.spokenPhraseLog(),
  }, approvedText);
}
