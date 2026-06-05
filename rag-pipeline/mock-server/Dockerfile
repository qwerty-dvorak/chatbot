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

WORKDIR /app

# Copy only the Python script
COPY server.py ./
RUN chmod +x server.py

EXPOSE 9000 9001 9002 9003

CMD ["python3", "server.py"]
