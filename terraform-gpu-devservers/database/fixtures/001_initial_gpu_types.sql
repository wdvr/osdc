-- Initial GPU Types Configuration
-- This populates the gpu_types table with the default GPU configurations

-- Use INSERT ... ON CONFLICT to make this idempotent
-- If a GPU type already exists, update it with the latest values

INSERT INTO gpu_types (
    gpu_type, instance_type, max_gpus, cpus, memory_gb,
    total_cluster_gpus, max_per_node, is_active, description
) VALUES
    ('t4', 'g4dn.12xlarge', 4, 48, 192, 8, 4, true,
     'NVIDIA T4 - Entry-level GPU for inference and light training'),
    
    ('t4-small', 'g4dn.2xlarge', 1, 8, 32, 1, 1, true,
     'NVIDIA T4 - Small instance for testing'),
    
    ('l4', 'g6.12xlarge', 4, 48, 192, 4, 4, true,
     'NVIDIA L4 - Efficient GPU for inference and training'),
    
    ('a10g', 'g5.12xlarge', 4, 48, 192, 4, 4, true,
     'NVIDIA A10G - Mid-range GPU for training and inference'),
    
    ('a100', 'p4d.24xlarge', 8, 96, 1152, 16, 8, true,
     'NVIDIA A100 - High-performance GPU for large-scale training'),
    
    ('h100', 'p5.48xlarge', 8, 192, 2048, 16, 8, true,
     'NVIDIA H100 - Top-tier GPU for AI training and HPC'),
    
    ('h200', 'p5e.48xlarge', 8, 192, 2048, 16, 8, true,
     'NVIDIA H200 - Latest generation with increased memory'),
    
    ('b200', 'p6-b200.48xlarge', 8, 192, 2048, 16, 8, true,
     'NVIDIA B200 - Next-generation Blackwell architecture'),
    
    ('cpu-x86', 'c7i.8xlarge', 0, 32, 64, 0, 0, true,
     'CPU-only instance (x86, Intel)'),
    
    ('cpu-arm', 'c7g.8xlarge', 0, 32, 64, 0, 0, true,
     'CPU-only instance (ARM, Graviton)')

ON CONFLICT (gpu_type) DO UPDATE SET
    instance_type = EXCLUDED.instance_type,
    max_gpus = EXCLUDED.max_gpus,
    cpus = EXCLUDED.cpus,
    memory_gb = EXCLUDED.memory_gb,
    total_cluster_gpus = EXCLUDED.total_cluster_gpus,
    max_per_node = EXCLUDED.max_per_node,
    is_active = EXCLUDED.is_active,
    description = EXCLUDED.description,
    updated_at = NOW();

