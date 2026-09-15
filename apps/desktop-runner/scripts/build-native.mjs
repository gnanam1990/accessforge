import { execFileSync } from 'node:child_process';
import { chmodSync, lstatSync, mkdirSync, mkdtempSync, renameSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.platform !== 'darwin') throw new Error('Safari probe requires macOS; no synthetic binary is built');
const output = fileURLToPath(new URL('../dist/native/', import.meta.url));
mkdirSync(output, { recursive: true, mode: 0o700 });
const directory = lstatSync(output);
if (!directory.isDirectory() || directory.uid !== process.getuid() || (directory.mode & 0o022) !== 0) {
  throw new Error('native output must be an owned directory not writable by other users');
}
const scratch = mkdtempSync(join(output, '.compile-'));
try {
  for (const name of ['safari-origin', 'voiceover-capture']) {
    const binary = join(scratch, `${name}-probe`);
    execFileSync('/usr/bin/xcrun', ['swiftc', '-O',
      fileURLToPath(new URL(`../native/${name}.swift`, import.meta.url)),
      '-o', binary], { stdio: 'inherit', timeout: 60000 });
    chmodSync(binary, 0o700);
  }
  // Both compile before replacing either installed helper; never publish a partial executable.
  for (const name of ['safari-origin', 'voiceover-capture']) {
    renameSync(join(scratch, `${name}-probe`), join(output, `${name}-probe`));
  }
} finally {
  // Only the exact task-owned temporary directory created immediately above.
  rmSync(scratch, { recursive: true });
}
