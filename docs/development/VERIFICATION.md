# Verification command manifest

Commands recorded here must be **real commands in this repository** that fail when their
prerequisites are missing. A command is added only once it exists and has actually been executed.

## Current state — 2026-09-09

No application code, package manifest, or test harness exists yet. Module 01 owns creating the
workspace and the first meaningful CI pipeline, and will populate the table below.

| Layer | Entry point | Status |
|---|---|---|
| Static / build | — | not yet created (module 01) |
| Unit / property | — | not yet created (module 01) |
| Integration (real PostgreSQL) | — | not yet created (module 02+) |
| Failure / recovery | — | not yet created (module 04+) |
| Security | — | not yet created (module 03+) |
| Runtime (real services) | — | not yet created (module 18+) |
| **Actual VoiceOver** | — | not yet created (module 08) — **capability BLOCKED**, see `docs/capabilities.md` §5.1 |
| **Actual NVDA** | — | not yet created (module 09) — **capability BLOCKED**, no Windows host |
| Human / UI | — | not yet created (module 21+) |
| Release operations | — | not yet created (module 27+) |

## Commands executed by module 00

Read-only capability probes only; none of these are acceptance tests.

```bash
sw_vers; uname -m                         # host OS and architecture
node -v; pnpm -v; python3 -V; uv --version # runtimes
pg_isready                                 # PostgreSQL reachability
psql -d postgres -tAc "select version();"  # PostgreSQL server version
docker info                                # Docker daemon state (observed: not running)
colima status                              # Colima state (observed: not running)
ls -d /System/Library/CoreServices/VoiceOver.app
defaults read com.apple.VoiceOver4/default SCREnableAppleScript          # legacy path
/usr/libexec/PlistBuddy -c "Print :SCREnableAppleScript" \
  "$HOME/Library/Group Containers/group.com.apple.VoiceOver/Library/Preferences/com.apple.VoiceOver4/default.plist"  # Sequoia+ path
npm view @guidepup/guidepup version        # adapter availability
```

## Evidence storage

Run outputs belong in the git-ignored `.evidence/` directory and are referenced from pull requests
and handoffs by path and run identity. They are never committed, and a passing run is never
recorded by adding a "tests passed" file to the tree.
