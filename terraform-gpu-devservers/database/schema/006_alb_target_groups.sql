-- ALB Target Groups Schema
-- This table stores ALB/NLB target group mappings for cleanup

CREATE TABLE IF NOT EXISTS alb_target_groups (
    reservation_id VARCHAR(255) PRIMARY KEY REFERENCES reservations(reservation_id) ON DELETE CASCADE,
    domain_name VARCHAR(255) NOT NULL,
    jupyter_target_group_arn TEXT,
    jupyter_rule_arn TEXT,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_alb_target_groups_domain_name
    ON alb_target_groups(domain_name);

CREATE INDEX IF NOT EXISTS idx_alb_target_groups_expires_at
    ON alb_target_groups(expires_at);

-- Create trigger for updated_at
CREATE OR REPLACE FUNCTION update_alb_target_groups_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_alb_target_groups_updated_at ON alb_target_groups;

CREATE TRIGGER trigger_alb_target_groups_updated_at
    BEFORE UPDATE ON alb_target_groups
    FOR EACH ROW
    EXECUTE FUNCTION update_alb_target_groups_updated_at();

