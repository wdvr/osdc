-- Migration: Add missing columns to reservations table
-- These columns are used by the application but were missing from the schema

-- Add ebs_availability_zone column
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS ebs_availability_zone VARCHAR(50);

-- Add domain_name column (subdomain, not full FQDN)
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS domain_name VARCHAR(255);

-- Add fqdn column (full qualified domain name)
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS fqdn VARCHAR(512);

-- Add alb_config column (JSON configuration for ALB/NLB)
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS alb_config JSONB;

-- Add preserve_entrypoint flag (NOT NULL for clarity - boolean should be definitive, not tri-state)
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS preserve_entrypoint BOOLEAN DEFAULT false NOT NULL;

-- Add node_private_ip column (for VPC-internal SSH proxy routing)
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS node_private_ip VARCHAR(50);

-- Create indexes for lookup performance
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

