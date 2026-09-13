/** Bootstrap-only native command. No reader is started and no OS capability is advertised. */
import { readReceiverConfig, receiveDispatch } from './dispatch-receiver.js';

async function main(): Promise<void> {
  const configPath = process.argv[2];
  if (configPath === undefined || process.argv.length !== 3) throw new Error('configuration');
  const chunks: Buffer[] = [];
  let bytes = 0;
  const timer = setTimeout(() => process.exit(78), 10000);
  try {
    for await (const chunk of process.stdin) {
      const data = Buffer.from(chunk as Uint8Array);
      bytes += data.length;
      if (bytes > 4096) throw new Error('input bound');
      chunks.push(data);
    }
    const receipt = await receiveDispatch(readReceiverConfig(configPath),
      JSON.parse(Buffer.concat(chunks).toString('utf8')));
    process.stdout.write(JSON.stringify(receipt) + '\n');
  } finally {
    clearTimeout(timer);
  }
}

main().catch(() => {
  // Never print thrown objects, input, token, config, response or request headers.
  process.stderr.write('receiver refused or reception unknown; retain local claim and reconcile\n');
  process.exitCode = 78;
});
