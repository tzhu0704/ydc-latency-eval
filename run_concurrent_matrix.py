#!/usr/bin/env python3
"""Concurrent, per-endpoint Search latency test with structured reports.

Each query/endpoint cell is an independent curl process.  The default endpoint
set matches the existing matrix runner.  Results are written by the main
thread so a report cannot be interleaved by worker threads.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


METRIC_FORMAT = (
    "%{http_code}|%{exitcode}|%{remote_ip}|%{time_namelookup}|"
    "%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}"
)
DEFAULT_ENDPOINTS = (
    ("use1.ydc-index.io/search", "YDC_API_KEY"),
    ("ydc-index.io/search", "YDC_API_KEY"),
    ("use1.ydc-index.io/v1/search", "YDC_API_KEY"),
    ("ydc-index.io/v1/search", "YDC_API_KEY"),
)


def load_dotenv(path: Path) -> dict[str, str]:
    """Read simple KEY=value dotenv entries without executing the file."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        values[key] = value.strip().strip("'\"")
    return values


def percentile(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, (len(ordered) * percent + 99) // 100)
    return ordered[rank - 1]


def parse_endpoint(value: str) -> tuple[str, str]:
    try:
        endpoint, key_env = value.rsplit("@", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("端点格式必须是 HOST/PATH@API_KEY_ENV") from exc
    if not endpoint or not key_env or "https://" in endpoint:
        raise argparse.ArgumentTypeError("端点格式必须是 HOST/PATH@API_KEY_ENV")
    return endpoint, key_env


def measure_cell(
    query: dict[str, Any], query_set: str, endpoint: str, key: str, round_number: int,
    curl_bin: str, connect_timeout: float, max_time: float,
) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    command = [
        curl_bin, "-sS", "--no-keepalive",
        "--connect-timeout", str(connect_timeout), "--max-time", str(max_time),
        "-H", f"X-API-Key: {key}",
        # These headers prevent compliant intermediary caches from reusing a response.
        "-H", "Cache-Control: no-cache, no-store",
        "-H", "Pragma: no-cache",
        "-H", f"X-Client-Request-Id: {request_id}",
        "--get", "--data-urlencode", f"query={query['query']}",
        "--data-urlencode", "count=5", "-o", "/dev/null", "-w", METRIC_FORMAT,
        f"https://{endpoint}",
    ]
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    values = run.stdout.strip().split("|")
    record: dict[str, Any] = {
        "timestamp_utc": started, "query_id": query["id"],
        "query_set": query_set, "endpoint": endpoint, "round": round_number,
        "client_request_id": request_id, "http_code": 0,
        "curl_exit": run.returncode, "error": run.stderr.strip()[:500],
    }
    if len(values) == 8:
        record.update({
            "http_code": int(values[0]), "curl_exit": int(values[1]),
            "remote_ip": values[2], "dns_s": float(values[3]),
            "connect_s": float(values[4]), "tls_s": float(values[5]),
            "ttfb_s": float(values[6]), "total_s": float(values[7]),
        })
    else:
        record["error"] = f"malformed curl metrics: {run.stderr.strip()[:400]}"
    return record


def verify_cell(query: dict[str, Any], query_set: str, endpoint: str, key: str,
                curl_bin: str, connect_timeout: float, max_time: float) -> dict[str, Any]:
    """Verify the endpoint accepts the query and returns its expected JSON shape."""
    request_id = str(uuid.uuid4())
    marker = "__YDC_STATUS__"
    command = [
        curl_bin, "-sS", "--no-keepalive", "--connect-timeout", str(connect_timeout),
        "--max-time", str(max_time), "-H", f"X-API-Key: {key}",
        "-H", "Cache-Control: no-cache, no-store", "-H", "Pragma: no-cache",
        "-H", f"X-Client-Request-Id: {request_id}", "--get",
        "--data-urlencode", f"query={query['query']}", "--data-urlencode", "count=1",
        "-w", f"\\n{marker}%{{http_code}}", f"https://{endpoint}",
    ]
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    body, marker_value = run.stdout.rsplit(marker, 1) if marker in run.stdout else (run.stdout, "0")
    body = body.strip()
    result: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "query_set": query_set, "query_id": query["id"], "endpoint": endpoint,
        "http_code": int(marker_value.strip() or 0), "curl_exit": run.returncode,
        "ok": False, "request_id": request_id,
    }
    if run.returncode != 0 or result["http_code"] < 200 or result["http_code"] >= 300:
        result["error"] = run.stderr.strip()[:500] or "non-2xx response"
        return result
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        result["error"] = f"invalid JSON: {exc.msg}"
        return result

    if endpoint.endswith("/v1/search"):
        sections = payload.get("results") if isinstance(payload, dict) else None
        lists = [sections.get(name) for name in ("web", "news")] if isinstance(sections, dict) else []
        result_count = sum(len(items) for items in lists if isinstance(items, list))
        shape = "results.web/news"
    else:
        hits = payload.get("hits") if isinstance(payload, dict) else None
        result_count = len(hits) if isinstance(hits, list) else 0
        shape = "hits"
    if result_count == 0:
        result["error"] = f"expected non-empty {shape} result list"
        return result
    result.update({"ok": True, "response_shape": shape, "result_count": result_count})
    return result


def summarize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for endpoint in sorted({r["endpoint"] for r in records}):
        rows = [r for r in records if r["endpoint"] == endpoint]
        success = [r for r in rows if r.get("curl_exit") == 0 and 200 <= r.get("http_code", 0) < 300]
        totals = [float(r["total_s"]) for r in success if "total_s" in r]
        output.append({
            "endpoint": endpoint, "requests": len(rows), "successes": len(success),
            "success_rate": len(success) / len(rows) if rows else 0,
            "p50_s": percentile(totals, 50), "p95_s": percentile(totals, 95),
            "p99_s": percentile(totals, 99), "min_s": min(totals) if totals else None,
            "max_s": max(totals) if totals else None,
            "mean_s": statistics.mean(totals) if totals else None,
            "http_codes": {str(code): sum(r.get("http_code") == code for r in rows)
                           for code in sorted({r.get("http_code", 0) for r in rows})},
        })
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="并发测试多个 Search API 端点并生成延迟报告")
    parser.add_argument("--queries", type=Path, nargs="+", required=True,
                        help="一个或多个 JSON 查询集；每轮按顺序轮换")
    parser.add_argument("--report-prefix", type=Path, required=True, help="报告前缀，例如 reports/search-concurrent")
    parser.add_argument("--endpoint", action="append", type=parse_endpoint,
                        help="可重复；格式 HOST/PATH@API_KEY_ENV")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=None, help="最大同时请求数，默认端点数")
    parser.add_argument("--connect-timeout", type=float, default=10)
    parser.add_argument("--max-time", type=float, default=20)
    parser.add_argument("--curl-bin", default="curl")
    parser.add_argument("--skip-functional-check", action="store_true",
                        help="跳过性能测试前的功能预检（不建议）")
    args = parser.parse_args()
    if args.rounds < 1 or (args.concurrency is not None and args.concurrency < 1):
        parser.error("rounds 和 concurrency 必须大于 0")

    endpoints = args.endpoint or list(DEFAULT_ENDPOINTS)
    dotenv = load_dotenv(Path(__file__).resolve().parent / ".env")
    query_sets = []
    for query_path in args.queries:
        queries = json.loads(query_path.read_text(encoding="utf-8"))
        if not queries or any("id" not in q or "query" not in q for q in queries):
            parser.error(f"{query_path}: 必须是非空数组，且每项包含 id 和 query")
        if len({q["id"] for q in queries}) != len(queries):
            parser.error(f"{query_path}: query IDs are not unique")
        query_sets.append((query_path.stem, queries))
    keys = {}
    for endpoint, env_name in endpoints:
        keys[endpoint] = os.environ.get(env_name) or dotenv.get(env_name)
        if not keys[endpoint]:
            parser.error(f"{env_name} is not set for {endpoint}")

    prefix = args.report_prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {"raw": prefix.with_suffix(".jsonl"), "summary": prefix.with_suffix(".summary.json"),
             "tsv": prefix.with_suffix(".summary.tsv"), "functional": prefix.with_suffix(".functional.json")}
    if any(path.exists() for path in paths.values()):
        parser.error("报告文件已存在，拒绝覆盖: " + ", ".join(str(p) for p in paths.values()))

    functional_records = []
    if not args.skip_functional_check:
        for query_set_name, queries in query_sets:
            query = queries[0]
            for endpoint, _ in endpoints:
                functional_records.append(verify_cell(query, query_set_name, endpoint, keys[endpoint],
                                                      args.curl_bin, args.connect_timeout, args.max_time))
        paths["functional"].write_text(json.dumps({"passed": all(r["ok"] for r in functional_records),
                                                     "checks": functional_records},
                                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        failed = [r for r in functional_records if not r["ok"]]
        if failed:
            parser.error("功能预检失败，已停止性能测试: " + "; ".join(
                f"{r['endpoint']}={r.get('error', 'failed')}" for r in failed))

    # A round uses one complete query set.  Endpoints for every query in that
    # set are submitted together, so endpoint comparisons share the same wave
    # of questions rather than comparing different query populations.
    jobs = [(q, query_set_name, endpoint, round_number)
            for round_number in range(1, args.rounds + 1)
            for query_set_name, queries in [query_sets[(round_number - 1) % len(query_sets)]]
            for q in queries for endpoint, _ in endpoints]
    workers = args.concurrency or len(endpoints)
    records = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(measure_cell, q, query_set_name, endpoint, keys[endpoint], round_number,
                               args.curl_bin, args.connect_timeout, args.max_time)
                   for q, query_set_name, endpoint, round_number in jobs]
        for future in as_completed(futures):
            records.append(future.result())
    records.sort(key=lambda r: (r["round"], r["query_id"], r["endpoint"]))
    summary = summarize(records)
    metadata = {"generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "requests": len(records), "concurrency": workers, "rounds": args.rounds,
                "query_sets": [name for name, _ in query_sets],
                "query_set_for_round": {str(i): query_sets[(i - 1) % len(query_sets)][0]
                                         for i in range(1, args.rounds + 1)},
                "functional_check": not args.skip_functional_check,
                "cache_isolation": ["--no-keepalive", "Cache-Control: no-cache, no-store",
                                     "Pragma: no-cache", "unique X-Client-Request-Id"]}
    with paths["raw"].open("w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    paths["summary"].write_text(json.dumps({"metadata": metadata, "endpoints": summary},
                                             ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with paths["tsv"].open("w", encoding="utf-8") as handle:
        handle.write("endpoint\trequests\tsuccesses\tsuccess_rate\tp50_s\tp95_s\tp99_s\tmin_s\tmax_s\tmean_s\n")
        for row in summary:
            handle.write("\t".join(str(row.get(k, "")) for k in
                                    ("endpoint", "requests", "successes", "success_rate", "p50_s", "p95_s", "p99_s", "min_s", "max_s", "mean_s")) + "\n")
    print(f"complete: {len(records)} requests, concurrency={workers}")
    for row in summary:
        print(f"{row['endpoint']}: success={row['successes']}/{row['requests']} p95={row['p95_s']}s")
    print("reports: " + ", ".join(str(p) for p in paths.values()))


if __name__ == "__main__":
    main()
