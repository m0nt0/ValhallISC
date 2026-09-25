FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip python3-wxgtk4.0 \
      fuse3 libfuse3-3 xvfb xauth ca-certificates procps psmisc \
 && rm -rf /var/lib/apt/lists/*
# venv with system site-packages so the distro wxPython (built for this python) is visible
RUN python3 -m venv --system-site-packages /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --no-cache-dir httpx defusedxml mfusepy platformdirs keyring pytest pytest-timeout hypothesis
WORKDIR /work
