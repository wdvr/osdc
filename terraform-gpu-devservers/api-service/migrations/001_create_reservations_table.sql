-- Migration: Create reservations table for tracking GPU job/reservation state
-- This table stores the complete state of each GPU reservation,
-- replacing DynamoDB as the source of truth

CREATE TABLE IF NOT EXISTS reservations (
    -- Primary identifiers
    reservation_id VARCHAR(255) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    
    -- Job metadata
    status VARCHAR(50) NOT NULL,  -- queued, pending, preparing, active, cancelled, expired, failed
    gpu_type VARCHAR(50),          -- h100, h200, a100, etc.
    gpu_count INTEGER,
    instance_type VARCHAR(100),    -- p5.48xlarge, etc.
    duration_hours FLOAT NOT NULL,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    launched_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- User-facing metadata
    name VARCHAR(255),
    github_user VARCHAR(255),
    
    -- Kubernetes/Pod info
    pod_name VARCHAR(255),
    namespace VARCHAR(100) DEFAULT 'default',
    node_ip VARCHAR(50),
    node_port INTEGER,
    
    -- Connection info
    ssh_command TEXT,
    
    -- Jupyter Lab
    jupyter_enabled BOOLEAN DEFAULT FALSE,
    jupyter_url TEXT,
    jupyter_port INTEGER,
    jupyter_token VARCHAR(255),
    jupyter_error TEXT,
    
    -- Disk/Storage
    ebs_volume_id VARCHAR(255),
    disk_name VARCHAR(255),
    
    -- Status tracking
    failure_reason TEXT,
    current_detailed_status TEXT,
    status_history JSONB DEFAULT '[]'::jsonb,
    pod_logs TEXT,
    warning TEXT,
    
    -- Secondary users (JSON array of GitHub usernames)
    secondary_users JSONB DEFAULT '[]'::jsonb,
    
    -- Multinode support
    is_multinode BOOLEAN DEFAULT FALSE,
    master_reservation_id VARCHAR(255),
    node_index INTEGER,
    total_nodes INTEGER,
    
    -- CLI version tracking
    cli_version VARCHAR(50)
);

-- Indexes for efficient queries

-- Query by user (most common - list user's reservations)
CREATE INDEX idx_reservations_user_id ON reservations(user_id);

-- Query by user and status (filter user's active/pending reservations)
CREATE INDEX idx_reservations_user_status ON reservations(user_id, status);

-- Query by status (admin queries, queue monitoring)
CREATE INDEX idx_reservations_status ON reservations(status);

-- Query by GPU type and status (availability checking)
CREATE INDEX idx_reservations_gpu_type_status ON reservations(gpu_type, status);

-- Query by creation time (sorting, cleanup jobs)
CREATE INDEX idx_reservations_created_at ON reservations(created_at DESC);

-- Query by expiration time (cleanup jobs, TTL monitoring)
CREATE INDEX idx_reservations_expires_at ON reservations(expires_at);

-- Query multinode groups
CREATE INDEX idx_reservations_master_id ON reservations(master_reservation_id)
    WHERE master_reservation_id IS NOT NULL;

-- Updated timestamp trigger
CREATE OR REPLACE FUNCTION update_reservations_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_reservations_updated_at
    BEFORE UPDATE ON reservations
    FOR EACH ROW
    EXECUTE FUNCTION update_reservations_updated_at();

-- Comments for documentation
COMMENT ON TABLE reservations IS 'Stores GPU reservation/job state, replacing DynamoDB';
COMMENT ON COLUMN reservations.reservation_id IS 'Unique reservation ID (UUID)';
COMMENT ON COLUMN reservations.user_id IS 'User email or identifier';
COMMENT ON COLUMN reservations.status IS 'Current status: queued, pending, preparing, active, cancelled, expired, failed';
COMMENT ON COLUMN reservations.gpu_type IS 'GPU type requested (h100, h200, a100, a10g, t4, etc.)';
COMMENT ON COLUMN reservations.instance_type IS 'AWS instance type / K8s node type (p5.48xlarge, etc.)';
COMMENT ON COLUMN reservations.pod_name IS 'Kubernetes pod name for active reservations';
COMMENT ON COLUMN reservations.ssh_command IS 'SSH command to connect (e.g., "ssh gpu-dev-abc123")';
COMMENT ON COLUMN reservations.status_history IS 'JSON array of status transitions with timestamps';
COMMENT ON COLUMN reservations.master_reservation_id IS 'For multinode: ID of the master node reservation';

