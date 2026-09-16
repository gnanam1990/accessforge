/** Local read-only declarations for operator review, never enrollment or preflight evidence. */
import { observeRunnerProfile, type ProbeEnvironment } from '@accessforge/at-voiceover';
import { digest } from '@accessforge/contracts';

export function observeEnrollment(environment: ProbeEnvironment) {
  if (environment.platform?.() !== 'darwin') throw new Error('macOS console required');
  const deviceId = environment.deviceIdentifier?.();
  const sessionId = environment.auditSessionId();
  const processSessionId = environment.processAuditSessionId?.();
  if (deviceId === undefined || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(deviceId) ||
      deviceId === '00000000-0000-0000-0000-000000000000' ||
      sessionId === undefined || !/^[1-9][0-9]*$/.test(sessionId) || Number(sessionId) >= 4294967295 ||
      processSessionId !== sessionId || environment.screenLocked() !== false) throw new Error('assigned console identity unavailable');
  const profile = observeRunnerProfile(environment);
  if (profile === undefined) throw new Error('active reader/browser profile unavailable');
  // Do not publish a mixed identity if the console changed during slower version/input-source reads.
  if (environment.deviceIdentifier?.() !== deviceId || environment.auditSessionId() !== sessionId ||
      environment.processAuditSessionId?.() !== processSessionId || environment.screenLocked() !== false ||
      digest(observeRunnerProfile(environment)) !== digest(profile)) throw new Error('desktop identity changed during observation');
  return {
    meaning: 'LOCAL_DECLARATION_NOT_ENROLLMENT_OR_QUALIFICATION',
    session: { deviceId, platform: 'darwin', interactiveSessionId: sessionId, console: true },
    profile,
    profileDigest: digest(profile),
  };
}
