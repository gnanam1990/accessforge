# AccessForge Tester Desk · Reuse and evidence

The owner’s GitHub inventory and selected source files were reviewed on 20 November 2026. Each link below pins the reviewed revision. Inspect current target behavior, compatibility and license at intake. If private access is unavailable, implement the specified interface independently and record that choice. This pack contains no upstream source code or credentials.

### accessforge

Pinned baseline `81d2a7d0710b20286e24376951010f7dfc71eeac` (public). Already covers screen-reader reproduction, scoped repair and independent retest. A new commercial product would need external tester procurement, compensation and client acceptance.

[patch_policy.py](https://github.com/gnanam1990/accessforge/blob/81d2a7d0710b20286e24376951010f7dfc71eeac/packages/domain/src/accessforge_domain/patch_policy.py) · [sessions.py](https://github.com/gnanam1990/accessforge/blob/81d2a7d0710b20286e24376951010f7dfc71eeac/apps/api/src/accessforge_api/auth/sessions.py)

### kitepass-sdk

Pinned baseline `797f339d53dcfefe5d18ca3297ba4d2051d5c7ec` (public). Policy and spend trackers are integration candidates. Verify against the current Passport contracts and session behavior before adopting.

[policy.ts](https://github.com/gnanam1990/kitepass-sdk/blob/797f339d53dcfefe5d18ca3297ba4d2051d5c7ec/packages/core/src/policy.ts) · [spend-tracker.ts](https://github.com/gnanam1990/kitepass-sdk/blob/797f339d53dcfefe5d18ca3297ba4d2051d5c7ec/packages/core/src/spend-tracker.ts)

## Project lineage

Hackathon Atlas surfaced [Accessibility Repair Studio](https://devpost.com/software/accessibility-repair-studio) as adjacent work. The user-approved project boundary is: Procure an independent human retest of one user journey on one release, browser and assistive technology combination. Extend AccessForge when the target checkout is provided. Keep repair generation and broad accessibility audits in their existing product boundaries. First support one web journey and a tester-supplied screen reader.

## External dependency

An explicit extension of AccessForge. Needs participating testers, privacy controls and a supported payment route. One task retest cannot certify an entire site. Platform interfaces are documented in [provider notes](KITE-AND-PROVIDERS.md). Market demand, integration behavior, source data access and live payment authority require separate evidence.
