# A device stand-in for deploy/check: Valkey 8.1.4 for the replica (the VM runs Debian's
# 8.1.1, same series), FUSE and the patched JuiceFS, on Debian trixie like the VM.
FROM valkey/valkey:8.1.4
ARG TARGETARCH
RUN apt-get update \
 && apt-get install -y --no-install-recommends fuse3 ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=juicefs juicefs-linux-${TARGETARCH} SHA256SUMS /tmp/juicefs/
RUN cd /tmp/juicefs && grep " juicefs-linux-${TARGETARCH}$" SHA256SUMS | sha256sum -c - \
 && install -m 0755 "juicefs-linux-${TARGETARCH}" /usr/local/bin/juicefs && rm -rf /tmp/juicefs
ENTRYPOINT []
