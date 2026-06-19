# Use the official Playwright Python image with Chromium pre-installed
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

# Set working directory
WORKDIR /app

# Copy dependency manifest first (better Docker layer caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Ensure Playwright browsers are installed (base image already has Chromium)
RUN python -m playwright install chromium

# Expose the port that the app runs on
# Cloud platforms inject the actual port via $PORT
ENV PORT=8000
EXPOSE 8000

# Start the FastAPI app with uvicorn
CMD ["sh", "-c", "python -m uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
