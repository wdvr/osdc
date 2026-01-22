-- Migration: Add Expiry Service Tracking Columns
-- Date: 2026-01-21
-- Purpose: Add columns needed by reservation-expiry-service for OOM tracking, warning tracking, and terminal state timestamps
-- Related: EXPIRY_SERVICE_CODE_REVIEW.md Issues #1, #2, #3

-- ============================================================================
-- Add OOM (Out of Memory) Tracking Columns
-- ============================================================================

-- Track OOM events for reservations
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS oom_count INTEGER DEFAULT 0;
COMMENT ON COLUMN reservations.oom_count IS 'Number of OOM (Out of Memory) events detected for this reservation';

ALTER TABLE reservations ADD COLUMN IF NOT EXISTS last_oom_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN reservations.last_oom_at IS 'Timestamp of the most recent OOM event';

ALTER TABLE reservations ADD COLUMN IF NOT EXISTS oom_container VARCHAR(255);
COMMENT ON COLUMN reservations.oom_container IS 'Name of the container that experienced the most recent OOM event';

-- ============================================================================
-- Add Warning Tracking Columns
-- ============================================================================

-- Track which expiry warnings have been sent to users
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS warnings_sent JSONB DEFAULT '{}'::jsonb;
COMMENT ON COLUMN reservations.warnings_sent IS 'JSON object tracking which warning levels have been sent (e.g., {"30min_warning_sent": true, "15min_warning_sent": true})';

ALTER TABLE reservations ADD COLUMN IF NOT EXISTS last_warning_time BIGINT;
COMMENT ON COLUMN reservations.last_warning_time IS 'Unix timestamp of the most recent warning sent to the user';

-- ============================================================================
-- Add Terminal State Timestamp Columns
-- ============================================================================

-- Track when reservations entered terminal states
ALTER TABLE reservations ADD COLUMN IF NOT EXISTS failed_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN reservations.failed_at IS 'Timestamp when reservation was marked as failed';

ALTER TABLE reservations ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN reservations.cancelled_at IS 'Timestamp when reservation was cancelled by user or system';

ALTER TABLE reservations ADD COLUMN IF NOT EXISTS reservation_ended TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN reservations.reservation_ended IS 'Timestamp when reservation ended (expired, failed, or cancelled) - used for lifecycle tracking';

-- Note: expired_at already exists in schema (from 002_reservations.sql line 15)

-- ============================================================================
-- Add Indexes for Performance
-- ============================================================================

-- Index for finding reservations that expired at specific times
CREATE INDEX IF NOT EXISTS idx_reservations_expires_at 
    ON reservations(expires_at) 
    WHERE expires_at IS NOT NULL;

-- Index for finding failed reservations
CREATE INDEX IF NOT EXISTS idx_reservations_failed_at 
    ON reservations(failed_at) 
    WHERE failed_at IS NOT NULL;

-- Index for finding cancelled reservations
CREATE INDEX IF NOT EXISTS idx_reservations_cancelled_at 
    ON reservations(cancelled_at) 
    WHERE cancelled_at IS NOT NULL;

-- Index for finding reservations by end time (for cleanup queries)
CREATE INDEX IF NOT EXISTS idx_reservations_ended 
    ON reservations(reservation_ended) 
    WHERE reservation_ended IS NOT NULL;

-- Index for finding reservations with OOM events
CREATE INDEX IF NOT EXISTS idx_reservations_oom 
    ON reservations(oom_count) 
    WHERE oom_count > 0;

-- ============================================================================
-- Add Column to Disks Table (Optional - See Review Issue #5)
-- ============================================================================

-- Track when disk was marked for deletion (improves snapshot tagging accuracy)
ALTER TABLE disks ADD COLUMN IF NOT EXISTS marked_deleted_at TIMESTAMP WITH TIME ZONE;
COMMENT ON COLUMN disks.marked_deleted_at IS 'Timestamp when disk was marked for deletion (is_deleted set to true)';

-- Trigger to automatically set marked_deleted_at when is_deleted changes to true
CREATE OR REPLACE FUNCTION set_disk_marked_deleted_at()
RETURNS TRIGGER AS $BODY$
BEGIN
    -- Only set marked_deleted_at when is_deleted changes from false to true
    IF NEW.is_deleted = TRUE AND (OLD.is_deleted = FALSE OR OLD.is_deleted IS NULL) THEN
        NEW.marked_deleted_at = NOW();
    END IF;
    
    -- Clear marked_deleted_at when is_deleted changes back to false
    IF NEW.is_deleted = FALSE AND OLD.is_deleted = TRUE THEN
        NEW.marked_deleted_at = NULL;
    END IF;
    
    RETURN NEW;
END;
$BODY$ LANGUAGE plpgsql;

-- Create or replace trigger for disks
DROP TRIGGER IF EXISTS trigger_disk_marked_deleted ON disks;

CREATE TRIGGER trigger_disk_marked_deleted
    BEFORE UPDATE ON disks
    FOR EACH ROW
    EXECUTE FUNCTION set_disk_marked_deleted_at();

-- ============================================================================
-- Verification Queries (Run these after migration to verify)
-- ============================================================================

-- Verify all new columns exist
-- SELECT column_name, data_type, is_nullable, column_default
-- FROM information_schema.columns
-- WHERE table_name = 'reservations'
--   AND column_name IN ('oom_count', 'last_oom_at', 'oom_container', 
--                       'warnings_sent', 'last_warning_time',
--                       'failed_at', 'cancelled_at', 'reservation_ended')
-- ORDER BY column_name;

-- Verify disk column exists
-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'disks'
--   AND column_name = 'marked_deleted_at';

-- Verify indexes created
-- SELECT indexname, indexdef
-- FROM pg_indexes
-- WHERE tablename = 'reservations'
--   AND indexname LIKE 'idx_reservations_%'
-- ORDER BY indexname;

-- ============================================================================
-- Rollback Script (If Needed)
-- ============================================================================

-- To rollback this migration (use with caution):
-- DROP INDEX IF EXISTS idx_reservations_expired_at;
-- DROP INDEX IF EXISTS idx_reservations_failed_at;
-- DROP INDEX IF EXISTS idx_reservations_cancelled_at;
-- DROP INDEX IF EXISTS idx_reservations_ended;
-- DROP INDEX IF EXISTS idx_reservations_oom;
-- DROP TRIGGER IF EXISTS trigger_disk_marked_deleted ON disks;
-- DROP FUNCTION IF EXISTS set_disk_marked_deleted_at();
-- ALTER TABLE disks DROP COLUMN IF EXISTS marked_deleted_at;
-- ALTER TABLE reservations DROP COLUMN IF EXISTS reservation_ended;
-- ALTER TABLE reservations DROP COLUMN IF EXISTS cancelled_at;
-- ALTER TABLE reservations DROP COLUMN IF EXISTS failed_at;
-- ALTER TABLE reservations DROP COLUMN IF EXISTS last_warning_time;
-- ALTER TABLE reservations DROP COLUMN IF EXISTS warnings_sent;
-- ALTER TABLE reservations DROP COLUMN IF EXISTS oom_container;
-- ALTER TABLE reservations DROP COLUMN IF EXISTS last_oom_at;
-- ALTER TABLE reservations DROP COLUMN IF EXISTS oom_count;


