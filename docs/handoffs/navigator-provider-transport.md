# Navigator provider transport bounds

The configured Strands `ModelRetryStrategy` is now the only navigator model retry layer. The
Bedrock client explicitly sets botocore `total_max_attempts=1` in standard mode, preventing AWS
environment/shared-profile retry defaults from multiplying the consented attempts and token hold.
The retry count is not measured token usage or a currency spending guarantee.

The same client configuration ignores configured endpoint URL overrides and bounds connect/read
timeouts by the reviewed profile (connect at most ten seconds). Construction reads back the actual
client region, endpoint, retry configuration and timeouts; a mismatch closes that client and refuses
agent creation without logging configuration or credentials. The endpoint is the currently pinned
commercial-region Bedrock runtime URL. Alternate/VPC/FIPS/dual-stack endpoints are not silently
accepted by this profile; supporting one requires an explicit reviewed transport contract.

The existing outer invocation timeout, native lease deadline, cancellation fencing and conservative
UNCONFIRMED ledger disposition remain unchanged. Socket timeouts are not proof that a remote call
stopped. Credential-provider discovery during construction is still handled conservatively as
potential provider entry; this change does not claim to bound every credential-chain operation.

The domain timeout validator also checks bounds before converting an integer to a float, so a huge
out-of-range JSON integer produces the intended validation refusal rather than OverflowError.

## Evidence and remaining work

- The orchestrator declares the already locked botocore 1.43.91 dependency directly; the lock update
  adds only that dependency edge and does not upgrade third-party packages.
- Changed-file Ruff and strict mypy checks passed.
- Three focused local synthetic construction checks passed at one, thirty and 120 seconds. Dummy
  credentials and hostile retry/endpoint environment settings exercise the real installed SDK's
  resolved client metadata without invoking a provider. The full suites remain in GitHub CI.
- This is transport configuration enforcement, not a retained runtime model identity receipt or
  provider-response attestation. MODEL_CALL_STARTED remains an intent checkpoint. Final evaluation
  must not fill an observed model identity from the sealed profile or this handoff. Durable runtime
  observation ingestion and finalizer integration remain to be built.
- No paid call, physical reader, OS permission, migration or deployment was executed.
