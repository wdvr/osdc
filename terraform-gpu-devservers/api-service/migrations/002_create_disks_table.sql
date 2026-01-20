-- Migration: Create disks table
-- Purpose: Migrate disk metadata from DynamoDB to PostgreSQL
-- Date: 2026-01-20

-- Create disks table
CREATE TABLE IF NOT EXISTS disks (
    disk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    disk_name TEXT NOT NULL,
    user_id TEXT NOT NULL,
    size_gb INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used TIMESTAMP WITH TIME ZONE,
    in_use BOOLEAN DEFAULT FALSE,
    reservation_id VARCHAR(255) REFERENCES reservations(reservation_id) ON DELETE SET NULL,
    is_backing_up BOOLEAN DEFAULT FALSE,
    is_deleted BOOLEAN DEFAULT FALSE,
    delete_date DATE,  -- Date when disk will be permanently deleted (30 days after soft delete)
    snapshot_count INTEGER DEFAULT 0,
    pending_snapshot_count INTEGER DEFAULT 0,
    ebs_volume_id TEXT,
    last_snapshot_at TIMESTAMP WITH TIME ZONE,
    operation_id UUID,  -- Current operation ID (for create/delete operations)
    operation_status TEXT,  -- pending, in_progress, completed, failed
    operation_error TEXT,  -- Error message if operation failed
    latest_snapshot_content_s3 TEXT,  -- S3 path to latest snapshot content (ls -R output)
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(user_id, disk_name)
);

-- Create indexes for efficient lookups
CREATE INDEX IF NOT EXISTS idx_disks_user_id ON disks (user_id);
CREATE INDEX IF NOT EXISTS idx_disks_in_use ON disks (in_use) WHERE in_use = true;
CREATE INDEX IF NOT EXISTS idx_disks_is_deleted ON disks (is_deleted) WHERE is_deleted = true;
CREATE INDEX IF NOT EXISTS idx_disks_operation_id ON disks (operation_id) WHERE operation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_disks_reservation_id ON disks (reservation_id) WHERE reservation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_disks_delete_date ON disks (delete_date) WHERE delete_date IS NOT NULL;

-- Function to update last_updated timestamp
CREATE OR REPLACE FUNCTION update_disks_last_updated_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger to call the function before update
DROP TRIGGER IF EXISTS update_disks_last_updated ON disks;
CREATE TRIGGER update_disks_last_updated
BEFORE UPDATE ON disks
FOR EACH ROW
EXECUTE FUNCTION update_disks_last_updated_column();

-- Comments for documentation
COMMENT ON TABLE disks IS 'Persistent disk storage metadata for GPU dev environments';
COMMENT ON COLUMN disks.disk_id IS 'Unique identifier for the disk';
COMMENT ON COLUMN disks.disk_name IS 'User-provided name for the disk';
COMMENT ON COLUMN disks.user_id IS 'Email/ID of the disk owner';
COMMENT ON COLUMN disks.size_gb IS 'Disk size in gigabytes';
COMMENT ON COLUMN disks.in_use IS 'Whether disk is currently attached to a reservation';
COMMENT ON COLUMN disks.reservation_id IS 'ID of the reservation currently using this disk';
COMMENT ON COLUMN disks.is_backing_up IS 'Whether disk is currently being backed up';
COMMENT ON COLUMN disks.is_deleted IS 'Whether disk is marked for deletion (soft delete)';
COMMENT ON COLUMN disks.delete_date IS 'Date when disk will be permanently deleted';
COMMENT ON COLUMN disks.operation_id IS 'ID of the current operation (create/delete)';
COMMENT ON COLUMN disks.operation_status IS 'Status of the current operation';

