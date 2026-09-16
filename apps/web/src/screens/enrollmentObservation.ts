export interface EnrollmentObservation {
  readonly session: Record<string, string | boolean>
  readonly profile: Record<string, string>
  readonly profileDigest: string
}
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
const exact = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key))

export async function parseEnrollmentObservation(raw: string): Promise<EnrollmentObservation> {
  if (raw.length > 8192) throw new Error('Observation must be at most 8,192 characters.')
  let value: unknown
  try { value = JSON.parse(raw) } catch { throw new Error('Paste valid enrollment observation JSON.') }
  if (!object(value) || !exact(value, ['meaning', 'session', 'profile', 'profileDigest']) ||
    value['meaning'] !== 'LOCAL_DECLARATION_NOT_ENROLLMENT_OR_QUALIFICATION' ||
    !object(value['session']) || !object(value['profile'])) throw new Error('Use the complete desktop enrollment-observation output.')
  const session = value['session'], profile = value['profile']
  const keys = ['platform', 'readerName', 'readerVersion', 'browserName', 'browserVersion', 'locale', 'keyboardLayout']
  if (!exact(session, ['deviceId', 'platform', 'interactiveSessionId', 'console']) ||
    session['platform'] !== 'darwin' || session['console'] !== true ||
    typeof session['deviceId'] !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(session['deviceId']) ||
    session['deviceId'] === '00000000-0000-0000-0000-000000000000' ||
    typeof session['interactiveSessionId'] !== 'string' || !/^[1-9][0-9]*$/.test(session['interactiveSessionId']) ||
    Number(session['interactiveSessionId']) >= 4294967295 ||
    !exact(profile, keys) || profile['platform'] !== 'darwin' || profile['readerName'] !== 'VoiceOver' || profile['browserName'] !== 'Safari' ||
    keys.some((key) => typeof profile[key] !== 'string' || !profile[key].trim() || profile[key].length > 128 || !/^[A-Za-z0-9 ._():-]+$/.test(profile[key])))
    throw new Error('A complete macOS console and VoiceOver/Safari profile is required; declarations are not qualification.')
  const canonical = JSON.stringify(Object.fromEntries(keys.sort().map((key) => [key, profile[key]])))
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical))
  const profileDigest = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('')
  if (value['profileDigest'] !== profileDigest) throw new Error('Profile digest does not match the observed profile. Collect fresh output; do not edit hashes.')
  return { session: session as Record<string, string | boolean>, profile: profile as Record<string, string>, profileDigest }
}
