# -*- coding: utf-8 -*-
"""Contract tests for the Codex SessionStart adapter."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "core"
ADAPTER = REPO / "adapters" / "codex" / "session_start.py"
CONFIG = REPO / "adapters" / "codex" / "hooks.example.json"

sys.path.insert(0, str(CORE))
import llmwiki  # noqa: E402


class TestCodexAdapter(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="llmwiki-codex-")
        self.workspace = Path(self._tmp.name)
        self.root = self.workspace / ".wiki"
        llmwiki.init_vault(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _page(self, title="Codex <context> page", summary="line one line two"):
        path = self.root / "wiki" / "concepts" / "Codex.md"
        llmwiki.atomic_write_text(path, (
            "---\n"
            f"title: {llmwiki.yaml_scalar_encode(title)}\n"
            f"summary: {llmwiki.yaml_scalar_encode(summary)}\n"
            "sources: []\n"
            "---\n\nbody\n"
        ))

    def _run(self, event: bytes) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["LLM_WIKI_ROOT"] = str(self.root)
        return subprocess.run(
            [sys.executable, str(ADAPTER)],
            input=event,
            capture_output=True,
            cwd=self.workspace,
            env=env,
            timeout=10,
            check=False,
        )

    def test_session_start_json_wraps_core_context_exactly(self):
        self._page()
        result = self._run(b'{"source":"startup"}')
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(result.stderr, b"")

        raw = result.stdout.decode("utf-8")
        payload = json.loads(raw)
        self.assertEqual(set(payload), {"hookSpecificOutput"})
        hook = payload["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], "SessionStart")
        context = hook["additionalContext"]
        self.assertTrue(context.startswith(llmwiki.CONTEXT_BEGIN))
        self.assertTrue(context.endswith(llmwiki.CONTEXT_END))
        self.assertIn("\n", context)
        self.assertIn("Codex <context> page", context)
        self.assertLessEqual(len(context), 8000)
        self.assertIn("<<<LLM_WIKI_CONTEXT>>>", raw)

    def test_bom_and_compact_source_add_recovery_inside_delimiters(self):
        self._page()
        result = self._run(b"\xef\xbb\xbf\xef\xbb\xbf" + b'{"source":"compact"}')
        context = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
        self.assertIn("コンパクション直後の回復指示", context)
        self.assertEqual(context.count(llmwiki.CONTEXT_BEGIN), 1)
        self.assertEqual(context.count(llmwiki.CONTEXT_END), 1)
        self.assertLess(context.index("コンパクション直後の回復指示"), context.index("LLM Wiki索引"))

    def test_empty_vault_is_success_with_no_output(self):
        result = self._run(b'{"source":"startup"}')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_non_object_or_malformed_input_does_not_break_startup(self):
        self._page()
        for event in (b"[]", b"not-json"):
            with self.subTest(event=event):
                result = self._run(event)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, b"")
                self.assertEqual(
                    json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]["hookEventName"],
                    "SessionStart",
                )

    def test_example_config_fixes_transport_contract(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        group = config["hooks"]["SessionStart"][0]
        handler = group["hooks"][0]
        self.assertEqual(group["matcher"], "startup|resume|clear|compact")
        self.assertEqual(handler["additionalContextLimit"], 0)
        self.assertIn("commandWindows", handler)
        self.assertNotIn("powershell", handler["commandWindows"].lower())
        self.assertEqual(handler["type"], "command")

    @unittest.skipUnless(os.name == "nt", "commandWindows is Windows-only")
    def test_command_runs_via_cmd_exe_without_powershell(self):
        self._page()
        env = os.environ.copy()
        env["LLM_WIKI_ROOT"] = str(self.root)
        command_line = f'cmd.exe /D /S /C python "{ADAPTER}"'
        result = subprocess.run(
            command_line,
            input=b'{"source":"startup"}',
            capture_output=True,
            cwd=self.workspace,
            env=env,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        hook = json.loads(result.stdout.decode("utf-8"))["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], "SessionStart")
        self.assertIn("Codex <context> page", hook["additionalContext"])


if __name__ == "__main__":
    unittest.main()
