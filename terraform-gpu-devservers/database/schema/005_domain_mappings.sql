-- Domain Mappings Schema
-- This table stores SSH domain name to reservation mappings

CREATE TABLE IF NOT EXISTS domain_mappings (
    domain_name VARCHAR(255) PRIMARY KEY,
    node_ip VARCHAR(50) NOT NULL,
    node_port INTEGER NOT NULL,
    reservation_id VARCHAR(255) NOT NULL REFERENCES reservations(reservation_id) ON DELETE CASCADE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_domain_mappings_reservation_id
    ON domain_mappings(reservation_id);

CREATE INDEX IF NOT EXISTS idx_domain_mappings_expires_at
    ON domain_mappings(expires_at);

-- Create trigger for updated_at
CREATE OR REPLACE FUNCTION update_domain_mappings_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_domain_mappings_updated_at ON domain_mappings;

CREATE TRIGGER trigger_domain_mappings_updated_at
    BEFORE UPDATE ON domain_mappings
    FOR EACH ROW
    EXECUTE FUNCTION update_domain_mappings_updated_at();

