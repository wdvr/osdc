-- Cloud-agnostic disk storage columns
-- pvc_name: K8s PersistentVolumeClaim name (non-AWS)
-- volume_snapshot_name: K8s VolumeSnapshot name (non-AWS)
ALTER TABLE disks ADD COLUMN IF NOT EXISTS pvc_name TEXT;
ALTER TABLE disks ADD COLUMN IF NOT EXISTS volume_snapshot_name TEXT;
