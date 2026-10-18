"""
One connection to a disk's Valkey through Traefik, held for a while, from inside the device
stand-in (so Docker Desktop's port forwarding is not in the path). check.py runs it in the
background and reads the JSON line it writes.

    python3 idle_probe.py quiet <host> <seconds> <out.json>        # AUTH, then silence
    python3 idle_probe.py ping <host> <seconds> <out.json> <every>  # AUTH, then PING every <every> s
    python3 idle_probe.py hello <host> <seconds> <out.json>        # TCP only, no TLS ClientHello

The user and password come from CHECK_DEVICE_USER and CHECK_DEVICE_PW. `quiet` reports when
the connection was closed, or that it still answers a PING at the end; `ping` reports every
PING that failed; `hello` reports when Traefik closed a connection that never started TLS
(the one thing its readTimeout covers on a TCP router's entrypoint).
"""

import json
import os
import socket
import ssl
import sys
import time


def resp(*parts: str) -> bytes:
    out = b"*%d\r\n" % len(parts)
    for p in parts:
        b = p.encode()
        out += b"$%d\r\n%s\r\n" % (len(b), b)
    return out


def main() -> None:
    mode, host, seconds, out = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
    every = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    if mode == "hello":
        raw = socket.create_connection((host, 443), timeout=seconds)
        start = time.monotonic()
        try:
            data = raw.recv(64)
            result = {"mode": mode, "closed_after_s": round(time.monotonic() - start, 1), "closed_with": repr(data)}
        except OSError as error:
            result = {"mode": mode, "closed_after_s": round(time.monotonic() - start, 1), "closed_with": repr(error)}
        with open(out, "w") as f:
            f.write(json.dumps(result))
        return
    ctx = ssl.create_default_context(cafile="/certs/ca.crt")
    sock = ctx.wrap_socket(socket.create_connection((host, 443), timeout=10), server_hostname=host)
    sock.sendall(resp("AUTH", os.environ["CHECK_DEVICE_USER"], os.environ["CHECK_DEVICE_PW"]))
    first = sock.recv(64)
    start = time.monotonic()
    result: dict[str, object] = {"mode": mode, "auth": first.decode(errors="replace").strip()}
    if mode == "quiet":
        sock.settimeout(seconds)
        try:
            data = sock.recv(64)
            result["closed_after_s"] = round(time.monotonic() - start, 1)
            result["closed_with"] = data.decode(errors="replace")
        except TimeoutError:
            sock.settimeout(10)
            try:
                sock.sendall(resp("PING"))
                result["ping_at_end"] = sock.recv(64).decode(errors="replace").strip()
            except OSError as error:
                result["ping_at_end"] = f"error: {error!r}"
        except OSError as error:
            result["closed_after_s"] = round(time.monotonic() - start, 1)
            result["closed_with"] = repr(error)
    else:
        failures, pings = [], 0
        while time.monotonic() - start < seconds:
            time.sleep(every)
            try:
                sock.sendall(resp("PING"))
                answer = sock.recv(64)
                pings += 1
                if answer != b"+PONG\r\n":
                    failures.append(f"{time.monotonic() - start:.0f}s: {answer!r}")
            except OSError as error:
                failures.append(f"{time.monotonic() - start:.0f}s: {error!r}")
                break
        result.update({"pings": pings, "failures": failures})
    result["held_s"] = round(time.monotonic() - start, 1)
    with open(out, "w") as f:
        f.write(json.dumps(result))


if __name__ == "__main__":
    main()
