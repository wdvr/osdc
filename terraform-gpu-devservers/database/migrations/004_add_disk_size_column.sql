-- Migration: Add disk_size column to disks table
-- This column stores human-readable disk usage from du -sh (e.g., "1.2G")

-- Check if column exists and add it if it doesn't
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'disks' 
        AND column_name = 'disk_size'
    ) THEN
        ALTER TABLE disks ADD COLUMN disk_size TEXT;
        RAISE NOTICE 'Added disk_size column to disks table';
    ELSE
        RAISE NOTICE 'disk_size column already exists';
    END IF;
END $$;

-- Add comment for documentation
COMMENT ON COLUMN disks.disk_size IS 'Human-readable disk usage from du -sh (e.g., "1.2G")';

