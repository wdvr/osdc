FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY reservation-expiry-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared utilities from top-level shared directory
COPY shared/ ./shared/

# Copy application code
COPY reservation-expiry-service/expiry/ ./expiry/

# Create non-root user
RUN useradd -m -u 1000 processoruser && \
    chown -R processoruser:processoruser /app

USER processoruser

# Set PYTHONPATH so expiry module can be imported
ENV PYTHONPATH=/app:$PYTHONPATH

# Default command runs the expiry service
CMD ["python3", "-u", "-m", "expiry.main"]

