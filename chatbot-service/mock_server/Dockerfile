# BEGIN BARC BASE IMAGE
FROM ubuntu:24.04
# END BARC BASE IMAGE

# BEGIN BARC APT SOURCES
# Public Ubuntu sources from the base image remain enabled.
# END BARC APT SOURCES

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    && rm -rf /var/lib/apt/lists/*

COPY server.py /app/server.py

EXPOSE 9000 9001 9003

CMD ["python3", "/app/server.py", "--chat-port", "9000", "--embed-port", "9001", "--rerank-port", "9003"]
