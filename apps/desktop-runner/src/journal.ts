/**
 * The on-disk action journal.
 *
 * Append-only JSON lines, and every append is followed by `fsync` before the promise resolves. The
 * flush is the entire point: the crash that a buffered write would lose is precisely the crash the
 * record exists to survive. A journal that resolved on a buffered write would pass every test that
 * did not kill the process, and would be useless for the one case it is for.
 */

import { closeSync, constants, existsSync, fstatSync, fsyncSync, openSync, readFileSync, writeSync } from 'node:fs';
import { dirname } from 'node:path';
import type { Journal, JournalEntry } from './supervisor.js';

export class FileJournal implements Journal {
  constructor(private readonly path: string) {}

  /** Exercise this exact journal's append descriptor and directory durability without adding
   * a fake action. This is a current filesystem check, not permission to dispatch an action.
   */
  async probeWritable(): Promise<boolean> {
    let fd: number | undefined;
    try {
      fd = this.openOwnedAppend();
      fsyncSync(fd);
      const directory = openSync(dirname(this.path), 'r');
      try { fsyncSync(directory); } finally { closeSync(directory); }
      return true;
    } catch {
      return false;
    } finally {
      if (fd !== undefined) closeSync(fd);
    }
  }

  private openOwnedAppend(): number {
    const fd = openSync(this.path, constants.O_WRONLY | constants.O_APPEND | constants.O_CREAT |
      constants.O_NOFOLLOW | constants.O_NONBLOCK, 0o600);
    try {
      const info = fstatSync(fd);
      if (!info.isFile() || typeof process.getuid !== 'function' || info.uid !== process.getuid() ||
          info.nlink !== 1 || (info.mode & 0o077) !== 0) throw new Error('private journal required');
      return fd;
    } catch (error) { closeSync(fd); throw error; }
  }

  async appendAndFlush(entry: JournalEntry): Promise<void> {
    // Synchronous and fsynced, deliberately. The asynchronous API would let the runtime hold the
    // bytes in a buffer while `dispatch` sends a keystroke to the operating system, which is the
    // exact ordering this journal exists to prevent.
    const fd = this.openOwnedAppend();
    try {
      const bytes = Buffer.from(`${JSON.stringify(entry)}\n`);
      let written = 0;
      while (written < bytes.length) {
        const count = writeSync(fd, bytes, written, bytes.length - written);
        if (count <= 0) throw new Error('local journal write did not complete');
        written += count;
      }
      fsyncSync(fd);
      // A new file's data can survive while its directory entry does not. Both must be durable
      // before the caller is allowed to touch the OS. Unsupported directory fsync fails closed.
      const directory = openSync(dirname(this.path), 'r');
      try { fsyncSync(directory); } finally { closeSync(directory); }
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
