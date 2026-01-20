# GPU Dev Server Analytics

Admin tools for generating usage statistics and dashboards.

## Setup

```bash
cd admin
pip install -r requirements.txt
```

## Usage

Generate analytics dashboard:

```bash
python generate_stats.py
```

This will:

1. Fetch all reservation data from PostgreSQL
2. Generate statistics including:
   - Total number of reservations ever
   - Number of unique users
   - Daily active reservations (last 8 weeks)
   - Hourly GPU usage (last 8 weeks)
   - GPU type distribution
   - Top 10 users
3. Create visualizations (PNG files)
4. Generate an HTML dashboard

## Output

All output is saved to `admin/output/`:

- `dashboard.html` - Main dashboard (open in browser)
- `daily_active_reservations.png` - Daily active reservation chart
- `hourly_gpu_usage.png` - Hourly GPU usage chart
- `gpu_type_distribution.png` - GPU type breakdown
- `top_users.png` - Top users by reservation count

## Configuration

Set these environment variables:

- `POSTGRES_HOST` - PostgreSQL hostname (default: postgres-primary.gpu-controlplane.svc.cluster.local)
- `POSTGRES_PORT` - PostgreSQL port (default: 5432)
- `POSTGRES_USER` - PostgreSQL username (default: gpudev)
- `POSTGRES_PASSWORD` - PostgreSQL password (required)
- `POSTGRES_DB` - PostgreSQL database name (default: gpudev)

### Connecting to the Database

**Option 1: Port forward (recommended for local development)**
```bash
# Forward PostgreSQL port
kubectl port-forward -n gpu-controlplane svc/postgres-primary 5432:5432

# Get password
export POSTGRES_PASSWORD=$(kubectl get secret -n gpu-controlplane postgres-credentials \
  -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)

# Run analytics
python generate_stats.py
```

**Option 2: Database URL**
```bash
export DATABASE_URL="postgresql://gpudev:PASSWORD@postgres-primary.gpu-controlplane.svc.cluster.local:5432/gpudev"
python generate_stats.py
```
