#!/usr/bin/env bash
# You.com (ydc-index.io) API latency diagnostic tool.
# It intentionally measures the client-to-API path.  It does not infer an
# internal root cause from a single request: retain the request IDs it prints
# when escalating to You.com support.

set -o pipefail

API_HOST=""
API_KEY="${YDC_API_KEY:-}"
PROXY=""
Q="latest AI news today"
C=10
COUNTRY=""
FRESH=""
LIVE=0
URLS=()
TIMEOUT=15                 # Contents crawler timeout, sent to the API
MAXAGE=""
RUNS=3
INTERVAL=1
CONNECT_TIMEOUT=5
MAX_TIME=45                # client-side curl deadline
HTTP_MODE="--http2"
REPORT=""
SEARCH_PATH="/search"
ALLOWED_ENDPOINTS=("global.ydc-index.io" "ydc-index.io" "api.ydc-index.io")

RED=$(printf '\033[0;31m'); YEL=$(printf '\033[0;33m'); GRN=$(printf '\033[0;32m')
CYA=$(printf '\033[0;36m'); BOLD=$(printf '\033[1m'); NC=$(printf '\033[0m')

br() { printf "${CYA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"; }
hdr() { printf '\n'; br; printf "${BOLD}%s${NC}\n" "$1"; br; }
ok() { printf "  ${GRN}✓${NC} %s\n" "$1"; }
warn() { printf "  ${YEL}⚠${NC} %s\n" "$1"; }
bad() { printf "  ${RED}✗${NC} %s\n" "$1"; }
die() { bad "$1"; exit 1; }
is_number() { [[ "${1:-}" =~ ^[0-9]+([.][0-9]+)?$ ]]; }
need_value() { [[ $# -ge 2 && -n "$2" ]] || die "$1 需要一个值"; }
percent_of_total() { awk "BEGIN {printf \"%.0f\", ($1 / $2) * 100}"; }

load_env_key() {
  # Do not source .env: a diagnostics script should never execute its content.
  local env_file="$(cd "$(dirname "$0")" && pwd)/.env" line value
  [[ -n "$API_KEY" || ! -f "$env_file" ]] && return
  line=$(grep -m1 -E '^[[:space:]]*(export[[:space:]]+)?YDC_API_KEY=' "$env_file" 2>/dev/null || true)
  value=${line#*=}
  # Human-edited .env files frequently leave a trailing space.  That byte is
  # part of an HTTP header value and makes an otherwise valid API key fail.
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value=${value#\"}; value=${value%\"}; value=${value#\'}; value=${value%\'}
  API_KEY="$value"
}

curl_common() {
  CURL=(curl --silent --show-error --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" "$HTTP_MODE")
  # A shell's HTTPS_PROXY/ALL_PROXY silently changes the measured route.  Make
  # the route explicit: direct by default, proxy only when the caller asks.
  if [[ -n "$PROXY" ]]; then CURL+=(--proxy "$PROXY"); else CURL+=(--noproxy '*'); fi
}

header_value() {
  # Uses the last response block, which is relevant if a redirect ever occurs.
  local name="$1" file="$2"
  awk -v wanted="$name" 'BEGIN{IGNORECASE=1} /^HTTP\// {value=""} $0 ~ "^" wanted ":" {sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); value=$0} END {print value}' "$file"
}

percentile() {
  # nearest-rank percentile; compatible with macOS Bash 3.2.
  local p="$1"
  sort -n | awk -v p="$p" '{ values[NR] = $1 } END { if (!NR) { print "n/a"; exit }; rank = int((NR * p + 99) / 100); if (rank < 1) rank = 1; print values[rank] }'
}

timestamp() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

search_result_summary() {
  jq -r 'if (.hits | type == "array") and (.hits | length > 0) then
    .hits[0] | "top result: \(.title // "untitled" | gsub("[\\r\\n\\t]"; " ") | .[0:160]) — \(.url // "no URL" | gsub("[\\r\\n\\t]"; " ") | .[0:220])"
  else "top result: no hits returned" end' "$1" 2>/dev/null || printf 'top result: response was not parseable JSON'
}

write_report() {
  [[ -z "$REPORT" ]] && return
  [[ -e "$REPORT" ]] || printf 'timestamp\tlabel\trun\texit\thttp\tdns\tconnect\ttls\tttfb\ttotal\tremote_ip\thttp_version\trequest_id\tcorrelation_id\n' > "$REPORT"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@" >> "$REPORT"
}

time_it() {
  local label="$1" run="$2"; shift 2
  local metric_file header_file stderr_file body_file metric_line exit_code
  metric_file=$(mktemp); header_file=$(mktemp); stderr_file=$(mktemp); body_file=$(mktemp)
  curl_common
  "${CURL[@]}" -o "$body_file" -D "$header_file" \
    --write-out '__YDC__%{time_namelookup}|%{time_connect}|%{time_appconnect}|%{time_starttransfer}|%{time_total}|%{http_code}|%{remote_ip}|%{http_version}|%{size_download}' \
    "$@" >"$metric_file" 2>"$stderr_file"
  exit_code=$?
  metric_line=$(grep '__YDC__' "$metric_file" | tail -1 | sed 's/.*__YDC__//')
  IFS='|' read -r dns connect tls ttfb total code remote httpver size <<< "$metric_line"
  local request_id correlation_id err
  request_id=$(header_value 'x-request-id' "$header_file")
  # ydc-index.io/API Gateway rejection responses commonly expose this header
  # instead of x-request-id.  It is the useful support correlation ID for 403.
  [[ -n "$request_id" ]] || request_id=$(header_value 'x-amzn-requestid' "$header_file")
  correlation_id=$(header_value 'x-correlation-id' "$header_file")
  err=$(tr '\n' ' ' < "$stderr_file" | sed 's/[[:space:]]\+/ /g')

  if ! is_number "$total"; then
    bad "$label #$run: curl failed before timing data (exit=$exit_code) ${err:-unknown error}"
    write_report "$(timestamp)" "$label" "$run" "$exit_code" "${code:-0}" "${dns:-}" "${connect:-}" "${tls:-}" "${ttfb:-}" "${total:-}" "${remote:-}" "${httpver:-}" "$request_id" "$correlation_id"
    rm -f "$metric_file" "$header_file" "$stderr_file" "$body_file"
    printf '__YDC_TOTAL__:n/a\n'
    return 1
  fi

  local tcp_only tls_only api_to_ttfb download setup total_api_share setup_share status_color conclusion
  tcp_only=$(awk "BEGIN {printf \"%.3f\", $connect - $dns}")
  tls_only=$(awk "BEGIN {printf \"%.3f\", $tls - $connect}")
  api_to_ttfb=$(awk "BEGIN {printf \"%.3f\", $ttfb - $tls}")
  download=$(awk "BEGIN {printf \"%.3f\", $total - $ttfb}")
  setup=$(awk "BEGIN {printf \"%.3f\", $tls}")
  total_api_share=$(percent_of_total "$api_to_ttfb" "$total")
  setup_share=$(percent_of_total "$setup" "$total")
  status_color="$GRN"; [[ "$code" =~ ^2 ]] || status_color="$RED"
  printf '\n  %-18s #%s  HTTP:%s%-3s%s  总耗时: %ss  exit:%s\n' \
    "$label" "$run" "$status_color" "$code" "$NC" "$total" "$exit_code"
  printf '    ├─ DNS 解析                 %7ss\n' "$dns"
  printf '    ├─ TCP 建连（DNS 后）       %7ss\n' "$tcp_only"
  printf '    ├─ TLS 握手（TCP 后）       %7ss\n' "$tls_only"
  printf '    ├─ API 首字节等待（TLS 后） %7ss  %s%%\n' "$api_to_ttfb" "$total_api_share"
  printf '    └─ 响应下载                 %7ss\n' "$download"
  printf '    网络建连合计（DNS+TCP+TLS） %7ss  %s%%\n' "$setup" "$setup_share"
  printf '    remote:%s  HTTP/%s  bytes:%s  request-id:%s%s\n' \
    "${remote:-n/a}" "${httpver:-n/a}" "${size:-0}" "${request_id:-n/a}" \
    "${correlation_id:+  correlation-id:$correlation_id}"
  [[ "$label" == 'search' && "$code" =~ ^2 ]] && ok "endpoint: https://${API_HOST}${SEARCH_PATH} | $(search_result_summary "$body_file")"
  [[ $exit_code -ne 0 ]] && warn "curl error: $err"
  [[ ! "$code" =~ ^2 ]] && warn "这是非 2xx 响应；请先按 HTTP 状态处理，勿将其归为 API 慢。"
  if (( exit_code == 0 )) && [[ "$code" =~ ^2 ]]; then
    if awk "BEGIN {exit !($api_to_ttfb >= 1.5 && $api_to_ttfb > $setup)}"; then
      warn "结论：主要等待发生在 API 收到请求之后（首字节 ${api_to_ttfb}s，占 ${total_api_share}%）。这包含边缘排队、You.com 服务处理；Contents 还可能包含目标 URL 的抓取。建议用 request-id 查询后端链路。"
    elif awk "BEGIN {exit !($tls_only >= 0.5 || $tcp_only >= 0.3)}"; then
      warn "结论：TCP/TLS 建连偏慢，当前样本更像客户端到 ydc-index.io 的网络路径问题，而非 API 后端处理主导。"
    elif awk "BEGIN {exit !($download >= 0.5)}"; then
      warn "结论：首字节已较快，耗时主要在响应下载；请检查客户端网络吞吐、代理以及响应体大小。"
    else
      ok "结论：本次没有单一明显瓶颈；API 首字节 ${api_to_ttfb}s、网络建连 ${setup}s。请增加 --runs 以判断是否为偶发抖动。"
    fi
  fi
  write_report "$(timestamp)" "$label" "$run" "$exit_code" "$code" "$dns" "$connect" "$tls" "$ttfb" "$total" "$remote" "$httpver" "$request_id" "$correlation_id"
  rm -f "$metric_file" "$header_file" "$stderr_file" "$body_file"
  if (( exit_code == 0 )) && [[ "$code" =~ ^2 ]]; then
    printf '__YDC_TOTAL__:%s\n' "$total"
  else
    printf '__YDC_TOTAL__:n/a\n'
    return 1
  fi
}

run_samples() {
  local label="$1"; shift
  local totals=() result i value
  for ((i=1; i<=RUNS; i++)); do
    result=$(time_it "$label" "$i" "$@")
    printf '%s\n' "$result" | sed '/^__YDC_TOTAL__:/d'
    value=$(printf '%s\n' "$result" | sed -n 's/^__YDC_TOTAL__://p' | tail -1)
    is_number "$value" && totals+=("$value")
    (( i < RUNS )) && sleep "$INTERVAL"
  done
  if ((${#totals[@]})); then
    printf '%s\n' "${totals[@]}" > "${TMPDIR:-/tmp}/ydc-totals-$$"
    printf '  汇总（成功样本 %d/%d）: p50=%ss  p95=%ss  min=%ss  max=%ss\n' \
      "${#totals[@]}" "$RUNS" \
      "$(printf '%s\n' "${totals[@]}" | percentile 50)" \
      "$(printf '%s\n' "${totals[@]}" | percentile 95)" \
      "$(printf '%s\n' "${totals[@]}" | sort -n | head -1)" \
      "$(printf '%s\n' "${totals[@]}" | sort -n | tail -1)"
    rm -f "${TMPDIR:-/tmp}/ydc-totals-$$"
  else
    bad "没有成功获得可用 timing；请检查网络、代理、Key 和 HTTP 状态。"
  fi
}

cmd_dns() {
  hdr "DNS 解析 — ${API_HOST}"
  command -v dig >/dev/null || die "缺少 dig（安装 dnsutils/bind-utils）"
  dig +stats "$API_HOST" 2>/dev/null | grep -E 'Query time|SERVER|IN A|IN AAAA' | sed 's/^/  /' || warn "DNS 查询失败"
  printf '  IPv4/IPv6: '; dig +short "$API_HOST" 2>/dev/null | tr '\n' ' '; printf '\n'
}

cmd_ping() {
  hdr "ICMP 连通性 — ${API_HOST}"
  ping -c 5 "$API_HOST" 2>&1 | tail -3 | sed 's/^/  /'
  warn "ICMP 结果只用于连通性参考，不能证明 HTTPS/TCP 的丢包或 API 性能。"
}

cmd_tls() {
  hdr "TLS 基线 — ${API_HOST}:443"
  local data proto ocsp cert_count cert_bytes
  data=$(openssl s_client -connect "${API_HOST}:443" -servername "$API_HOST" -showcerts </dev/null 2>/dev/null || true)
  proto=$(printf '%s\n' "$data" | awk '/Protocol[[:space:]]*:/ {print $3; exit}')
  cert_count=$(printf '%s\n' "$data" | grep -c 'BEGIN CERTIFICATE' || true)
  cert_bytes=$(printf '%s\n' "$data" | awk '/BEGIN CERTIFICATE/,/END CERTIFICATE/' | wc -c | tr -d ' ')
  printf '  protocol:%s  certs:%s  PEM-bytes:%s\n' "${proto:-n/a}" "$cert_count" "$cert_bytes"
  ocsp=$(openssl s_client -connect "${API_HOST}:443" -servername "$API_HOST" -status </dev/null 2>/dev/null || true)
  if printf '%s\n' "$ocsp" | grep -q 'OCSP Response Status: successful'; then ok 'OCSP stapling: 响应存在';
  elif printf '%s\n' "$ocsp" | grep -q 'OCSP response: no response sent'; then warn 'OCSP stapling: 未提供（不代表客户端一定会额外查询 OCSP）';
  else warn '无法确认 OCSP 状态'; fi
  warn 'TLS 只描述本机到 ydc-index.io 的链路；经 HTTP proxy 时此命令不覆盖 proxy 隧道。'
}

search_args() {
  SEARCH_ARGS=(-G "https://${API_HOST}${SEARCH_PATH}" -H "X-API-Key: ${API_KEY}" --data-urlencode "query=${Q}" --data-urlencode "count=${C}")
  [[ -n "$COUNTRY" ]] && SEARCH_ARGS+=(--data-urlencode "country=${COUNTRY}")
  [[ -n "$FRESH" ]] && SEARCH_ARGS+=(--data-urlencode "freshness=${FRESH}")
  (( LIVE )) && SEARCH_ARGS+=(--data-urlencode 'livecrawl=web' --data-urlencode 'livecrawl_formats=markdown' --data-urlencode "crawl_timeout=${TIMEOUT}")
}

contents_json() {
  local url="$1"
  # URLs cannot validly contain unescaped double quotes; reject them rather than emit malformed JSON.
  [[ "$url" != *'"'* && "$url" != *$'\n'* ]] || die 'URL 包含不能编码的字符'
  printf '{"urls":["%s"],"formats":["markdown"],"crawl_timeout":%s%s}' "$url" "$TIMEOUT" "${MAXAGE:+,\"max_age\":$MAXAGE}"
}

cmd_search() {
  hdr "Search API — ${API_HOST}"
  printf '  query:%s  count:%s  runs:%s  HTTP mode:%s\n' "$Q" "$C" "$RUNS" "${HTTP_MODE#--}"
  (( LIVE )) && warn "livecrawl 已开启；post-TLS→TTFB 会包含实时抓取等待。"
  search_args; run_samples 'search' "${SEARCH_ARGS[@]}"
}

cmd_contents() {
  ((${#URLS[@]})) || die 'contents 需要至少一个 -u/--url <url>'
  hdr "Contents API — ${API_HOST}"
  local url json
  for url in "${URLS[@]}"; do
    json=$(contents_json "$url")
    printf '  URL: %s\n' "$url"
    run_samples 'contents' -X POST "https://${API_HOST}/v1/contents" -H "X-API-Key: ${API_KEY}" -H 'Content-Type: application/json' --data "$json"
  done
}

cmd_batch() {
  ((${#URLS[@]})) || die 'batch 需要一个或多个 -u/--url <url>'
  hdr "Contents API batch — ${API_HOST}"
  local json='{"urls":[' url sep=''
  for url in "${URLS[@]}"; do
    [[ "$url" != *'"'* && "$url" != *$'\n'* ]] || die 'URL 包含不能编码的字符'
    json+="$sep\"$url\""; sep=','
  done
  json+='],"formats":["markdown"],"crawl_timeout":'"$TIMEOUT"'}'
  run_samples 'contents-batch' -X POST "https://${API_HOST}/v1/contents" -H "X-API-Key: ${API_KEY}" -H 'Content-Type: application/json' --data "$json"
}

cmd_full() { cmd_dns; cmd_tls; cmd_search; ((${#URLS[@]})) && cmd_contents || warn '未提供 URL，跳过 Contents；使用 -u 指定用户的真实 URL。'; }

usage() {
  cat <<'EOF'
用法: bash ydcapi_latency_diag.sh [全局参数] <command> [参数]
目标固定默认为 https://ydc-index.io；全局参数可放在 command 前或后。

Commands: dns | ping | tls | search | contents | batch | full

常用参数:
  --endpoint HOST           必填：global.ydc-index.io、ydc-index.io 或 api.ydc-index.io
  --proxy URL               例如 http://127.0.0.1:7890
  --runs N --interval SEC   重复次数（默认 5）及样本间隔（默认 1 秒）
  --connect-timeout SEC --max-time SEC   客户端超时
  --http1.1 | --http2       强制协议，便于对比
  --report FILE.tsv         输出可附工单的 TSV（不写入 API Key/响应体）
  -q QUERY -c COUNT --country XX --freshness VALUE --livecrawl
  -u URL                    可重复使用；contents 逐 URL 测，batch 合并为一请求
  --timeout SEC --max-age SEC   Contents API 参数

示例:
  bash ydcapi_latency_diag.sh --endpoint ydc-index.io search -q '大模型价格战' --runs 10 --report search.tsv
  bash ydcapi_latency_diag.sh contents -u 'https://example.com/a' -u 'https://example.com/b' --runs 5
  bash ydcapi_latency_diag.sh --proxy http://127.0.0.1:7890 batch -u 'https://example.com/a' -u 'https://example.com/b'
EOF
}

CMD=""
while (($#)); do
  case "$1" in
    dns|ping|tls|search|contents|batch|full) [[ -z "$CMD" ]] || die "只能指定一个 command"; CMD="$1"; shift ;;
    --endpoint) need_value "$@"; API_HOST="$2"; shift 2 ;;
    --proxy) need_value "$@"; PROXY="$2"; shift 2 ;;
    --runs) need_value "$@"; RUNS="$2"; shift 2 ;;
    --interval) need_value "$@"; INTERVAL="$2"; shift 2 ;;
    --connect-timeout) need_value "$@"; CONNECT_TIMEOUT="$2"; shift 2 ;;
    --max-time) need_value "$@"; MAX_TIME="$2"; shift 2 ;;
    --report) need_value "$@"; REPORT="$2"; shift 2 ;;
    --http1.1) HTTP_MODE='--http1.1'; shift ;;
    --http2) HTTP_MODE='--http2'; shift ;;
    -q|--query) need_value "$@"; Q="$2"; shift 2 ;;
    -c|--count) need_value "$@"; C="$2"; shift 2 ;;
    --country) need_value "$@"; COUNTRY="$2"; shift 2 ;;
    --freshness) need_value "$@"; FRESH="$2"; shift 2 ;;
    --livecrawl) LIVE=1; shift ;;
    -u|--url) need_value "$@"; IFS=',' read -r -a _urls <<< "$2"; URLS+=("${_urls[@]}"); shift 2 ;;
    --timeout) need_value "$@"; TIMEOUT="$2"; shift 2 ;;
    --max-age) need_value "$@"; MAXAGE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数或 command: $1" ;;
  esac
done

[[ -n "$CMD" ]] || { usage; exit 1; }
[[ -n "$API_HOST" ]] || die '请通过 --endpoint 指定测试端点'
if [[ " ${ALLOWED_ENDPOINTS[*]} " != *" ${API_HOST} "* ]]; then
  die "不允许的 endpoint: ${API_HOST}"
fi
for v in "$RUNS" "$INTERVAL" "$CONNECT_TIMEOUT" "$MAX_TIME" "$TIMEOUT"; do is_number "$v" || die "数值参数非法: $v"; done
[[ "$C" =~ ^[0-9]+$ ]] || die "--count 必须是整数"
load_env_key
case "$CMD" in
  search|contents|batch|full) [[ -n "$API_KEY" ]] || die '请设置 YDC_API_KEY 或传入 --key' ;;
esac

hdr "You.com API 延迟诊断"
printf '  target:https://%s  time:%s  proxy:%s\n' "$API_HOST" "$(timestamp)" "${PROXY:-direct}"
[[ -n "$PROXY" ]] && warn '使用 proxy 时，DNS/TCP 指标描述的是客户端到 proxy 的路径；TLS/TTFB 描述 CONNECT 隧道后的总效果。'
case "$CMD" in
  dns) cmd_dns ;; ping) cmd_ping ;; tls) cmd_tls ;; search) cmd_search ;;
  contents) cmd_contents ;; batch) cmd_batch ;; full) cmd_full ;;
esac
br
