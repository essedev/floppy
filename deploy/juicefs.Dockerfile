# syntax=docker/dockerfile:1
# The patched juicefs (spike/juicefs-replica's `out` stage, the `juicefs-out` context) laid
# out as server/Dockerfile's `juicefs` context expects it: juicefs-linux-<arch> and its
# SHA256SUMS, the same shape spike/juicefs-replica/build.sh writes to dist/.
FROM juicefs-out AS bin

FROM busybox:1.37 AS layout
ARG TARGETARCH
COPY --from=bin /juicefs /dist/juicefs-linux-${TARGETARCH}
RUN cd /dist && sha256sum "juicefs-linux-${TARGETARCH}" > SHA256SUMS

FROM scratch
COPY --from=layout /dist/ /
