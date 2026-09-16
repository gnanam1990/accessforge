-- Explicit operator-created bindings, not email/login-name matching or membership grants.
CREATE TABLE github_user_identity (
    github_subject BIGINT PRIMARY KEY CHECK (github_subject > 0),
    user_id UUID NOT NULL UNIQUE REFERENCES app_user(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    revoked_at TIMESTAMPTZ,
    UNIQUE (github_subject, user_id)
);

ALTER TABLE user_session ADD COLUMN github_subject BIGINT;
ALTER TABLE user_session ADD CONSTRAINT user_session_github_identity
    FOREIGN KEY (github_subject, user_id)
    REFERENCES github_user_identity(github_subject, user_id) ON DELETE CASCADE;
CREATE INDEX user_session_github_subject ON user_session(github_subject)
    WHERE github_subject IS NOT NULL;

COMMENT ON COLUMN user_session.github_subject IS
    'Provider identity provenance retained across rotation. Revoked bindings must refuse every '
    'subsequent resolution; NULL preserves existing non-GitHub sessions.';
