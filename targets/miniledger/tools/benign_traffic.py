"""Generate benign GET traffic for the local MiniLedger teaching target only.

This utility is deliberately restricted to the numeric loopback host and the
ports registered by targets/miniledger/profile.yaml. It sends no writes or SQL
probes, disables environment proxies, and refuses HTTP redirects.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PROFILE_PORTS = {"a": 8095, "b": 8096}
REQUESTS = (
    ("home", "/"),
    ("health", "/health"),
    ("shipments", "/shipments"),
    ("carrier", "/shipments?carrier=SHP-101"),
    ("carrier", "/shipments?carrier=SHP-204"),
)


class RejectRedirectHandler(HTTPRedirectHandler):
    """Prevent urllib from following a response to any other destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_base(value: str, instance: str) -> str:
    """Accept only an explicit HTTP numeric-loopback URL on the profile port."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid --base URL: {exc}") from exc

    if parsed.scheme.lower() != "http" or not host:
        raise argparse.ArgumentTypeError("--base must be an explicit http:// loopback URL")
    if parsed.username is not None or parsed.password is not None:
        raise argparse.ArgumentTypeError("--base must not contain credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("--base must contain only the local origin")
    if "%" in host:
        raise argparse.ArgumentTypeError("scoped IP addresses are not allowed in --base")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--base must use a numeric loopback IP address (no DNS names)"
        ) from exc
    if not address.is_loopback:
        raise argparse.ArgumentTypeError("refusing non-loopback --base")

    expected = PROFILE_PORTS[instance]
    if port != expected:
        raise argparse.ArgumentTypeError(
            f"--base port must match MiniLedger instance {instance} ({expected})"
        )
    formatted_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{formatted_host}:{port}"


def fetch(opener, base: str, path: str, timeout: float) -> tuple[int, str, str | None]:
    request = Request(base + path, headers={"User-Agent": "MiniLedgerBenignTraffic/1.0"})
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(65536).decode("utf-8", "replace")
            status = response.status
            error = None if status == 200 else f"unexpected HTTP status {status}"
            return status, body, error
    except HTTPError as exc:
        body = exc.read(65536).decode("utf-8", "replace")
        if 300 <= exc.code < 400:
            return exc.code, body, f"redirect refused (HTTP {exc.code})"
        return exc.code, body, f"unexpected HTTP status {exc.code}"
    except (OSError, URLError, TimeoutError) as exc:
        return 0, "", f"{type(exc).__name__}: {str(exc)[:160]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="explicit local MiniLedger URL")
    parser.add_argument("--instance", required=True, choices=tuple(PROFILE_PORTS))
    parser.add_argument("--sessions", type=int, default=6)
    parser.add_argument("--out", default="targets/miniledger/var/benign_traffic.jsonl")
    parser.add_argument("--capture", action="store_true", help="include response snippets")
    args = parser.parse_args()
    if not 1 <= args.sessions <= 100:
        parser.error("--sessions must be between 1 and 100")
    try:
        base = local_base(args.base, args.instance)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    # An empty proxy map makes this opener independent of HTTP_PROXY/HTTPS_PROXY.
    opener = build_opener(ProxyHandler({}), RejectRedirectHandler())
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    failures = 0
    with output.open("w", encoding="utf-8") as stream:
        for session in range(args.sessions):
            for kind, path in REQUESTS:
                status, body, error = fetch(opener, base, path, timeout=5.0)
                record = {
                    "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    "instance": args.instance,
                    "label": "benign",
                    "session": session,
                    "kind": kind,
                    "detail": path[:80],
                    "status": status,
                }
                if args.capture:
                    record["response_snippet"] = body[:200]
                if error:
                    record["error"] = error
                    failures += 1
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1
    print(f"done: {total} benign local GETs -> {output}; failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
