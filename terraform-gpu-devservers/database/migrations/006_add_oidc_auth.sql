-- Migration: Add OIDC authentication support
-- Adds OIDC identity tracking to api_users and creates audit/token usage tables

-- ============================================================================
-- Add OIDC columns to api_users
-- ============================================================================

-- Add OIDC subject identifier (unique per issuer)
ALTER TABLE api_users ADD COLUMN IF NOT EXISTS oidc_subject VARCHAR(512);

-- Add OIDC issuer URL (e.g., https://token.actions.githubusercontent.com)
ALTER TABLE api_users ADD COLUMN IF NOT EXISTS oidc_issuer VARCHAR(512);

-- Create unique constraint for OIDC identity (subject + issuer combo)
CREATE UNIQUE INDEX IF NOT EXISTS idx_api_users_oidc_identity
    ON api_users(oidc_subject, oidc_issuer)
    WHERE oidc_subject IS NOT NULL AND oidc_issuer IS NOT NULL;

-- Index for looking up users by OIDC issuer
CREATE INDEX IF NOT EXISTS idx_api_users_oidc_issuer
    ON api_users(oidc_issuer)
    WHERE oidc_issuer IS NOT NULL;

-- Add comments for documentation
COMMENT ON COLUMN api_users.oidc_subject IS 'OIDC subject identifier (sub claim from JWT)';
COMMENT ON COLUMN api_users.oidc_issuer IS 'OIDC issuer URL (iss claim from JWT)';

-- ============================================================================
-- Create audit_log table
-- ============================================================================

CREATE TABLE IF NOT EXISTS audit_log (
    event_id SERIAL PRIMARY KEY,

    -- Who performed the action
    user_id INTEGER REFERENCES api_users(user_id) ON DELETE SET NULL,
    username VARCHAR(255),

    -- What action was performed
    event_type VARCHAR(64) NOT NULL,
    action TEXT NOT NULL,

    -- What resource was affected
    resource_type VARCHAR(64),
    resource_id VARCHAR(255),

    -- Additional details (JSON)
    details JSONB DEFAULT '{}',

    -- Request context
    ip_address INET,
    user_agent TEXT,

    -- Timestamp
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for querying user's audit history
CREATE INDEX IF NOT EXISTS idx_audit_log_user_id
    ON audit_log(user_id, created_at DESC)
    WHERE user_id IS NOT NULL;

-- Index for querying by event type
CREATE INDEX IF NOT EXISTS idx_audit_log_event_type
    ON audit_log(event_type, created_at DESC);

-- Index for querying resource history
CREATE INDEX IF NOT EXISTS idx_audit_log_resource
    ON audit_log(resource_type, resource_id, created_at DESC)
    WHERE resource_type IS NOT NULL AND resource_id IS NOT NULL;

-- Index for time-based queries (cleanup, reporting)
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
    ON audit_log(created_at);

-- Add comments
COMMENT ON TABLE audit_log IS 'Audit trail for all user actions and system events';
COMMENT ON COLUMN audit_log.event_type IS 'Event category (auth.login, reservation.create, etc.)';
COMMENT ON COLUMN audit_log.action IS 'Human-readable description of the action';
COMMENT ON COLUMN audit_log.details IS 'Additional event details in JSON format';

-- ============================================================================
-- Create token_usage table for LLM billing/monitoring
-- ============================================================================

CREATE TABLE IF NOT EXISTS token_usage (
    usage_id SERIAL PRIMARY KEY,

    -- Who used the tokens
    user_id INTEGER NOT NULL REFERENCES api_users(user_id) ON DELETE CASCADE,

    -- What model was used
    model VARCHAR(128) NOT NULL,

    -- Token counts
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,

    -- Cost tracking (optional)
    cost_usd DECIMAL(12, 6),

    -- Request correlation
    request_id VARCHAR(255),

    -- Timestamp
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index for querying user's token usage
CREATE INDEX IF NOT EXISTS idx_token_usage_user_id
    ON token_usage(user_id, created_at DESC);

-- Index for querying by model
CREATE INDEX IF NOT EXISTS idx_token_usage_model
    ON token_usage(model, created_at DESC);

-- Index for time-based aggregation (billing reports)
CREATE INDEX IF NOT EXISTS idx_token_usage_created_at
    ON token_usage(created_at);

-- Index for correlating with requests
CREATE INDEX IF NOT EXISTS idx_token_usage_request_id
    ON token_usage(request_id)
    WHERE request_id IS NOT NULL;

-- Add comments
COMMENT ON TABLE token_usage IS 'Tracks LLM/AI token usage for billing and monitoring';
COMMENT ON COLUMN token_usage.model IS 'LLM model name (e.g., claude-3-opus, gpt-4)';
COMMENT ON COLUMN token_usage.cost_usd IS 'Estimated cost in USD based on model pricing';
COMMENT ON COLUMN token_usage.request_id IS 'Request ID for correlation with audit log';

-- ============================================================================
-- Create view for user token usage summary
-- ============================================================================

CREATE OR REPLACE VIEW user_token_summary AS
SELECT
    u.user_id,
    u.username,
    t.model,
    COUNT(*) as request_count,
    SUM(t.input_tokens) as total_input_tokens,
    SUM(t.output_tokens) as total_output_tokens,
    SUM(t.total_tokens) as total_tokens,
    SUM(COALESCE(t.cost_usd, 0)) as total_cost_usd,
    MIN(t.created_at) as first_usage,
    MAX(t.created_at) as last_usage
FROM token_usage t
JOIN api_users u ON t.user_id = u.user_id
GROUP BY u.user_id, u.username, t.model;

COMMENT ON VIEW user_token_summary IS 'Aggregated token usage per user per model';

-- ============================================================================
-- Create function for audit log cleanup
-- ============================================================================

CREATE OR REPLACE FUNCTION cleanup_old_audit_logs(days_to_keep INTEGER DEFAULT 90)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM audit_log
    WHERE created_at < NOW() - (days_to_keep || ' days')::INTERVAL;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_old_audit_logs(INTEGER) IS 'Deletes audit log entries older than specified days';

-- ============================================================================
-- Migrate existing AWS-authenticated users to have placeholder OIDC info
-- ============================================================================

-- For existing users authenticated via AWS, we can optionally mark them
-- This allows gradual migration without breaking existing functionality
-- Uncomment if you want to track AWS SSO users separately:

-- UPDATE api_users
-- SET oidc_issuer = 'aws-sts-legacy'
-- WHERE oidc_issuer IS NULL
--   AND username LIKE '%@%';
