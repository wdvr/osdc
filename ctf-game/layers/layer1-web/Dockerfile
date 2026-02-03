FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install flask gunicorn

# Copy application
COPY app.py /app/
COPY config.py /app/

# Create fake "package list" for recon
RUN mkdir -p /app/static
COPY packages.txt /app/static/

# Set environment variables (some "leaked" intentionally)
ENV FLASK_ENV=development
ENV DEBUG_MODE=enabled
ENV INTERNAL_API_KEY=layer2-access-token-7f3d9a2b
ENV DATABASE_URL=postgres://admin:supersecret@internal-db:5432/app
ENV LAYER2_ENDPOINT=http://layer2-shell:9000

# Flag for this layer
RUN mkdir -p /flags && echo "FLAG{1nf0_d1scl0sur3_1s_r34l}" > /flags/flag1.txt

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
