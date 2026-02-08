-- Reservations Schema
-- This table stores GPU reservation/job information

-- Create reservations table if not exists (MUST be before disks due to FK)
CREATE TABLE IF NOT EXISTS reservations (
    reservation_id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL,
    gpu_type VARCHAR(50),
    gpu_count INTEGER,
    instance_type VARCHAR(100),
    duration_hours FLOAT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    launched_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    name VARCHAR(255),
    github_user VARCHAR(255),
    pod_name VARCHAR(255),
    namespace VARCHAR(100) DEFAULT 'default',
    node_ip VARCHAR(50),
    node_port INTEGER,
    ssh_command TEXT,
    jupyter_enabled BOOLEAN DEFAULT FALSE,
    jupyter_url TEXT,
    jupyter_port INTEGER,
    jupyter_token VARCHAR(255),
    jupyter_error TEXT,
    ebs_volume_id VARCHAR(255),
    disk_name VARCHAR(255),
    failure_reason TEXT,
    current_detailed_status TEXT,
    status_history JSONB DEFAULT '[]'::jsonb,
    pod_logs TEXT,
    warning TEXT,
    secondary_users JSONB DEFAULT '[]'::jsonb,
    is_multinode BOOLEAN DEFAULT FALSE,
    master_reservation_id VARCHAR(255),
    node_index INTEGER,
    total_nodes INTEGER,
    cli_version VARCHAR(50),
    ebs_availability_zone VARCHAR(50),
    domain_name VARCHAR(255),
    fqdn VARCHAR(512),
    alb_config JSONB,
    preserve_entrypoint BOOLEAN DEFAULT false NOT NULL,
    node_private_ip VARCHAR(50)
);

-- Create indexes for reservations table
CREATE INDEX IF NOT EXISTS idx_reservations_user_id
    ON reservations(user_id);

CREATE INDEX IF NOT EXISTS idx_reservations_user_status
    ON reservations(user_id, status);

CREATE INDEX IF NOT EXISTS idx_reservations_status
    ON reservations(status);

CREATE INDEX IF NOT EXISTS idx_reservations_gpu_type_status
    ON reservations(gpu_type, status);

CREATE INDEX IF NOT EXISTS idx_reservations_created_at
    ON reservations(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_reservations_expires_at
    ON reservations(expires_at);

CREATE INDEX IF NOT EXISTS idx_reservations_master_id
    ON reservations(master_reservation_id)
    WHERE master_reservation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reservations_domain_name
    ON reservations(domain_name)
    WHERE domain_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reservations_fqdn
    ON reservations(fqdn)
    WHERE fqdn IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_reservations_node_private_ip
    ON reservations(node_private_ip)
    WHERE node_private_ip IS NOT NULL;

-- Add column comments for documentation
COMMENT ON COLUMN reservations.ebs_availability_zone IS 'AWS availability zone where EBS volume is located';
COMMENT ON COLUMN reservations.domain_name IS 'Subdomain assigned to this reservation (e.g., my-server)';
COMMENT ON COLUMN reservations.fqdn IS 'Full qualified domain name (e.g., my-server.gpudev.example.com)';
COMMENT ON COLUMN reservations.alb_config IS 'ALB/NLB configuration including target group and rule ARNs (JSON)';
COMMENT ON COLUMN reservations.preserve_entrypoint IS 'Whether to preserve Docker image ENTRYPOINT (true) or override with SSH (false)';
COMMENT ON COLUMN reservations.node_private_ip IS 'Private VPC IP address of the node (for SSH proxy routing)';

-- Create trigger function for reservations updated_at
CREATE OR REPLACE FUNCTION update_reservations_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger for reservations
DROP TRIGGER IF EXISTS trigger_reservations_updated_at ON reservations;

CREATE TRIGGER trigger_reservations_updated_at
    BEFORE UPDATE ON reservations
    FOR EACH ROW
    EXECUTE FUNCTION update_reservations_updated_at();

