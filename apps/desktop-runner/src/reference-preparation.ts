/** One-shot reference preparation inside the runner's already-owned startup lifetime. */
import type { RuntimeProbeEvidence } from '@accessforge/at-voiceover';
import { createArtifactProbe, type ArtifactProbeOptions } from './artifact-probe.js';
import type { ReferenceAppSetupOptions } from './browser-setup.js';
import { prepareSafariReferenceApp } from './safari-launcher.js';
import type { SafariOriginOptions } from './safari-origin.js';

export interface ReferencePreparationOptions {
  readonly fixture: Omit<ReferenceAppSetupOptions, 'launch' | 'fetch' | 'observedBuildDigest'>;
  /** Separate approval for fixture reconciliation and opening Safari, not reader consent. */
  readonly authorize: (signal: AbortSignal) => Promise<void>;
}

/** Test/embedding ports only; physical-preflight never accepts these in operator configuration. */
interface Ports {
  readonly measure?: ReturnType<typeof createArtifactProbe>;
  readonly prepare?: typeof prepareSafariReferenceApp;
}

export function createReferencePreparation(options: ReferencePreparationOptions,
  safari: SafariOriginOptions, artifact: ArtifactProbeOptions | undefined, ports: Ports = {}) {
  const fixture = Object.freeze({ ...options.fixture });
  const target = Object.freeze({ ...safari });
  const authorize = options.authorize;
  if (artifact === undefined || artifact.expectedBuildDigest !== fixture.expectedBuildDigest ||
      new URL(`/form/${encodeURIComponent(fixture.reservedNonce)}`, fixture.permittedOrigin).href !== target.expectedUrl ||
      typeof authorize !== 'function') throw new Error('reference preparation must match the sealed native target and live build probe');
  const measure = ports.measure ?? createArtifactProbe(artifact);
  const prepare = ports.prepare ?? prepareSafariReferenceApp;
  let used = false;
  let fenced = false;
  return async (assertHeld: () => void, signal: AbortSignal): Promise<RuntimeProbeEvidence> => {
    if (used) { fenced = true; throw new Error('reference preparation cannot be replayed'); }
    used = true;
    const guard = () => {
      if (signal.aborted || fenced) throw new Error('reference preparation cancelled');
      assertHeld();
      if (signal.aborted || fenced) throw new Error('reference preparation cancelled');
    };
    guard();
    await authorize(signal);
    guard();
    const measured = await measure(signal);
    guard();
    if (measured.expectedBuildDigest !== fixture.expectedBuildDigest ||
        measured.observedBuildDigest !== fixture.expectedBuildDigest) throw new Error('reference build measurement differs');
    // Measurement can outlive a grant. Reauthorize before the first reconciliation POST.
    await authorize(signal);
    guard();
    const result = await prepare({
      permittedOrigin: fixture.permittedOrigin, reservedNonce: fixture.reservedNonce,
      setupToken: fixture.setupToken, variant: fixture.variant,
      expectedBuildDigest: fixture.expectedBuildDigest, observedBuildDigest: measured.observedBuildDigest,
      expectedFixtureDigest: fixture.expectedFixtureDigest,
    }, { ...target, authorize, assertDesktopHeld: guard, signal });
    guard();
    if (result.startUrl !== target.expectedUrl || result.browserVersion !== target.expectedBrowserVersion) {
      throw new Error('reference preparation receipt differs');
    }
    return Object.freeze({ ...result.evidence });
  };
}
