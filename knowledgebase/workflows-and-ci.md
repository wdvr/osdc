# Workflows and CI

## GitHub Actions

### PyPI Publish (`publish.yml`)

- **File**: `/.github/workflows/publish.yml`
- **Trigger**: Push of `v*` tags (e.g., `v0.3.9`)
- **Runner**: `ubuntu-latest`
- **Environment**: `pypi` (GitHub environment for trusted publishers)
- **Permissions**: `id-token: write`, `attestations: write`, `contents: read`

**Steps**:
1. Checkout code
2. Install `uv` (Python package manager) with Python 3.13
3. **Version verification**: Extracts version from `pyproject.toml`, compares with git tag. Fails if mismatch.
4. Build package: `uv build`
5. Generate build attestations via `actions/attest-build-provenance@v2`
6. Publish to PyPI via `pypa/gh-action-pypi-publish@release/v1` (trusted publisher, no API token needed)

### No Gitlinks (`no-gitlinks.yml`)

- **File**: `/.github/workflows/no-gitlinks.yml`
- **Triggers**: Pull requests + push to `main`
- **Purpose**: Ensures no git submodule links (mode 160000) are tracked
- **Check**: `git ls-files -s | awk "$1 == 160000 {print}"`

## Deployment Process

### CLI Deployment

1. Update version in `/pyproject.toml`
2. Update `LAMBDA_VERSION` in `/terraform-gpu-devservers/lambda.tf` (should match)
3. Commit and push
4. Create git tag: `git tag v0.3.9`
5. Push tag: `git push origin v0.3.9`
6. GitHub Actions builds and publishes to PyPI
7. Users install via: `pip install --upgrade gpu-dev`

### Infrastructure Deployment

1. Make changes to Terraform files
2. Run `tf plan` to verify changes
3. User runs `tf apply` (never automated, never by agent)
4. For Lambda changes: build is triggered automatically by `null_resource` on file changes

### Docker Image Deployment

1. Modify `/terraform-gpu-devservers/docker/Dockerfile`
2. Run `tf apply` -- the `null_resource` in `ecr.tf` detects changes and:
   - Builds `linux/amd64` image
   - Tags with content hash + `latest`
   - Pushes to ECR
   - Restarts prepuller DaemonSet
3. New pods get the updated image; existing pods are unaffected

### Lambda Deployment

Lambda code is built and deployed via Terraform:
1. `null_resource` in `lambda.tf` detects changes to `.py` or `requirements.txt` files
2. Runs `pip install -r requirements.txt` for linux/x86_64
3. Copies `shared/` directory
4. Creates zip archive
5. `tf apply` uploads new zip to Lambda

### SSH Proxy Deployment

1. Modify `/terraform-gpu-devservers/ssh-proxy/proxy.py`
2. `tf apply` rebuilds Docker image, pushes to ECR
3. ECS service performs rolling update

## Workspace Management

Two Terraform workspaces:

| Workspace | Region | Domain | Script |
|-----------|--------|--------|--------|
| `default` (test) | us-west-1 | test.devservers.io | `tf workspace select default` |
| `prod` | us-east-2 | devservers.io | `tf workspace select prod` |

Helper script: `/terraform-gpu-devservers/switch-to.sh`

Deploy to test first, then prod:
```bash
# Test
tf workspace select default
tf plan
tf apply

# Prod
tf workspace select prod
tf plan
tf apply
```

## Version Coordination

The CLI version and Lambda version must be coordinated:

| Location | Variable | Current |
|----------|----------|---------|
| `/pyproject.toml` | `version` | 0.3.9 |
| `/terraform-gpu-devservers/lambda.tf` | `LAMBDA_VERSION` env var | 0.3.9 |
| `/terraform-gpu-devservers/lambda.tf` | `MIN_CLI_VERSION` env var | 0.3.8 |

The Lambda rejects CLI versions below `MIN_CLI_VERSION` with an upgrade prompt.
