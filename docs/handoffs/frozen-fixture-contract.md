# Frozen fixture contract integration

The compiler now retains the original logical fixture hash preimage in the protected reviewer
summary. Contract schema version 2 includes template ID, navigator values and hashes of the entire
private reset/observer maps. Neither private key names nor values are published. New compilations
receive new fixture and journey digests; historical versions and seals remain unchanged. It does not expose the
contract through navigator policy. A loader checks the original row hash and navigator-value binding;
historical versions without the preimage are refused rather than reconstructed.

The completion observer now reads that original contract and binds the instance's template ID,
navigator values and private observer configuration to it. It separately checks the supported
reference-app template digest before reading the application's nonce-scoped state. Historical
versions missing the original preimage are refused; existing records are not backfilled.
Navigator planning verifies the same logical/template distinction without loading private observer
values. Its loader selects only fixture metadata, not the rest of the reviewer assertion summary.

This is not completed runtime fixture evidence. Next work must bind trusted reset material,
provision a fresh reference-app instance and retain its app-template identity and nonce.
Do not simply replace one digest with the other or accept a caller-supplied nonce as proof of reset.

Changed-file Ruff/strict mypy passed. Five focused synthetic loader cases passed locally in 0.25s.
The existing authenticated observer and finalizer integration fixtures now distinguish logical
contract hashes from application-template hashes. Added wrong-value/oracle rejection cases run in
CI; the full local integration suite was not repeated.
No live fixture, database migration, AT session, billable provider call or deployment was performed.

CI found that retaining private key names exposed oracle metadata. The v2 privacy correction was
source-reviewed and applied through GitHub while the local host was unresponsive. The original
no-oracle-material regression remains unchanged; an additional case checks that renaming a private
key still changes identity without publishing either name. This correction awaits fresh CI and has
not been locally executed. Pending controller work must consume v2 map hashes, not removed key lists.
