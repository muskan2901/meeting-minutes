FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

# Install system dependencies including curl for health checks
# Pin to specific versions for security compliance
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git=1:2.47.3-0+deb13u1 \
    curl=8.14.1-2 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ .

# Create outputs directory
RUN mkdir -p outputs

# Create a non-root user and set permissions
RUN useradd -m appuser && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app

USER appuser

# Expose the port the app runs on
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Command to run the application with Uvicorn
# Security Note: 0.0.0.0 binding is required for containerized applications to accept
# connections from outside the container. This is safe when deployed behind ALB/proxy.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
