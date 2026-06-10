# BEGIN BARC BASE IMAGE
FROM ubuntu:24.04
# END BARC BASE IMAGE

ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# BEGIN BARC APT SOURCES
# Public Ubuntu sources from the base image remain enabled.
# END BARC APT SOURCES

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    python3 \
    python3-dev \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# BEGIN BARC PYMILVUS INSTALL
RUN pip install --no-cache-dir --break-system-packages pymilvus
# END BARC PYMILVUS INSTALL

WORKDIR /app

# Copy only the single Python script
COPY test_milvus.py ./
RUN chmod +x test_milvus.py

# Run the script directly with Python
CMD ["python3", "test_milvus.py"]
