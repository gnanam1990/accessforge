#!/usr/bin/env node
/** Operator-only, single read. Does not accept a ticket, start a reader, or authorize an action. */
import { createSafariOriginProbe, SafariProbeUnavailable } from './safari-origin.js';

async function main(): Promise<void> {
  if (process.argv.length !== 2) throw new Error('input');
  const input: Buffer[] = [];
  let size = 0;
  const timeout = setTimeout(() => {
    process.stdout.write(JSON.stringify({ status: 'UNKNOWN', reason: 'INPUT_OR_PROBE_TIMEOUT' }) + '\n');
    process.exit(78);
  }, 5000);
  try {
    for await (const chunk of process.stdin) {
      const bytes = Buffer.from(chunk as Uint8Array);
      size += bytes.length;
      if (size > 8192) throw new Error('input');
      input.push(bytes);
    }
    const value = JSON.parse(Buffer.concat(input).toString('utf8')) as Record<string, unknown>;
    if (value === null || typeof value !== 'object' || Array.isArray(value) ||
        Object.keys(value).sort().join(',') !== 'expectedBrowserVersion,expectedUrl' ||
        typeof value.expectedUrl !== 'string' || typeof value.expectedBrowserVersion !== 'string') throw new Error('input');
    const probe = createSafariOriginProbe({ expectedUrl: value.expectedUrl, expectedBrowserVersion: value.expectedBrowserVersion });
    const observedOrigin = await probe();
    process.stdout.write(JSON.stringify({ status: 'OBSERVED_ONCE', observedOrigin,
      meaning: 'One native browser-origin observation; not preflight readiness, reader proof or execution authority.' }) + '\n');
  } finally { clearTimeout(timeout); }
}

main().catch((error: unknown) => {
  const reason = error instanceof SafariProbeUnavailable ? error.reason : 'INPUT_OR_PROBE_UNAVAILABLE';
  process.stdout.write(JSON.stringify({ status: 'UNKNOWN', reason }) + '\n');
  process.exitCode = 78;
});
