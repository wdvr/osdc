"""
Configuration for Internal Portal API
Note: This file should not be exposed publicly!
"""

import os

# Flask settings
DEBUG = os.environ.get("DEBUG_MODE", "disabled") == "enabled"
SECRET_KEY = os.environ.get("FLASK_SECRET", "dev-secret-change-in-prod")

# Internal service configuration
LAYER2_ENDPOINT = os.environ.get("LAYER2_ENDPOINT", "http://layer2-shell:9000")
LAYER2_TOKEN = os.environ.get("INTERNAL_API_KEY", "")

# Database (not actually used, but leaked)
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Feature flags
ENABLE_DEBUG_ENDPOINT = True  # TODO: disable in production
ENABLE_BACKUP_ENDPOINT = True  # TODO: disable in production
