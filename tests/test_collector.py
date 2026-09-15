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
            environment.pop("BYTEPLUS_TEST_KEY", None)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "measure",
                    "--rounds",
                    "1",
                    "--authenticated",
                    "--api-key-env",
                    "BYTEPLUS_TEST_KEY",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("BYTEPLUS_TEST_KEY is not set", completed.stderr)
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


if __name__ == "__main__":
    unittest.main()
