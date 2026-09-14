# Baseline source preparation

`accessforge_build_worker.baseline_source.prepare_baseline_source` recovers the
original committed baseline source through the existing bounded Git-object broker.
Repository locations come from operator configuration, not run/navigator requests.
It does not modify a checkout, execute source, fetch a repository or start Docker.

Preparation requires the original live approved canonical seal, a queued run with
no desktop lease, and no candidate-run binding. It checks the run/manifest/approval
and project linkage before reading source, releases those authority locks, recovers
the exact source snapshot, and then rechecks authority in a separate transaction.
Revocation or lease acquisition during a slow Git read must not be hidden behind
a long-lived approval lock. Cancellation and changed project/snapshot/tree bindings
refuse preparation.

The returned immutable source and binding include the **expected** artifact digest
from the seal. This is not an observed build identity, retained artifact, completed
build or functional outcome. A future durable baseline worker must reserve an
owned task before process creation, recheck dispatch authority, capture exact build
and cleanup receipts, retain/reconcile the resulting bytes, and independently run
the protected suite. These inputs alone never authorize container or reader startup.

Focused tests cover synthetic DB/authority boundaries and the existing real local
Git-object recovery suite. No actual baseline build, Docker/AT run, paid model call,
deployment or live migration was performed by this change.
