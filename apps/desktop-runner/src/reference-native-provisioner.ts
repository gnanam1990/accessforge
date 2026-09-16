/** Concrete reference-host assembly. Trusted operator inputs, never navigator configuration. */
import { lstatSync, mkdtempSync, realpathSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import type { NativeHostConfiguration } from './native-host.js';
import { parseReference } from './dispatch-receiver.js';
import { createArtifactProbe, type ArtifactProbeOptions } from './artifact-probe.js';
import { FileJournal } from './journal.js';

type Bootstrap = NativeHostConfiguration['bootstrap'];
type Navigator = NativeHostConfiguration['navigator'];

export interface ReferenceNativeProvisioningOptions {
  readonly privateDirectory: string;
  readonly maxActions: number;
  readonly maxWallTimeSeconds: number;
  readonly bootstrap: Omit<Bootstrap, 'clock' | 'journal' | 'lease' | 'physicalPreflight' |
    'referencePreparation' | 'navigatorBridgeDirectory'> & {
      readonly referencePreparation: NonNullable<Bootstrap['referencePreparation']>;
    };
  readonly physicalPreflight: Omit<Bootstrap['physicalPreflight'], 'observeRuntimeEvidence' |
    'artifactProbe'> & { readonly artifactProbe: ArtifactProbeOptions };
  readonly navigator: Omit<Navigator, 'reference' | 'deadlineMonotonic'>;
  /** Independent live observation. UNKNOWN stays UNKNOWN; this factory cannot manufacture
   * proof that a previous automation source is absent from a desktop claim or PID listing.
   */
  readonly observeStaleInputSource: (signal: AbortSignal) => Promise<boolean | undefined>;
}

/** Export the returned function as `provisionNativeHost` from a private operator .mjs module.
 * Construction is inert. Invocation allocates one private attempt directory, not a reader,
 * browser, model, session, lease or consent. All existing execution gates run afterward.
 */
export function createReferenceNativeProvisioner(options: ReferenceNativeProvisioningOptions):
  (signal: AbortSignal) => Promise<NativeHostConfiguration> {
  const reference = Object.freeze(parseReference(options.bootstrap.receiver.localReference));
  const directory = options.privateDirectory;
  const maxActions = options.maxActions, maxWallTimeSeconds = options.maxWallTimeSeconds;
  const observeStaleInputSource = options.observeStaleInputSource;
  if (!Number.isSafeInteger(maxActions) || maxActions < 1 || maxActions > 500 ||
      !Number.isSafeInteger(maxWallTimeSeconds) || maxWallTimeSeconds < 1 || maxWallTimeSeconds > 1800 ||
      typeof observeStaleInputSource !== 'function' ||
      typeof options.bootstrap.readerStartup.authorize !== 'function' ||
      typeof options.bootstrap.authorizePhysicalAction !== 'function' ||
      typeof options.bootstrap.recordObservation !== 'function' ||
      typeof options.bootstrap.referencePreparation?.authorize !== 'function') {
    throw new Error('explicit bounded native host authorities required');
  }
  const bootstrap = {
    ...options.bootstrap,
    receiver: Object.freeze({ ...options.bootstrap.receiver, localReference: reference }),
    safari: Object.freeze({ ...options.bootstrap.safari }),
    readerStartup: Object.freeze({ ...options.bootstrap.readerStartup }),
    referencePreparation: Object.freeze({ ...options.bootstrap.referencePreparation,
      fixture: Object.freeze({ ...options.bootstrap.referencePreparation.fixture }) }),
  };
  const physical = structuredClone(options.physicalPreflight);
  const navigator = structuredClone(options.navigator);
  createArtifactProbe(physical.artifactProbe); // Validate capability shape; no socket is opened.
  const fixture = bootstrap.referencePreparation.fixture;
  if (physical.artifactProbe.expectedBuildDigest !== fixture.expectedBuildDigest ||
      new URL(`/form/${encodeURIComponent(fixture.reservedNonce)}`, fixture.permittedOrigin).href !==
        bootstrap.safari.expectedUrl) throw new Error('native fixture and build bindings differ');
  let used = false;
  return async signal => {
    if (used) throw new Error('native provisioner cannot allocate a replacement attempt');
    used = true;
    signal.throwIfAborted();
    if (!isAbsolute(directory) || realpathSync(directory) !== resolve(directory)) {
      throw new Error('private native provisioning directory required');
    }
    const owner = lstatSync(directory);
    if (!owner.isDirectory() || owner.uid !== process.getuid?.() || (owner.mode & 0o077) !== 0) {
      throw new Error('private native provisioning directory required');
    }
    const attemptDirectory = mkdtempSync(join(directory, 'native-'));
    const clock = Object.freeze({ monotonic: () => performance.now(), utc: () => new Date().toISOString() });
    const deadlineMonotonic = clock.monotonic() + maxWallTimeSeconds * 1000;
    return {
      privateDirectory: attemptDirectory,
      bootstrap: {
        ...bootstrap,
        clock,
        journal: new FileJournal(join(attemptDirectory, 'actions.jsonl')),
        navigatorBridgeDirectory: attemptDirectory,
        lease: Object.freeze({ leaseId: reference.leaseId, epoch: reference.epoch,
          maxActions, maxWallTimeSeconds, deadlineMonotonic }),
        physicalPreflight: {
          ...physical,
          async observeRuntimeEvidence(probeSignal) {
            const lifetime = AbortSignal.any([signal, probeSignal]);
            lifetime.throwIfAborted();
            const detected = await observeStaleInputSource(lifetime);
            lifetime.throwIfAborted();
            if (detected !== undefined && typeof detected !== 'boolean') {
              throw new Error('stale input-source observation malformed');
            }
            // Build measurement, reset/origin, speech and journal evidence are supplied by
            // the concrete bootstrap components, not constants in this provisioner.
            return detected === undefined ? {} : { staleInputSourceDetected: detected };
          },
        },
      },
      navigator: { ...navigator, reference, deadlineMonotonic },
    };
    // Keep the private directory and any later journal after failure for reconciliation.
  };
}
