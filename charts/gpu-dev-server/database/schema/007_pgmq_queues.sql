-- PGMQ Queues Schema
-- Creates message queues for asynchronous job processing

-- Ensure PGMQ extension is installed (should already be done in init script)
-- CREATE EXTENSION IF NOT EXISTS pgmq;

-- Create reservation queue for GPU reservation requests
-- This queue handles: reserve, cancel, and other reservation operations
SELECT pgmq.create('gpu_reservations');

-- Create disk operations queue for disk management
-- This queue handles: snapshot, delete, backup operations
SELECT pgmq.create('disk_operations');

-- Verify queues were created
DO $$
DECLARE
    queue_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO queue_count
    FROM pgmq.list_queues()
    WHERE queue_name IN ('gpu_reservations', 'disk_operations');
    
    IF queue_count != 2 THEN
        RAISE EXCEPTION 'Failed to create PGMQ queues. Expected 2, got %', queue_count;
    END IF;
    
    RAISE NOTICE 'Successfully created % PGMQ queues', queue_count;
END $$;

