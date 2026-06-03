FROM python:3.11-slim

# Avoid writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE 1
# Prevent buffering stdout/stderr
ENV PYTHONUNBUFFERED 1

WORKDIR /app

# Create configuration and downloads directories
RUN mkdir -p /config /downloads

# Copy requirements
COPY requirements.txt .

# Filter out PyQt5 dependencies from requirements.txt to keep the container headless and tiny
RUN sed -i '/PyQt5/d' requirements.txt && \
    sed -i '/pytest/d' requirements.txt && \
    sed -i '/pyinstaller/d' requirements.txt

# Install standard dependencies + FastAPI/Uvicorn
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir fastapi uvicorn pydantic

# Copy source and web assets
COPY src/ ./src/
COPY web/ ./web/
COPY favicon-16.png .

# Env variables for paths
ENV ARCHIMMICH_CONFIG_DIR=/config
ENV ARCHIMMICH_DOWNLOADS_DIR=/downloads

# Expose FastAPI default port
EXPOSE 8000

# Run FastAPI app
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
