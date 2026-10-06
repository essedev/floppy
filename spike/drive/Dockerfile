# The "Linux box" that hosts the JuiceFS mount and shares it to macOS over SMB,
# so the Mac needs no FUSE kernel extension.
FROM debian:bookworm-slim

ARG JUICEFS_VERSION=1.4.1
ARG TARGETARCH
RUN apt-get update \
 && apt-get install -y --no-install-recommends samba samba-vfs-modules fuse3 ca-certificates curl \
 && curl -fsSL "https://github.com/juicedata/juicefs/releases/download/v${JUICEFS_VERSION}/juicefs-${JUICEFS_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin juicefs \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY smb.conf /etc/samba/smb.conf
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
