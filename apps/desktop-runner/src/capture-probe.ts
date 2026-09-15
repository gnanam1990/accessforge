/** Private live channel health, never a phrase observation or acoustic freshness attestation. */
import { execFile } from 'node:child_process';
import { lstat, realpath } from 'node:fs/promises';
import { isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const known = '{"schemaVersion":1,"status":"KNOWN","captureResponsive":true}';

export function createVoiceOverCaptureProbe(helperPath = fileURLToPath(
  new URL('./native/voiceover-capture-probe', import.meta.url),
)): (signal: AbortSignal) => Promise<boolean> {
  return async signal => {
    try {
      if (signal.aborted || !isAbsolute(helperPath) || await realpath(helperPath) !== resolve(helperPath)) return false;
      const info = await lstat(helperPath);
      if (!info.isFile() || info.uid !== process.getuid?.() || (info.mode & 0o022) !== 0 ||
          (info.mode & 0o100) === 0 || signal.aborted) return false;
      return await new Promise<boolean>(yes => {
        execFile(helperPath, [], {timeout: 1500, killSignal: 'SIGKILL', maxBuffer: 1024,
          encoding: 'utf8', signal, env: {PATH: '/usr/bin:/bin', LANG: 'en_US.UTF-8'}},
        (error, stdout) => yes(!error && !signal.aborted && stdout.trim() === known));
      });
    } catch { return false; }
  };
}
