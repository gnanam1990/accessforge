/** Trusted, inert assembly for the separately labelled first-profile qualification entrypoint. */
import { assertActionPermitted } from '@accessforge/at-voiceover';
import type { CandidateHostConfiguration } from './candidate-host.js';
import { createArtifactProbe, type ArtifactProbeOptions } from './artifact-probe.js';
import { parseReference } from './dispatch-receiver.js';
import type { ReferencePreparationOptions } from './reference-preparation.js';

export interface ReferenceCandidateProvisioningOptions extends Omit<CandidateHostConfiguration,
  'physicalPreflight' | 'referencePreparation'> {
  readonly referencePreparation: ReferencePreparationOptions;
  readonly physicalPreflight: Omit<CandidateHostConfiguration['physicalPreflight'],
    'observeRuntimeEvidence' | 'artifactProbe'> & { readonly artifactProbe: ArtifactProbeOptions };
  /** Must measure the independently assigned host, not infer absence from a lock or PID. */
  readonly observeStaleInputSource: (signal: AbortSignal) => Promise<boolean | undefined>;
}

/** Export this returned function as provisionCandidateProof in a private operator .mjs module.
 * No reader, browser, probe, model, journal or directory is started/created by this factory.
 * runCandidateHost still owns private output, the desktop claim and all actual execution gates.
 */
export function createReferenceCandidateProvisioner(options: ReferenceCandidateProvisioningOptions):
  (signal: AbortSignal) => Promise<CandidateHostConfiguration> {
  const desktop = { ...structuredClone(options.desktop), reference: parseReference(options.desktop.reference) };
  const physical = structuredClone(options.physicalPreflight);
  const safari = structuredClone(options.safari);
  const actions = structuredClone(options.actions);
  const approvedTextValues = structuredClone(options.approvedTextValues);
  const maxDurationSeconds = options.maxDurationSeconds, actionTimeoutMs = options.actionTimeoutMs;
  const authorizeStartup = options.authorizeStartup, authorizeAction = options.authorizeAction;
  const observeStaleInputSource = options.observeStaleInputSource;
  const authorizePreparation = options.referencePreparation?.authorize;
  const fixture = structuredClone(options.referencePreparation?.fixture);
  if (typeof authorizeStartup !== 'function' || typeof authorizeAction !== 'function' ||
      typeof authorizePreparation !== 'function' || typeof observeStaleInputSource !== 'function' ||
      !Number.isSafeInteger(maxDurationSeconds) || maxDurationSeconds < 1 || maxDurationSeconds > 1800 ||
      !Number.isSafeInteger(actionTimeoutMs) || actionTimeoutMs < 1 || actionTimeoutMs > 30000 ||
      !Array.isArray(actions) || actions.length < 1 || actions.length > 100 ||
      !Array.isArray(approvedTextValues) || approvedTextValues.some(value => typeof value !== 'string') ||
      !/^[1-9][0-9]*$/.test(desktop.desktopSessionId) || Number(desktop.desktopSessionId) >= 4294967295 ||
      desktop.desktopSessionId !== physical.expectedDesktopSessionId || !fixture) {
    throw new Error('explicit bounded candidate host authorities and assignment required');
  }
  for (const action of actions) {
    assertActionPermitted(action);
    if (action.action === 'TYPE_TEXT' && !approvedTextValues.includes(action.text!)) {
      throw new Error('candidate typing requires exact approved text');
    }
  }
  createArtifactProbe(physical.artifactProbe); // Shape validation only; no socket connection.
  if (fixture.expectedBuildDigest !== physical.artifactProbe.expectedBuildDigest ||
      new URL(`/form/${encodeURIComponent(fixture.reservedNonce)}`, fixture.permittedOrigin).href !== safari.expectedUrl) {
    throw new Error('candidate fixture, browser and live build bindings differ');
  }
  let used = false;
  return async signal => {
    if (used) throw new Error('candidate provisioner cannot allocate a replacement attempt');
    used = true;
    signal.throwIfAborted();
    return {
      desktop, safari, actions, approvedTextValues, maxDurationSeconds, actionTimeoutMs,
      authorizeStartup, authorizeAction,
      referencePreparation: { fixture, authorize: authorizePreparation },
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
          return detected === undefined ? {} : { staleInputSourceDetected: detected };
        },
      },
    };
  };
}
