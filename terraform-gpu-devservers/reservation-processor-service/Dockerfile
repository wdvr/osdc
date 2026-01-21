FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY reservation-processor-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared utilities from top-level shared directory
COPY shared/ ./shared/

# Copy application code
COPY reservation-processor-service/processor/ ./processor/

# Create non-root user
RUN useradd -m -u 1000 processoruser && \
    chown -R processoruser:processoruser /app

USER processoruser

# Run the processor
CMD ["python3", "-u", "processor/main.py"]

