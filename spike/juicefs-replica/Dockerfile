# syntax=docker/dockerfile:1
# The patched juicefs binary: JuiceFS v1.4.1 plus 0001-*.patch (reads from a
# local Redis/Valkey replica). Built by build.sh for linux/arm64 and
# linux/amd64; the "out" stage holds only the binary.
#
# Cross-compiled on the build platform with Debian's cross gcc, because the
# SQLite driver needs cgo. The binary links glibc dynamically: built on
# bookworm, it runs on bookworm (the spike drive image) and trixie (the VM).
FROM --platform=$BUILDPLATFORM golang:1.25.14-bookworm AS build
ARG TARGETARCH
ARG JUICEFS_VERSION=v1.4.1
RUN set -eu; \
    if [ "$(dpkg --print-architecture)" != "$TARGETARCH" ]; then \
      case "$TARGETARCH" in \
        amd64) gcc=gcc-x86-64-linux-gnu ;; \
        arm64) gcc=gcc-aarch64-linux-gnu ;; \
        *) echo "unsupported TARGETARCH $TARGETARCH" >&2; exit 1 ;; \
      esac; \
      apt-get update; \
      apt-get install -y --no-install-recommends "$gcc" "libc6-dev-${TARGETARCH}-cross"; \
      rm -rf /var/lib/apt/lists/*; \
    fi
RUN git clone --depth 1 --branch "$JUICEFS_VERSION" https://github.com/juicedata/juicefs.git /src
WORKDIR /src
COPY 0001-*.patch /tmp/patches/
RUN git apply /tmp/patches/0001-*.patch
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    set -eu; \
    if [ "$(dpkg --print-architecture)" = "$TARGETARCH" ]; then cc=gcc; \
    elif [ "$TARGETARCH" = amd64 ]; then cc=x86_64-linux-gnu-gcc; \
    else cc=aarch64-linux-gnu-gcc; fi; \
    pkg=github.com/juicedata/juicefs/pkg/version; \
    CGO_ENABLED=1 CC="$cc" GOOS=linux GOARCH="$TARGETARCH" go build \
      -ldflags "-s -w -X $pkg.revision=$(git rev-parse --short HEAD)-replica -X $pkg.revisionDate=$(git log -1 --format=%cs)" \
      -o /out/juicefs .

FROM scratch AS out
COPY --from=build /out/juicefs /juicefs
