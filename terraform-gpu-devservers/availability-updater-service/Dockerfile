FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY availability-updater-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared utilities from top-level shared directory
COPY shared/ ./shared/

# Copy application code
COPY availability-updater-service/updater/ ./updater/

# Create non-root user
RUN useradd -m -u 1000 updateruser && \
    chown -R updateruser:updateruser /app

USER updateruser

# Set PYTHONPATH so updater module can be imported
ENV PYTHONPATH=/app:$PYTHONPATH

# Default command runs the updater service
CMD ["python3", "-u", "-m", "updater.main"]

