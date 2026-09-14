# Frozen fixture contract integration

The compiler now retains the original logical fixture hash preimage in the protected reviewer
summary. Its hash is unchanged: it includes template ID, navigator values, reset/observer key names
and hashes of private reset/observer values. It does not include those private values or expose the
contract through navigator policy. A loader checks the original row hash and navigator-value binding;
historical versions without the preimage are refused rather than reconstructed.

This is the first part of fixture provisioning, not completed runtime fixture evidence. The current
completion observer compares the application template digest with the manifest logical fixture digest;
these are distinct identities. Next work must use the retained contract to bind trusted reset/observer
material, provision a fresh reference-app instance and retain its app-template identity and nonce.
Do not simply replace one digest with the other or accept a caller-supplied nonce as proof of reset.

Changed-file Ruff/strict mypy passed. Five focused synthetic loader cases passed locally in 0.25s.
No live fixture, database migration, AT session, billable provider call or deployment was performed.
