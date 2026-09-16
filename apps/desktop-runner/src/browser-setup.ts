/** Supervisor-only reconciliation of an already confirmed, controller-reserved fixture. */

import type { RuntimeProbeEvidence } from '@accessforge/at-voiceover';
import { REFERENCE_FIXTURE_DIGEST, REFERENCE_FIXTURE_VERSION } from '@accessforge/contracts';

export type ReferenceVariant = 'accessible' | 'inaccessible' | 'missing-label-v1' | 'broken-focus-v1' | 'keyboard-trap-v1';

interface FetchResponse {
  readonly ok: boolean;
  readonly status: number;
  json(): Promise<unknown>;
}

export type SetupFetch = (
  url: string,
  init: {
    readonly method: 'POST';
    readonly headers: Readonly<Record<string, string>>;
    readonly redirect: 'error';
    readonly signal?: AbortSignal;
  },
) => Promise<FetchResponse>;

export interface BrowserLaunchResult {
  /** URL independently observed after launch, not merely the requested URL echoed by the caller. */
  readonly observedUrl: string;
  readonly browserVersion: string;
}

export type BrowserLauncher = (sealedUrl: string) => Promise<BrowserLaunchResult>;

export interface ReferenceAppSetupOptions {
  readonly permittedOrigin: string;
  /** Exact nonce from the trusted queued-run controller; never generated here or model supplied. */
  readonly reservedNonce: string;
  /** Setup identity is held only here and never appears in the returned navigator projection. */
  readonly setupToken: string;
  readonly variant: ReferenceVariant;
  readonly expectedBuildDigest: string;
  readonly observedBuildDigest: string;
  /**
   * Frozen template definition selected by the trusted controller, never by the candidate.
   * Not the journey/manifest fixtureDigest, which also covers values and observer configuration.
   */
  readonly expectedFixtureDigest: string;
  readonly fetch?: SetupFetch;
  readonly signal?: AbortSignal;
  readonly launch: BrowserLauncher;
}

export interface ReferenceAppSetupResult {
  readonly startUrl: string;
  readonly fixture: {
    readonly nonce: string;
    readonly variant: ReferenceVariant;
    readonly templateDigest: string;
    readonly templateVersion: string;
  };
  readonly browserVersion: string;
  readonly evidence: RuntimeProbeEvidence;
}

function assertLoopbackOrigin(raw: string): URL {
  const url = new URL(raw);
  const loopback = new Set(['127.0.0.1', 'localhost', '[::1]']);
  if (url.protocol !== 'http:' || !loopback.has(url.hostname) || url.origin !== raw) {
    throw new Error(
      `${raw} is not an exact loopback HTTP origin; the reference fixture must never be exposed ` +
        'or reached through an unsealed path',
    );
  }
  return url;
}

const boundedLocalFetch: SetupFetch = async (url, init) => {
  const timeout = AbortSignal.timeout(5_000);
  const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout;
  const response = await globalThis.fetch(url, { ...init, signal });
  const reader = response.body?.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  if (reader) {
    try {
      for (;;) {
        const next = await reader.read();
        if (next.done) break;
        total += next.value.length;
        if (total > 16_384) throw new Error('reference setup response exceeds 16 KiB');
        chunks.push(next.value);
      }
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
  }
  const payload = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    payload.set(chunk, offset);
    offset += chunk.length;
  }
  const text = new TextDecoder('utf-8', { fatal: true }).decode(payload);
  return { ok: response.ok, status: response.status, json: async () => JSON.parse(text) as unknown };
};

function readFixture(
  body: unknown, expectedVariant: ReferenceVariant, expectedDigest: string, expectedNonce: string,
): ReferenceAppSetupResult['fixture'] {
  if (typeof body !== 'object' || body === null) throw new Error('fixture response is not an object');
  const value = body as Record<string, unknown>;
  if (
    typeof value.nonce !== 'string' ||
    !/^[A-Za-z0-9_-]{16,64}$/.test(value.nonce) ||
    value.nonce !== expectedNonce ||
    value.variant !== expectedVariant ||
    typeof value.template_digest !== 'string' ||
    value.template_digest !== expectedDigest ||
    value.template_version !== REFERENCE_FIXTURE_VERSION
  ) {
    throw new Error('fixture response does not match the frozen nonce, variant, version and digest contract');
  }
  return {
    nonce: value.nonce,
    variant: expectedVariant,
    templateDigest: value.template_digest,
    templateVersion: value.template_version,
  };
}

export async function prepareReferenceApp(
  options: ReferenceAppSetupOptions,
): Promise<ReferenceAppSetupResult> {
  const origin = assertLoopbackOrigin(options.permittedOrigin);
  if (typeof options.reservedNonce !== 'string' || !/^[A-Za-z0-9_-]{16,64}$/.test(options.reservedNonce)) {
    throw new Error('controller-reserved fixture nonce required before browser setup');
  }
  if (options.setupToken.trim() === '') throw new Error('reference setup token is empty');
  if (options.expectedFixtureDigest !== REFERENCE_FIXTURE_DIGEST) {
    throw new Error('the sealed fixture definition is not the supported frozen reference contract');
  }
  if (typeof options.expectedBuildDigest !== 'string' ||
      !/^[a-f0-9]{64}$/.test(options.expectedBuildDigest) ||
      options.observedBuildDigest !== options.expectedBuildDigest) {
    throw new Error('the independently observed build must match before fixture reconciliation');
  }
  const doFetch: SetupFetch = options.fetch ?? boundedLocalFetch;
  options.signal?.throwIfAborted();
  const headers = { 'x-setup-token': options.setupToken };

  // Reconcile the existing empty fixture only. Never globally reset the application or select
  // a new nonce. A 201 means the previously confirmed app fixture disappeared: fence launch.
  const endpoint = new URL('/api/_test/fixtures', origin);
  endpoint.searchParams.set('variant', options.variant);
  endpoint.searchParams.set('nonce', options.reservedNonce);
  const fixtureResponse = await doFetch(
    endpoint.href,
    { method: 'POST', headers, redirect: 'error', ...(options.signal ? { signal: options.signal } : {}) },
  );
  options.signal?.throwIfAborted();
  if (!fixtureResponse.ok || fixtureResponse.status !== 200) {
    throw new Error(`fixture reconciliation returned HTTP ${fixtureResponse.status}; browser launch is refused`);
  }
  const fixture = readFixture(await fixtureResponse.json(), options.variant, options.expectedFixtureDigest, options.reservedNonce);
  options.signal?.throwIfAborted();
  const startUrl = new URL(`/form/${encodeURIComponent(fixture.nonce)}`, origin).href;
  const launched = await options.launch(startUrl);
  options.signal?.throwIfAborted();
  if (launched.observedUrl !== startUrl) {
    throw new Error('browser did not independently observe the exact reserved fixture URL; launch is unconfirmed');
  }

  return {
    startUrl,
    fixture,
    browserVersion: launched.browserVersion,
    evidence: {
      permittedOrigin: options.permittedOrigin,
      observedOrigin: origin.origin,
      originReachable: true,
      environmentResetSucceeded: true,
      expectedBuildDigest: options.expectedBuildDigest,
      observedBuildDigest: options.observedBuildDigest,
    },
  };
}

/** The navigator gets a destination and nothing from the setup or observer identities. */
export function projectSetupForNavigator(
  setup: ReferenceAppSetupResult,
): { readonly startUrl: string } {
  return { startUrl: setup.startUrl };
}
