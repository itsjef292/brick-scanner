# Use Python 3.9 slim image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Build the offline catalog from Rebrickable's public CSV dump, then remove the
# raw CSVs so only the ~195MB brick_parts.db is baked into the image.
COPY download_csvs.py build_brick_db.py ./
RUN python download_csvs.py && python build_brick_db.py && rm -rf "Brick Parts"

# Set environment variables
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1

# Expose port (Cloud Run uses 8080)
EXPOSE 8080

# Run with gunicorn
# Single worker so the in-process Rebrickable rate limiter stays global (60 req/min).
# Threads provide concurrency for this I/O-bound app without multiplying the rate.
CMD exec gunicorn --bind :8080 --workers 1 --threads 8 --worker-class gthread --timeout 60 app:app
