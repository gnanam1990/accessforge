/** Bind collector startup to the current reader-startup authority, not only the run lifetime. */
export function createEffectStartupAuthorization(
  authorize: (signal: AbortSignal) => Promise<void>,
  effect: {
    start(signal: AbortSignal): Promise<void>;
    assertActive(): void;
    abort(): void;
  },
  lifetime: AbortSignal,
): (signal: AbortSignal) => Promise<void> {
  let ready: Promise<void> | undefined;
  return async signal => {
    const check = () => {
      signal.throwIfAborted();
      lifetime.throwIfAborted();
    };
    check();
    await authorize(signal);
    check(); // A late approval cannot spawn a database-connected observer.
    const abort = () => effect.abort();
    signal.addEventListener('abort', abort, { once: true });
    try {
      check();
      // The long-lived worker uses run lifetime. Only its startup wait is bound
      // to this operation; ordinary completion of startup must not kill it.
      ready ??= effect.start(lifetime);
      await ready;
      check();
      effect.assertActive();
    } finally {
      signal.removeEventListener('abort', abort);
    }
  };
}
