# Reading-order authoring in the journey editor

The journey form offers a consecutive reading-order assertion only when the API advertises the
supported bounded `READER_NEXT_SEQUENCE` profile. Its executable-rule checkbox starts unchecked.
An author chooses the starting action and edits each literal phrase in a separate labelled textarea;
serialization produces consecutive action ordinals without trimming spaces or line breaks.

Validation reserves one action-budget slot for mandatory STOP and checks the complete sequence against
the remaining selected action budget, the selected NEXT action,
step count, per-phrase character limits and total UTF-8 bytes. Errors stay inline and are linked from
the existing focusable error summary. Add-step focuses the new field; removing the last step focuses
the last remaining field. Removing a draft assertion returns focus to its add button. Frozen versions
require explicit STOP capability: it is initially selected, and removing it produces a linked inline
action-group error before submission. No hidden action is added to the submitted selection. Existing
frozen versions are not changed; author a successor when an older version omitted STOP. Frozen versions
remain immutable and the existing explicit Freeze version operation does not execute a run.

No DOM/tab-order or focus-coordinate proof is implied. Unsupported server profiles cannot enable the
rule, missing captures remain UNKNOWN, and expected phrases stay in the protected reviewer/evaluator
contract rather than navigator input. Existing tokens and native controls are reused without a new
layout system or animation. Older unsupported rule families are not silently converted into this one.

Validation: production web build/typecheck and diff checks locally; targeted authoring/payload,
budget/UTF-8 and focus cases added to existing CI only. No local test suite, actual reader run,
provider invocation or deployment. This completes the UI continuation of the API/evaluator rule;
physical matched failure-to-repair acceptance and the other assertion families remain separate work.
