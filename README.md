# API Endpoint Latency Test

**English** | [简体中文](#简体中文)

This repository contains tools for comparing the client-observed latency and connectivity of You.com Search API endpoints. It is intended for controlled endpoint diagnostics—not for load testing, benchmarking maximum throughput, or simulating production traffic.

The diagnostic script supports `global.ydc-index.io`, `ydc-index.io`, and `api.ydc-index.io`. It can measure DNS, TCP connection, TLS handshake, time to first byte (TTFB), total request time, HTTP status, remote IP, and request/correlation IDs. Measurements include the client-to-endpoint path and do not, by themselves, identify a specific server-side or network cause.

## Requirements

- Bash
- `curl` (HTTP/2 support is needed when using the default `--http2` option)
- `jq` for parsing Search API responses
- Python 3.10+ for the Python collector and tests

## Quick start

Set an API key in the environment, or place it in a local `.env` file as `YDC_API_KEY=...`. Never commit credentials. The `.env` file is ignored by Git.

```bash
export YDC_API_KEY='your-api-key'
bash ydcapi_latency_diag.sh --endpoint ydc-index.io search \
  -q 'latest electric vehicle news' -c 5 --runs 10 --report results/search.tsv
```

To check which required values exist in a dotenv file without printing their values:

```bash
python3 collector.py env-check --env-file .env \
  --required YDC_API_KEY INTERNATIONAL_API_KEY
```

The shell diagnostic script also reads `YDC_API_KEY` from the project-root `.env` when it is not set in the environment. Do not `source` a `.env` file.

## Diagnostic script

```text
bash ydcapi_latency_diag.sh --endpoint HOST COMMAND [OPTIONS]
```

Commands: `dns`, `ping`, `tls`, `search`, `contents`, `batch`, and `full`. Common options:

- `--endpoint HOST`: required; one of the three supported endpoint hostnames.
- `--runs N --interval SEC`: sample count and delay between samples.
- `--report FILE.tsv`: append timing and response metadata to a TSV file. It does not store the API key or response body; it does include request/correlation IDs when present.
- `--http1.1` or `--http2`: select the HTTP version.
- `--proxy URL`: use an explicit proxy. Without this option, the script attempts a direct connection.
- `-q QUERY -c COUNT`: Search request parameters.

See `bash ydcapi_latency_diag.sh --help` for all options. Treat queries, URLs, request IDs, and generated reports as potentially sensitive. The repository ignores local `results/` and `reports/` directories.

## Python collector

`collector.py` parses curl timing output, performs a simple alternating two-endpoint measurement, and checks dotenv values. For example:

```bash
python3 collector.py measure --rounds 10 --output results/collector.jsonl
```

The optional `--authenticated` flag sends the key named by `--api-key-env` (default: `YDC_API_KEY`) as an `X-API-Key` header. This collector makes requests to `/v1/search` without specifying a query or count; use the diagnostic script or a purpose-built matrix runner for realistic Search API comparisons. Review the target API's current requirements before running authenticated tests.

## Tests

```bash
python3 -m unittest tests/test_collector.py -v
```

## Interpreting results

Run comparisons from the network location that matters, use equivalent requests and valid credentials, alternate endpoint order where practical, and collect enough samples to compare success/timeout rates and latency percentiles. A small sample is useful for exploration but not a reliable P95/P99 estimate. Distinct API keys, account configuration, caches, request differences, and endpoint capabilities can confound a comparison. Confirm that endpoints implement the same API contract before drawing conclusions.

## Repository hygiene

`.gitignore` excludes local credentials, measured results, reports, query datasets, a test plan containing environment-specific details, temporary scripts, and Python cache files. Before publishing, inspect the staged changes (`git diff --cached`) and make sure no credentials, customer data, private infrastructure details, or sensitive queries are included. Ignoring files does not remove them from Git history if they were committed previously.

---

# 简体中文

本仓库提供用于比较 You.com Search API 不同端点的客户端延迟和连通性诊断工具。它适用于受控的端点诊断，**不是**负载测试、最大吞吐量测试，也不模拟生产流量。

诊断脚本支持 `global.ydc-index.io`、`ydc-index.io` 和 `api.ydc-index.io`，可测量 DNS、TCP 建连、TLS 握手、首字节时间（TTFB）、总请求时间、HTTP 状态、远端 IP，以及请求/关联 ID。测量反映客户端到端点的整体路径；单靠这些数据不能确定具体的服务端或网络故障原因。

## 环境要求

- Bash
- `curl`（使用默认 `--http2` 参数时需要支持 HTTP/2）
- `jq`（用于解析 Search API 响应）
- Python 3.10+（用于 Python collector 和测试）

## 快速开始

在环境变量中设置 API Key，或在本地 `.env` 文件中设置 `YDC_API_KEY=...`。不要提交凭证；`.env` 已被 Git 忽略。

```bash
export YDC_API_KEY='your-api-key'
bash ydcapi_latency_diag.sh --endpoint ydc-index.io search \
  -q 'latest electric vehicle news' -c 5 --runs 10 --report results/search.tsv
```

检查 dotenv 文件中是否存在指定配置（只输出是否存在，不输出值）：

```bash
python3 collector.py env-check --env-file .env \
  --required YDC_API_KEY INTERNATIONAL_API_KEY
```

若环境中没有设置 `YDC_API_KEY`，Shell 诊断脚本也会尝试读取项目根目录 `.env` 中的该配置。不要使用 `source` 加载 `.env`。

## 诊断脚本

```text
bash ydcapi_latency_diag.sh --endpoint HOST COMMAND [OPTIONS]
```

命令包括：`dns`、`ping`、`tls`、`search`、`contents`、`batch` 和 `full`。常用选项：

- `--endpoint HOST`：必填；只能使用上述三个支持的端点域名。
- `--runs N --interval SEC`：采样次数及采样间隔。
- `--report FILE.tsv`：将计时与响应元数据写入 TSV。不会保存 API Key 或响应体；如果响应中包含请求/关联 ID，也会记录这些 ID。
- `--http1.1` 或 `--http2`：选择 HTTP 版本。
- `--proxy URL`：显式使用代理；不指定时脚本会尝试直连。
- `-q QUERY -c COUNT`：Search 请求参数。

完整选项请运行 `bash ydcapi_latency_diag.sh --help`。查询词、URL、请求 ID 和生成的报告都应视为潜在敏感信息。仓库已忽略本地 `results/` 和 `reports/` 目录。

## Python collector

`collector.py` 用于解析 curl 计时输出、交替测量两个端点，以及检查 dotenv 配置。例如：

```bash
python3 collector.py measure --rounds 10 --output results/collector.jsonl
```

可选参数 `--authenticated` 会通过 `X-API-Key` 请求头发送指定环境变量中的 Key；变量名由 `--api-key-env` 指定，默认是 `YDC_API_KEY`。该 collector 请求 `/v1/search` 时不会提供 query 或 count；如需贴近真实使用方式的 Search API 对比，请使用诊断脚本或专用矩阵运行器。进行认证测试前，应核对目标 API 当前的请求要求。

## 测试

```bash
python3 -m unittest tests/test_collector.py -v
```

## 结果解读

应从实际关心的网络位置运行测试，确保请求条件相同、凭证有效，并尽可能交替端点顺序；样本量应足以比较成功率、超时率和延迟分位数。少量样本适合初步探索，但不足以可靠估计 P95/P99。不同 API Key、账户配置、缓存、请求差异和端点能力都可能影响比较结果。下结论前，先确认各端点实现相同的 API 契约。

## 仓库安全

`.gitignore` 排除了本地凭证、测量结果、报告、查询数据集、包含环境细节的测试计划、临时脚本和 Python 缓存。发布前请检查暂存内容（`git diff --cached`），确认其中不含凭证、客户数据、内部基础设施信息或敏感查询。若文件曾经提交过，仅添加忽略规则并不能将其从 Git 历史中移除。
