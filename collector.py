#!/usr/bin/env python3
"""Produce safe, structured latency measurements from curl metric output."""

import argparse
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess


METRIC_FIELDS = (
    "http_code",
    "curl_exit",
    "remote_ip",
    "dns_s",
    "connect_s",
    "tls_s",
    "ttfb_s",
    "total_s",
)


def parse_metrics(endpoint: str, round_number: int, metrics: str) -> dict[str, object]:
    values = metrics.strip().split("|")
    if len(values) != len(METRIC_FIELDS):
        raise ValueError("expected 8 pipe-delimited curl metric fields")

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "endpoint": endpoint,
        "round": round_number,
        "http_code": int(values[0]),
        "curl_exit": int(values[1]),
        "remote_ip": values[2],
        "dns_s": float(values[3]),
        "connect_s": float(values[4]),
        "tls_s": float(values[5]),
        "ttfb_s": float(values[6]),
        "total_s": float(values[7]),
    }


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def measure(rounds: int, curl_bin: str, output: Path, api_key: str | None = None) -> None:
    metric_format = (
        "%{http_code}|%{exitcode}|%{remote_ip}|%{time_namelookup}|"
        "%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}"
    )
    with output.open("w", encoding="utf-8") as result_file:
        for round_number in range(1, rounds + 1):
            for endpoint in ("global.ydc-index.io", "ydc-index.io"):
                completed = subprocess.run(
                    [
                        curl_bin,
                        "-sS",
                        "--connect-timeout",
                        "10",
                        "--max-time",
                        "15",
                        "-o",
                        "/dev/null",
                        "-w",
                        metric_format,
                    ]
                    + (["-H", f"X-API-Key: {api_key}"] if api_key else [])
                    + [f"https://{endpoint}/v1/search"],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                record = parse_metrics(endpoint, round_number, completed.stdout)
                result_file.write(json.dumps(record) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    parse_command = subcommands.add_parser("parse")
    parse_command.add_argument("--endpoint", required=True)
    parse_command.add_argument("--round", type=int, required=True)
    parse_command.add_argument("--metrics", required=True)
    measure_command = subcommands.add_parser("measure")
    measure_command.add_argument("--rounds", type=int, required=True)
    measure_command.add_argument("--curl-bin", default="curl")
    measure_command.add_argument("--output", type=Path, required=True)
    measure_command.add_argument("--authenticated", action="store_true")
    measure_command.add_argument("--api-key-env", default="YDC_API_KEY")
    env_check_command = subcommands.add_parser("env-check")
    env_check_command.add_argument("--env-file", type=Path, required=True)
    env_check_command.add_argument("--required", nargs="+", required=True)
    args = parser.parse_args()

    if args.command == "parse":
        print(json.dumps(parse_metrics(args.endpoint, args.round, args.metrics)))
    if args.command == "measure":
        api_key = None
        if args.authenticated:
            api_key = os.environ.get(args.api_key_env)
            if not api_key:
                parser.error(f"{args.api_key_env} is not set")
        measure(args.rounds, args.curl_bin, args.output, api_key)
    if args.command == "env-check":
        values = load_dotenv(args.env_file)
        print(json.dumps({key: bool(values.get(key)) for key in sorted(args.required)}))


if __name__ == "__main__":
    main()
