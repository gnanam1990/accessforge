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
  const binary = join(scratch, 'safari-origin-probe');
  execFileSync('/usr/bin/xcrun', ['swiftc', '-O',
    fileURLToPath(new URL('../native/safari-origin.swift', import.meta.url)),
    '-o', binary], { stdio: 'inherit', timeout: 60000 });
  chmodSync(binary, 0o700);
  // A failed build leaves the previously installed helper intact; no partially linked executable.
  renameSync(binary, join(output, 'safari-origin-probe'));
} finally {
  // Only the exact task-owned temporary directory created immediately above.
  rmSync(scratch, { recursive: true });
}
