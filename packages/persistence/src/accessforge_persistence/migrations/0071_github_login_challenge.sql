-- Global pre-authentication state, like user_session, not workspace authorization.
-- Only independently generated high-entropy token hashes are persisted.
CREATE TABLE github_login_challenge (
    state_hash TEXT PRIMARY KEY CHECK (state_hash ~ '^[a-f0-9]{64}$'),
    browser_hash TEXT NOT NULL CHECK (browser_hash ~ '^[a-f0-9]{64}$'),
    verifier_hash TEXT NOT NULL CHECK (verifier_hash ~ '^[a-f0-9]{64}$'),
    configuration_hash TEXT NOT NULL CHECK (configuration_hash ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    CHECK (expires_at > created_at),
    CHECK (expires_at <= created_at + interval '10 minutes')
);

CREATE INDEX github_login_challenge_expiry ON github_login_challenge (expires_at);

COMMENT ON TABLE github_login_challenge IS
    'Single-use browser-bound GitHub identity challenges. Not account or workspace authority. '
    'Consume in an independently committed transaction before contacting the provider.';
