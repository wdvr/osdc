-- GPU Types Schema
-- This table stores centralized GPU configuration

-- Create gpu_types table for centralized GPU configuration
CREATE TABLE IF NOT EXISTS gpu_types (
    gpu_type VARCHAR(50) PRIMARY KEY,
    instance_type VARCHAR(100) NOT NULL,
    max_gpus INTEGER NOT NULL,
    cpus INTEGER NOT NULL,
    memory_gb INTEGER NOT NULL,
    total_cluster_gpus INTEGER DEFAULT 0,
    max_per_node INTEGER,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    description TEXT
);

-- Create index for active GPU types
CREATE INDEX IF NOT EXISTS idx_gpu_types_active
    ON gpu_types(is_active)
    WHERE is_active = true;

-- Create trigger function for gpu_types table
CREATE OR REPLACE FUNCTION update_gpu_types_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create trigger for gpu_types table
DROP TRIGGER IF EXISTS update_gpu_types_updated_at ON gpu_types;

CREATE TRIGGER update_gpu_types_updated_at
    BEFORE UPDATE ON gpu_types
    FOR EACH ROW
    EXECUTE FUNCTION update_gpu_types_updated_at_column();

