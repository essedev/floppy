# The "Linux box" that hosts the JuiceFS mount and shares it to macOS over SMB,
# so the Mac needs no FUSE kernel extension.
#
# JUICEFS_SOURCE picks the juicefs binary: "upstream" downloads the release,
# "replica" takes the build with reads from a local metadata replica
# (spike/juicefs-replica, passed as the "replica" build context).
ARG JUICEFS_SOURCE=upstream

FROM debian:bookworm-slim AS base
RUN apt-get update \
 && apt-get install -y --no-install-recommends samba samba-vfs-modules fuse3 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

FROM base AS juicefs-upstream
ARG JUICEFS_VERSION=1.4.1
ARG TARGETARCH
RUN curl -fsSL "https://github.com/juicedata/juicefs/releases/download/v${JUICEFS_VERSION}/juicefs-${JUICEFS_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz -C /usr/local/bin juicefs

FROM base AS juicefs-replica
ARG TARGETARCH
COPY --from=replica --chmod=0755 juicefs-linux-${TARGETARCH} /usr/local/bin/juicefs

FROM juicefs-${JUICEFS_SOURCE}
RUN apt-get purge -y curl && apt-get autoremove -y
COPY smb.conf /etc/samba/smb.conf
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
