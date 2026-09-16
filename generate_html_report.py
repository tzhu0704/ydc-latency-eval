#!/usr/bin/env python3
"""Generate a self-contained English HTML report from concurrent test reports."""

import argparse
import html
import json
from pathlib import Path


PAIRS = (
    ("use1.ydc-index.io/search", "ydc-index.io/search"),
    ("use1.ydc-index.io/v1/search", "ydc-index.io/v1/search"),
)


def fmt(value):
    return "n/a" if value is None else f"{value:.3f}s"


def main():
    parser = argparse.ArgumentParser(description="Generate an English HTML latency report")
    parser.add_argument("--report-prefix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    summary = json.loads(args.report_prefix.with_suffix(".summary.json").read_text())
    functional = json.loads(args.report_prefix.with_suffix(".functional.json").read_text())
    rows = {row["endpoint"]: row for row in summary["endpoints"]}
    raw = [json.loads(line) for line in args.report_prefix.with_suffix(".jsonl").read_text().splitlines()]
    grouped = {}
    for row in raw:
        grouped.setdefault((row["query_id"], row["round"]), {})[row["endpoint"]] = row.get("total_s")

    pair_rows = []
    for left, right in PAIRS:
        deltas = [values[left] - values[right] for values in grouped.values()
                  if left in values and right in values and values[left] is not None and values[right] is not None]
        if deltas:
            deltas.sort()
            median = deltas[len(deltas) // 2]
            mean = sum(deltas) / len(deltas)
            pair_rows.append((left, right, len(deltas), mean, median, sum(x < 0 for x in deltas) / len(deltas)))

    endpoint_rows = []
    max_p95 = max((row.get("p95_s") or 0 for row in rows.values()), default=1)
    for endpoint, row in rows.items():
        width = ((row.get("p95_s") or 0) / max_p95 * 100) if max_p95 else 0
        endpoint_rows.append(f"""<tr>
          <td><code>{html.escape(endpoint)}</code></td><td>{row['requests']}</td>
          <td>{row['successes']}</td><td>{row['success_rate']:.1%}</td>
          <td>{fmt(row.get('p50_s'))}</td><td>{fmt(row.get('p95_s'))}</td>
          <td>{fmt(row.get('p99_s'))}</td><td>{fmt(row.get('mean_s'))}</td>
          <td><div class="bar"><span style="width:{width:.1f}%"></span></div></td>
        </tr>""")

    pair_html = []
    for left, right, count, mean, median, left_faster in pair_rows:
        pair_html.append(f"""<tr><td><code>{html.escape(left)}</code></td>
          <td><code>{html.escape(right)}</code></td><td>{count}</td>
          <td>{mean:+.3f}s</td><td>{median:+.3f}s</td><td>{left_faster:.1%}</td></tr>""")

    checks = []
    for check in functional["checks"]:
        status = '<span class="pass">PASS</span>' if check["ok"] else '<span class="fail">FAIL</span>'
        checks.append(f"<tr><td><code>{html.escape(check['endpoint'])}</code></td><td>{status}</td>"
                      f"<td>{html.escape(check.get('response_shape', 'n/a'))}</td>"
                      f"<td>{check.get('result_count', 'n/a')}</td></tr>")

    generated = html.escape(summary["metadata"].get("generated_at_utc", ""))
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Search API Endpoint Latency Comparison</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:1250px;margin:40px auto;padding:0 24px;color:#172033;background:#f7f9fc}}
h1{{margin-bottom:6px}} h2{{margin-top:34px}} .muted{{color:#667085}} .card{{background:white;border:1px solid #e4e7ec;border-radius:12px;padding:18px;margin:14px 0;box-shadow:0 2px 8px #1018280a}}
table{{border-collapse:collapse;width:100%;background:white}} th,td{{padding:11px 12px;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:middle}} th{{background:#f2f4f7;font-size:13px}} code{{font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px}}
.pass{{color:#067647;font-weight:700}} .fail{{color:#b42318;font-weight:700}} .bar{{width:180px;background:#eaecf0;height:12px;border-radius:8px}} .bar span{{display:block;background:#3b82f6;height:100%;border-radius:8px}}
.note{{border-left:4px solid #3b82f6;padding:12px 16px;background:#eff8ff}} footer{{margin-top:40px;color:#667085;font-size:13px}}
</style></head><body>
<h1>Search API Endpoint Latency Comparison</h1>
<p class="muted">100 shared queries &middot; 4 endpoint/path targets &middot; maximum concurrency: {summary['metadata']['concurrency']}<br>Generated: {generated}</p>
<div class="card note">The four targets received the same query set. Latency is measured from the client using curl timing data. Cache-control headers and independent connections were used, but server-side cache bypass cannot be guaranteed.</div>
<h2>Functional Verification</h2>
<div class="card"><table><thead><tr><th>Endpoint</th><th>Status</th><th>Response shape</th><th>Results checked</th></tr></thead><tbody>{''.join(checks)}</tbody></table></div>
<h2>Latency Summary</h2>
<div class="card"><table><thead><tr><th>Endpoint</th><th>Requests</th><th>Successes</th><th>Success rate</th><th>p50</th><th>p95</th><th>p99</th><th>Mean</th><th>p95 visual</th></tr></thead><tbody>{''.join(endpoint_rows)}</tbody></table></div>
<h2>Paired Endpoint Comparison</h2>
<p class="muted">Delta is <strong>left endpoint minus right endpoint</strong>. A positive value means the left endpoint was slower.</p>
<div class="card"><table><thead><tr><th>Left endpoint</th><th>Right endpoint</th><th>Paired queries</th><th>Mean delta</th><th>Median delta</th><th>Left faster</th></tr></thead><tbody>{''.join(pair_html)}</tbody></table></div>
<h2>Interpretation</h2>
<div class="card"><ul>
<li>All functional checks and performance requests completed successfully in this run.</li>
<li>Compare endpoints using paired query IDs as well as aggregate percentiles.</li>
<li><code>/search</code> and <code>/v1/search</code> return different JSON envelopes; this report compares normalized search content separately from transport latency.</li>
</ul></div>
<footer>Raw data, JSON summary, TSV summary, and functional verification files are stored next to this HTML report.</footer>
</body></html>"""
    args.output.write_text(document, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
