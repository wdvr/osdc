-- Add Real-time Availability Tracking to gpu_types Table
-- Extends gpu_types with dynamic availability metrics from Kubernetes
-- Replaces DynamoDB table used by availability_updater Lambda

-- Add availability columns to gpu_types table
ALTER TABLE gpu_types 
  ADD COLUMN IF NOT EXISTS available_gpus INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS max_reservable INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS full_nodes_available INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS running_instances INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS desired_capacity INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_availability_update TIMESTAMP WITH TIME ZONE,
  ADD COLUMN IF NOT EXISTS last_availability_updated_by VARCHAR(100);

-- Add index for querying available GPU types
CREATE INDEX IF NOT EXISTS idx_gpu_types_available_gpus
    ON gpu_types(available_gpus)
    WHERE is_active = true AND available_gpus > 0;

-- Add index for last availability update
CREATE INDEX IF NOT EXISTS idx_gpu_types_availability_update
    ON gpu_types(last_availability_update DESC)
    WHERE is_active = true;

-- Add comments for new columns
COMMENT ON COLUMN gpu_types.available_gpus IS 'Real-time schedulable GPUs from K8s API (updated every 5min by availability-updater)';
COMMENT ON COLUMN gpu_types.max_reservable IS 'Maximum GPUs that can be reserved in a single reservation (multinode aware)';
COMMENT ON COLUMN gpu_types.full_nodes_available IS 'Number of nodes with all GPUs free';
COMMENT ON COLUMN gpu_types.running_instances IS 'Count of InService ASG instances (from AWS or K8s node count)';
COMMENT ON COLUMN gpu_types.desired_capacity IS 'Total desired capacity across all ASGs for this GPU type';
COMMENT ON COLUMN gpu_types.last_availability_update IS 'Timestamp of last availability update from availability-updater CronJob';
COMMENT ON COLUMN gpu_types.last_availability_updated_by IS 'Pod/service that performed the update (e.g., availability-updater-cronjob-xyz)';

