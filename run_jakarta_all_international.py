#!/usr/bin/env python3
"""One clean Jakarta Web Search matrix with one shared API key."""
import json, os, subprocess
from datetime import datetime, timezone
from pathlib import Path

root = Path('/opt/github/byteplus-latency-test')
output = root / 'results/20260817-jakarta-all-international-search.jsonl'
queries = json.loads((root / 'query_sets/sea-natural-40-batch5.json').read_text())
if output.exists() or len({q['id'] for q in queries}) != len(queries):
    raise SystemExit('output exists or query IDs are not unique')
key = os.environ['INTERNATIONAL_API_KEY']
endpoints = ['global.ydc-index.io/search', 'ydc-index.io/search', 'api.ydc-index.io/search']
fmt = '%{http_code}|%{exitcode}|%{time_namelookup}|%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}'
with output.open('x') as handle:
    for i, query in enumerate(queries):
        for endpoint in endpoints[i % 3:] + endpoints[:i % 3]:
            result = subprocess.run(['curl','-sS','--no-keepalive','--connect-timeout','10','--max-time','20','-H',f'X-API-Key: {key}','--get','--data-urlencode',f'query={query["query"]}','--data-urlencode','count=5','-o','/dev/null','-w',fmt,f'https://{endpoint}'], capture_output=True, text=True)
            p = result.stdout.strip().split('|')
            if len(p) != 7: raise SystemExit('malformed curl metrics')
            handle.write(json.dumps({'timestamp_utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'query_id':query['id'],'endpoint':endpoint,'http_code':int(p[0]),'curl_exit':int(p[1]),'dns_s':float(p[2]),'connect_s':float(p[3]),'tls_s':float(p[4]),'ttfb_s':float(p[5]),'total_s':float(p[6])})+'\n')
print('complete', len(queries) * len(endpoints))
