# BEGIN BARC BASE IMAGE
FROM ubuntu:24.04
# END BARC BASE IMAGE

ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY server.py ./

EXPOSE 8093

CMD ["python3", "server.py"]
