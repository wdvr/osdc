-- Disks Schema
-- This table stores persistent disk information

-- Create disks table if not exists (AFTER reservations due to FK)
CREATE TABLE IF NOT EXISTS disks (
    disk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    disk_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    size_gb INTEGER,
    disk_size TEXT,  -- Human-readable disk usage from du -sh (e.g., "1.2G")
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used TIMESTAMP WITH TIME ZONE,
    in_use BOOLEAN DEFAULT FALSE,
    reservation_id VARCHAR(255) REFERENCES reservations(reservation_id) ON DELETE SET NULL,
    is_backing_up BOOLEAN DEFAULT FALSE,
    is_deleted BOOLEAN DEFAULT FALSE,
    delete_date DATE,
    snapshot_count INTEGER DEFAULT 0,
    pending_snapshot_count INTEGER DEFAULT 0,
    ebs_volume_id TEXT,
    last_snapshot_at TIMESTAMP WITH TIME ZONE,
    operation_id UUID,
    operation_status TEXT,
    operation_error TEXT,
    latest_snapshot_content_s3 TEXT,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, disk_name)
);

-- Create indexes for disks table
CREATE INDEX IF NOT EXISTS idx_disks_user_id ON disks (user_id);

CREATE INDEX IF NOT EXISTS idx_disks_in_use
    ON disks (in_use) WHERE in_use = true;

CREATE INDEX IF NOT EXISTS idx_disks_is_deleted
    ON disks (is_deleted) WHERE is_deleted = true;

CREATE INDEX IF NOT EXISTS idx_disks_operation_id
    ON disks (operation_id) WHERE operation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_disks_reservation_id
    ON disks (reservation_id) WHERE reservation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_disks_delete_date
    ON disks (delete_date) WHERE delete_date IS NOT NULL;

-- Create trigger function for disks table
CREATE OR REPLACE FUNCTION update_disks_last_updated_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create trigger for disks table
DROP TRIGGER IF EXISTS update_disks_last_updated ON disks;

CREATE TRIGGER update_disks_last_updated
    BEFORE UPDATE ON disks
    FOR EACH ROW
    EXECUTE FUNCTION update_disks_last_updated_column();

