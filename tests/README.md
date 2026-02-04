# ODC Test Suite

Comprehensive tests for the ODC (GPU Dev Servers) system.

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures and AWS mocking
├── requirements-test.txt    # Test dependencies
├── unit/                    # Unit tests (mocked, fast)
│   ├── cli/                 # CLI module tests
│   │   ├── test_config.py
│   │   ├── test_auth.py
│   │   ├── test_disks.py
│   │   └── test_reservations.py
│   └── lambda/              # Lambda function tests
│       └── test_reservation_processor.py
├── e2e/                     # End-to-end tests (real AWS)
│   ├── test_reservation_flow.py
│   └── test_cli_commands.py
└── fixtures/                # Test data factories
```

## Running Tests

### Prerequisites

```bash
# Install test dependencies
pip install -r tests/requirements-test.txt

# Or install with optional test group
pip install -e ".[test]"
```

### Unit Tests (Fast, Mocked)

Unit tests use moto to mock AWS services. No AWS credentials required.

```bash
# Run all unit tests
pytest tests/unit/ -v

# Run with coverage
pytest tests/unit/ --cov=cli-tools/gpu-dev-cli --cov-report=html

# Run specific test file
pytest tests/unit/cli/test_config.py -v

# Run specific test class
pytest tests/unit/cli/test_config.py::TestConfigInit -v

# Run specific test
pytest tests/unit/cli/test_config.py::TestConfigInit::test_config_creates_default_file_if_missing -v
```

### E2E Tests (Real AWS Dev Cluster)

E2E tests run against the actual dev cluster in us-west-1.

**Requirements:**
- AWS credentials with gpu-dev access
- GitHub username configured: `gpu-dev config set github_user <your-username>`
- Test environment enabled: `gpu-dev config environment test`

```bash
# Run E2E tests
RUN_E2E_TESTS=1 pytest tests/e2e/ -v

# Run with specific GitHub user
RUN_E2E_TESTS=1 E2E_GITHUB_USER=myuser pytest tests/e2e/ -v

# Skip slow tests
RUN_E2E_TESTS=1 pytest tests/e2e/ -v -m "not slow"

# Run only fast E2E tests (no actual reservations)
RUN_E2E_TESTS=1 pytest tests/e2e/test_cli_commands.py -v
```

### All Tests

```bash
# Run everything (unit only by default)
pytest

# Run with markers
pytest -m unit      # Only unit tests
pytest -m e2e       # Only E2E tests (requires RUN_E2E_TESTS=1)
pytest -m "not slow"  # Skip slow tests
```

## Test Markers

- `@pytest.mark.unit` - Unit tests (fast, mocked)
- `@pytest.mark.e2e` - End-to-end tests (require real AWS)
- `@pytest.mark.slow` - Slow tests (can be skipped)

## Writing Tests

### Unit Tests

Use moto for AWS mocking:

```python
from moto import mock_aws
import boto3

@mock_aws
def test_something(aws_credentials):
    # Create mock resources
    dynamodb = boto3.resource("dynamodb", region_name="us-west-1")
    # ... test code
```

Use fixtures from conftest.py:

```python
def test_with_fixtures(dynamodb_mock, reservation_factory):
    # dynamodb_mock provides mock DynamoDB tables
    # reservation_factory creates test reservation data
    reservation = reservation_factory.create(
        user_id="test-user",
        gpu_count=2,
        gpu_type="t4",
    )
```

### E2E Tests

Use cleanup fixtures to avoid resource leaks:

```python
@pytest.mark.e2e
def test_reservation(cleanup_reservations):
    # Create reservation
    result = subprocess.run(["gpu-dev", "reserve", ...])

    # Track for cleanup
    cleanup_reservations.append(reservation_id)
```

## Coverage

Generate coverage report:

```bash
# HTML report
pytest tests/unit/ --cov=cli-tools/gpu-dev-cli --cov-report=html
open htmlcov/index.html

# Terminal report
pytest tests/unit/ --cov=cli-tools/gpu-dev-cli --cov-report=term-missing
```

## CI Integration

For GitHub Actions:

```yaml
- name: Run Unit Tests
  run: pytest tests/unit/ -v --cov

- name: Run E2E Tests
  if: github.event_name == 'schedule'  # Nightly only
  env:
    RUN_E2E_TESTS: "1"
    AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
    AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
  run: pytest tests/e2e/ -v -m "not slow"
```

## Troubleshooting

### "No module named 'gpu_dev_cli'"

Install the package in development mode:

```bash
pip install -e .
```

### "AWS credentials not found" in unit tests

Unit tests shouldn't need real credentials - check that you're using `@mock_aws` decorator.

### E2E tests timing out

- Check your AWS credentials are valid
- Verify test environment: `gpu-dev config environment test`
- Ensure dev cluster is running

### Tests pass locally but fail in CI

- Check environment variables are set
- Verify CI has access to AWS (for E2E only)
- Check for timezone-sensitive assertions
