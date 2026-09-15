#!/usr/bin/env python3
"""Run one non-overlapping, cold-connection three-endpoint Search matrix."""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


location, query_path, output_path = sys.argv[1:]
queries = json.loads(Path(query_path).read_text())
output = Path(output_path)
if output.exists():
    raise SystemExit(f"refusing to overwrite existing output: {output}")
if len({item["id"] for item in queries}) != len(queries):
    raise SystemExit("query IDs are not unique")

endpoints = [
    ("global.ydc-index.io/search", os.environ["INTERNATIONAL_API_KEY"]),
    ("ydc-index.io/search", os.environ["YDC_API_KEY"]),
    ("api.ydc-index.io/search", os.environ["INTERNATIONAL_API_KEY"]),
]
fmt = "%{http_code}|%{exitcode}|%{time_namelookup}|%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}"
seen = set()
with output.open("x") as handle:
    for index, item in enumerate(queries):
        for endpoint, key in endpoints[index % 3 :] + endpoints[: index % 3]:
            identity = (item["id"], endpoint)
            if identity in seen:
                raise SystemExit(f"duplicate matrix cell: {identity}")
            seen.add(identity)
            run = subprocess.run(
                ["curl", "-sS", "--no-keepalive", "--connect-timeout", "10", "--max-time", "20",
                 "-H", f"X-API-Key: {key}", "--get", "--data-urlencode", f"query={item['query']}",
                 "--data-urlencode", "count=5", "-o", "/dev/null", "-w", fmt, f"https://{endpoint}"],
                capture_output=True, text=True,
            )
            values = run.stdout.strip().split("|")
            if len(values) != 7:
                raise SystemExit(f"malformed curl metrics for {identity}")
            handle.write(json.dumps({
                "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "location": location, "query_id": item["id"], "endpoint": endpoint,
                "http_code": int(values[0]), "curl_exit": int(values[1]),
                "dns_s": float(values[2]), "connect_s": float(values[3]), "tls_s": float(values[4]),
                "ttfb_s": float(values[5]), "total_s": float(values[6]),
            }) + "\n")
if len(seen) != len(queries) * len(endpoints):
    raise SystemExit("incomplete matrix")
print(f"complete: {len(seen)} unique cells")
