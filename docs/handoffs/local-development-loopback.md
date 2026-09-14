# Local development login binding

The API's opt-in `local-development` identity provider accepts an enabled user's email without
a password. The existing `environment=local` guard did not restrict the listening interface:
the ordinary API entrypoint passed `ApiSettings.host` directly to uvicorn, including wildcard
and LAN addresses. A local environment label was not a network boundary.

Configuration now additionally requires an IP loopback address or `localhost` for that provider.
Wildcard/empty hosts, remote IPs and arbitrary DNS names fail before startup. Other providers
retain their existing host behavior; this does not implement production authentication.

The focused reproducer failed for all five unsafe binding inputs before the fix, while its
allowed-path controls passed. Configuration checks after the fix cover IPv4/IPv6 loopback and
the provider-disabled case. No network listener or live account was needed to reproduce it.

This is a startup configuration guard, not a proxy authentication mechanism. Do not expose
the development bridge through a tunnel/reverse proxy or an ASGI launcher overriding this
setting. An externally reachable deployment requires an actual identity provider, not this
passwordless bridge.
