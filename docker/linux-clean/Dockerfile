# A "clean" Linux machine: no Python, only the FUSE runtime. Used to smoke-test the packaged binary.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends fuse3 ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /work
