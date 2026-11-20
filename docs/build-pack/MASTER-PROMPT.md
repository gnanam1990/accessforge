# AccessForge Tester Desk · Master build prompt

Build AccessForge Tester Desk in the repository supplied with this pack. The product's first complete journey is the following.

An agency specifies checkout on a release-specific URL, Safari version, VoiceOver, a keyboard-only path, permitted evidence and tester fee. The tester accepts after reviewing consent terms, reports that focus is trapped after the shipping form, attaches an allowed trace and submits. An agency reviewer accepts the complete evidence even though checkout failed. Accounting pays the tester for the work. A fixed release gets its own retest.

## Intake

Read repository instructions and inspect the current branch, HEAD, uncommitted work, existing architecture, locked dependencies, test commands and any ongoing implementation. Check [source baselines](SOURCES.md) before reusing upstream work. Record whether this is a dedicated greenfield project or an extension. For an existing implementation, trace each claimed capability from its real entry point before assigning completion. Preserve user changes.

## Build

Follow [the module index](MODULES.md). Drive changes from observable failing behavior. Implement actual domain rules, authorization, UI/API, storage and recovery. Use test doubles for deterministic fault injection and pair them with real local integration tests. Keep independent work moving when a partner dependency blocks one path. Expose each blocked external feature honestly in the UI and status ledger.

## Evidence and exit

Commission AF-21 pays 20.00 USD for a five-step checkout on release r18. Step four traps keyboard focus. Tester marks step four failed and step five unreachable, submits the agreed evidence, reviewer accepts it. Expect task failed, evidence accepted and one 20.00 payable. Repeat the submit and approval after restart: still one payable. Open r19 and show a new pending retest with no inherited pass.

Show the normal workflow in a clean checkout, exercise failure and restart, and leave evidence tied to the exact tested revision. A recorded demo uses the running product and permitted inputs. Verify logs, screenshots and exports are redacted. Local building and testing begin when the owner invokes this prompt for the supplied target. Remote publishing, deployment, paid calls, account creation, signing and public submissions need action-specific authority. Use the owner’s existing grants when applicable.

After every module, and before yielding, update the [continuation records](DELIVERY.md). Tell the next agent what already works, what failed, the last command, and the first concrete operation to perform.
