# GitHub receipt ingress body deadline

The receipt-only webhook receiver now imposes one five-second deadline over the entire body
read. Previously its byte limit bounded memory but not the duration of a partial request.
Trickling a new chunk does not renew the deadline. An incomplete body returns a static 408
REFUSED response before authentication or any database receipt transaction is attempted.

The existing byte limit, duplicate-header refusal, exact binding and digest deduplication
remain unchanged. A complete request still reaches the normal authentication/receipt boundary.
Host TLS, connection limits, rate limiting and redacted logging are still necessary; this
application deadline is not a substitute for deployment-level resource controls.

Two focused in-process ingress checks cover a stalled body and a completed body. Receipt work
is stubbed in those checks to assert its ordering, not to claim HMAC, database or remote GitHub
acceptance. Existing authentication and integration checks cover their own boundaries.
