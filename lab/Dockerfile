# lab/Dockerfile — the practice target.
#
# Deliberately vulnerable-looking and deliberately fake. It serves the synthetic
# fixture corpus as a marketplace and a forum so the collectors have a real
# hidden service to crawl instead of a mock. Nothing here is a real site and
# nothing it serves describes a real person.
#
# Slim by design: stdlib HTTP server, no framework, one dependency
# (`cryptography`) and only because the planted misconfiguration is a TLS
# certificate whose SAN names a clearnet host in fixtures/clearnet_obs/.

FROM python:3.11-slim

WORKDIR /lab

RUN pip install --no-cache-dir cryptography==46.0.7

# The corpus is the fixture corpus. Copying it rather than mounting it means
# the image is self-contained and the crawl is reproducible from the tag alone.
COPY fixtures/ /lab/fixtures/
COPY lab/app.py /lab/lab/app.py

# `serve()` writes the self-signed cert here at boot.
RUN mkdir -p /certs

EXPOSE 8080 8443

CMD ["python", "-u", "lab/app.py", "--http", "8080", "--https", "8443"]
