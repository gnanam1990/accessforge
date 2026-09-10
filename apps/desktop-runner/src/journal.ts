/**
 * The on-disk action journal.
 *
 * Append-only JSON lines, and every append is followed by `fsync` before the promise resolves. The
 * flush is the entire point: the crash that a buffered write would lose is precisely the crash the
 * record exists to survive. A journal that resolved on a buffered write would pass every test that
 * did not kill the process, and would be useless for the one case it is for.
 */

import { closeSync, existsSync, fsyncSync, openSync, readFileSync, writeSync } from 'node:fs';
import type { Journal, JournalEntry } from './supervisor.js';

export class FileJournal implements Journal {
  constructor(private readonly path: string) {}

  async appendAndFlush(entry: JournalEntry): Promise<void> {
    // Synchronous and fsynced, deliberately. The asynchronous API would let the runtime hold the
    // bytes in a buffer while `dispatch` sends a keystroke to the operating system, which is the
    // exact ordering this journal exists to prevent.
    const fd = openSync(this.path, 'a');
    try {
      writeSync(fd, `${JSON.stringify(entry)}\n`);
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
  }

  async read(): Promise<readonly JournalEntry[]> {
    if (!existsSync(this.path)) {
      return [];
    }
    const lines = readFileSync(this.path, 'utf8').split('\n').filter((l) => l.trim() !== '');
    const entries: JournalEntry[] = [];
    for (const line of lines) {
      try {
        entries.push(JSON.parse(line) as JournalEntry);
      } catch {
        // A truncated final line is the expected shape of a crash mid-append. It is skipped rather
        // than treated as corruption of the whole file: the entries before it are intact and are
        // exactly what the restart needs to read. A truncated entry is also not an unresolved
        // action -- nothing was dispatched on the strength of a write that never completed.
        continue;
      }
    }
    return entries;
  }
}

/** An in-memory journal for tests. Records flush calls so a test can assert the ordering. */
export class MemoryJournal implements Journal {
  readonly entries: JournalEntry[] = [];
  readonly flushes: string[] = [];

  async appendAndFlush(entry: JournalEntry): Promise<void> {
    this.entries.push(entry);
    this.flushes.push(entry.actionId);
  }

  async read(): Promise<readonly JournalEntry[]> {
    return this.entries;
  }
}
