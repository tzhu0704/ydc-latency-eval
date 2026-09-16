import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import os


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "collector.py"


class CollectorCliTests(unittest.TestCase):
    def test_parse_emits_a_typed_measurement_record(self):
        """Catches a collector that loses phase timings or emits strings."""
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "parse",
                "--endpoint",
                "ydc-index.io",
                "--round",
                "7",
                "--metrics",
                "403|0|203.0.113.1|0.012|0.045|0.410|0.650|0.651",
            ],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        record = json.loads(completed.stdout)
        self.assertEqual(record["endpoint"], "ydc-index.io")
        self.assertEqual(record["round"], 7)
        self.assertEqual(record["http_code"], 403)
        self.assertEqual(record["curl_exit"], 0)
        self.assertEqual(record["remote_ip"], "203.0.113.1")
        self.assertEqual(record["dns_s"], 0.012)
        self.assertEqual(record["connect_s"], 0.045)
        self.assertEqual(record["tls_s"], 0.410)
        self.assertEqual(record["ttfb_s"], 0.650)
        self.assertEqual(record["total_s"], 0.651)

    def test_measure_alternates_endpoints_and_writes_jsonl(self):
        """Catches a run that silently omits a candidate endpoint or corrupts its output."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            fake_curl = temporary_path / "curl"
            fake_curl.write_text(
                "#!/bin/sh\n"
                "printf '403|0|203.0.113.1|0.012|0.045|0.410|0.650|0.651'\n"
            )
            fake_curl.chmod(0o755)
            output = temporary_path / "baseline.jsonl"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "measure",
                    "--rounds",
                    "2",
                    "--curl-bin",
                    str(fake_curl),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            records = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(
                [record["endpoint"] for record in records],
                [
                    "global.ydc-index.io",
                    "ydc-index.io",
                    "global.ydc-index.io",
                    "ydc-index.io",
                ],
            )
            self.assertTrue(all(record["http_code"] == 403 for record in records))

    def test_authenticated_measure_refuses_to_run_without_its_key(self):
        """Catches an authenticated run that sends unauthenticated requests by mistake."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "authenticated.jsonl"
            environment = os.environ.copy()
            environment.pop("TEST_API_KEY", None)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "measure",
                    "--rounds",
                    "1",
                    "--authenticated",
                    "--api-key-env",
                    "TEST_API_KEY",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("TEST_API_KEY is not set", completed.stderr)
            self.assertFalse(output.exists())

    def test_env_check_accepts_dotenv_values_that_are_not_shell_syntax(self):
        """Catches treating a dotenv file as shell code and losing a valid API key."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                "YDC_API_KEY=ydc-test-key\n"
                "INTERNATIONAL_API_KEY=&international-test-key\n"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "env-check",
                    "--env-file",
                    str(env_file),
                    "--required",
                    "YDC_API_KEY",
                    "INTERNATIONAL_API_KEY",
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {"INTERNATIONAL_API_KEY": True, "YDC_API_KEY": True},
        )

    def test_concurrent_matrix_generates_endpoint_reports_and_cache_headers(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            fake_curl = temporary_path / "curl"
            args_log = temporary_path / "curl-args.log"
            fake_curl.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >> '{args_log}'\n"
                "case \" $* \" in\n"
                "  *' -o /dev/null '*) printf '200|0|203.0.113.1|0.012|0.045|0.410|0.650|0.651' ;;\n"
                "  *) printf '{\"hits\":[{\"url\":\"https://example.com\"}]}'\n"
                "     printf '\\n__YDC_STATUS__200' ;;\n"
                "esac\n"
            )
            fake_curl.chmod(0o755)
            queries = temporary_path / "queries.json"
            queries.write_text(json.dumps([{"id": "q1", "query": "test"}]))
            queries2 = temporary_path / "queries-2.json"
            queries2.write_text(json.dumps([{"id": "q2", "query": "different test"}]))
            prefix = temporary_path / "reports" / "concurrent"
            environment = os.environ.copy()
            environment.update({"KEY_A": "key-a", "KEY_B": "key-b"})

            completed = subprocess.run(
                [
                    sys.executable, str(PROJECT_ROOT / "run_concurrent_matrix.py"),
                    "--queries", str(queries), str(queries2), "--report-prefix", str(prefix),
                    "--endpoint", "a.example/search@KEY_A",
                    "--endpoint", "b.example/search@KEY_B",
                    "--rounds", "2", "--concurrency", "2", "--curl-bin", str(fake_curl),
                ], capture_output=True, check=False, text=True, env=environment,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(prefix.with_suffix(".jsonl").exists())
            self.assertTrue(prefix.with_suffix(".summary.json").exists())
            self.assertTrue(prefix.with_suffix(".summary.tsv").exists())
            summary = json.loads(prefix.with_suffix(".summary.json").read_text())
            self.assertEqual([row["requests"] for row in summary["endpoints"]], [2, 2])
            raw_records = [json.loads(line) for line in prefix.with_suffix(".jsonl").read_text().splitlines()]
            self.assertEqual({record["query_set"] for record in raw_records}, {"queries", "queries-2"})
            args = args_log.read_text()
            self.assertIn("--no-keepalive", args)
            self.assertIn("Cache-Control: no-cache, no-store", args)
            self.assertIn("Pragma: no-cache", args)
            self.assertIn("X-Client-Request-Id:", args)


if __name__ == "__main__":
    unittest.main()
