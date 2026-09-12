/** Supervisor-only setup for the real local reference application and sealed browser target. */

import type { RuntimeProbeEvidence } from '@accessforge/at-voiceover';

export type ReferenceVariant = 'accessible' | 'inaccessible';

interface FetchResponse {
  readonly ok: boolean;
  readonly status: number;
  json(): Promise<unknown>;
}

export type SetupFetch = (
  url: string,
  init: { readonly method: 'POST'; readonly headers: Readonly<Record<string, string>> },
) => Promise<FetchResponse>;

export interface BrowserLaunchResult {
  /** URL independently observed after launch, not merely the requested URL echoed by the caller. */
  readonly observedUrl: string;
  readonly browserVersion: string;
}

export type BrowserLauncher = (sealedUrl: string) => Promise<BrowserLaunchResult>;

export interface ReferenceAppSetupOptions {
  readonly permittedOrigin: string;
  /** Setup identity is held only here and never appears in the returned navigator projection. */
  readonly setupToken: string;
  readonly variant: ReferenceVariant;
  readonly expectedBuildDigest: string;
  readonly observedBuildDigest: string;
  readonly fetch?: SetupFetch;
  readonly launch: BrowserLauncher;
}

export interface ReferenceAppSetupResult {
  readonly startUrl: string;
  readonly fixture: {
    readonly nonce: string;
    readonly variant: ReferenceVariant;
    readonly templateDigest: string;
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

function readFixture(body: unknown, expectedVariant: ReferenceVariant): ReferenceAppSetupResult['fixture'] {
  if (typeof body !== 'object' || body === null) throw new Error('fixture response is not an object');
  const value = body as Record<string, unknown>;
  if (
    typeof value.nonce !== 'string' ||
    value.nonce === '' ||
    value.variant !== expectedVariant ||
    typeof value.template_digest !== 'string' ||
    !/^[a-f0-9]{64}$/.test(value.template_digest)
  ) {
    throw new Error('fixture response does not carry the expected nonce, variant and SHA-256 digest');
  }
  return {
    nonce: value.nonce,
    variant: expectedVariant,
    templateDigest: value.template_digest,
  };
}

export async function prepareReferenceApp(
  options: ReferenceAppSetupOptions,
): Promise<ReferenceAppSetupResult> {
  const origin = assertLoopbackOrigin(options.permittedOrigin);
  if (options.setupToken.trim() === '') throw new Error('reference setup token is empty');
  const doFetch: SetupFetch = options.fetch ?? (globalThis.fetch as SetupFetch);
  const headers = { 'x-setup-token': options.setupToken };

  const reset = await doFetch(new URL('/api/_test/reset', origin).href, {
    method: 'POST',
    headers,
  });
  if (!reset.ok || reset.status !== 204) {
    throw new Error(`reference reset returned HTTP ${reset.status}; browser launch is refused`);
  }

  const fixtureResponse = await doFetch(
    new URL(`/api/_test/fixtures?variant=${options.variant}`, origin).href,
    { method: 'POST', headers },
  );
  if (!fixtureResponse.ok || fixtureResponse.status !== 201) {
    throw new Error(`fixture creation returned HTTP ${fixtureResponse.status}; browser launch is refused`);
  }
  const fixture = readFixture(await fixtureResponse.json(), options.variant);
  const startUrl = new URL(`/form/${encodeURIComponent(fixture.nonce)}`, origin).href;
  const launched = await options.launch(startUrl);
  let observedOrigin: string | undefined;
  try {
    observedOrigin = new URL(launched.observedUrl).origin;
  } catch {
    // Preserved as undefined evidence below. A setup launcher that cannot say where the browser
    // landed has not proved the sealed origin.
  }

  return {
    startUrl,
    fixture,
    browserVersion: launched.browserVersion,
    evidence: {
      permittedOrigin: options.permittedOrigin,
      ...(observedOrigin !== undefined ? { observedOrigin } : {}),
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
