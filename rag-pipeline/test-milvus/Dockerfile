FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# BARC-specific: Clear out default public repositories
RUN rm -f /etc/apt/sources.list.d/*.sources /etc/apt/sources.list

# BARC-specific: Overwrite sources.list with local BARC repository
RUN echo "deb http://osrepo.barc.gov.in/ubuntu/ noble main restricted universe multiverse\n\
deb http://osrepo.barc.gov.in/ubuntu/ noble-updates main restricted universe multiverse\n\
deb http://osrepo.barc.gov.in/ubuntu/ noble-security main restricted universe multiverse" > /etc/apt/sources.list

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    build-essential \
    python3 \
    python3-dev \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install pymilvus using the BARC intranet PyPI mirror
# (--break-system-packages is required in Ubuntu 24.04 when installing globally without a venv)
RUN pip install --no-cache-dir --break-system-packages \
    --index-url http://osrepo.barc.gov.in/python-pypi/simple \
    --trusted-host osrepo.barc.gov.in \
    pymilvus

WORKDIR /app

# Copy only the single Python script
COPY test_milvus.py ./
RUN chmod +x test_milvus.py

# Run the script directly with Python
CMD ["python3", "test_milvus.py"]
