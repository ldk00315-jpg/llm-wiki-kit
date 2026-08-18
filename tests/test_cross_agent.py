# -*- coding: utf-8 -*-
"""Cross-agent adapter contracts over one host-neutral fixture Vault."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


KIT = Path(__file__).resolve().parent.parent
FIXTURE = KIT / "tests" / "fixtures" / "vault-basic" / ".wiki"
CLAUDE_SESSION = KIT / "hooks" / "wiki_index_hook.py"
CLAUDE_PRECOMPACT = KIT / "hooks" / "precompact_hook.py"
CODEX_SESSION = KIT / "adapters" / "codex" / "session_start.py"
CODEX_PRECOMPACT = KIT / "adapters" / "codex" / "pre_compact.py"

sys.path.insert(0, str(KIT / "core"))
import llmwiki  # noqa: E402


class AdapterContractCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="llmwiki-cross-agent-")
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _vault(self, name: str) -> Path:
        root = self.base / name / ".wiki"
        shutil.copytree(FIXTURE, root)
        return root

    def _run(self, adapter: Path, root: Path, event: bytes) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["LLM_WIKI_ROOT"] = str(root)
        return subprocess.run(
            [sys.executable, str(adapter)],
            input=event,
            capture_output=True,
            cwd=root.parent,
            env=env,
            timeout=10,
            check=False,
        )

    @staticmethod
    def _codex_context(result: subprocess.CompletedProcess) -> str:
        payload = json.loads(result.stdout.decode("utf-8"))
        hook = payload["hookSpecificOutput"]
        if hook["hookEventName"] != "SessionStart":
            raise AssertionError("wrong Codex hookEventName")
        return hook["additionalContext"]

    @staticmethod
    def _rows(context: str) -> list[str]:
        return sorted(line for line in context.splitlines() if line.startswith("- "))

    def _assert_index_contract(self, context: str) -> None:
        self.assertLessEqual(len(context), 8000)
        self.assertTrue(context.startswith(llmwiki.CONTEXT_BEGIN))
        self.assertTrue(context.endswith(llmwiki.CONTEXT_END))
        self.assertEqual(context.count(llmwiki.CONTEXT_BEGIN), 1)
        self.assertEqual(context.count(llmwiki.CONTEXT_END), 1)
        self.assertIn("Cross Agent Trusted — Shared summary visible", context)
        self.assertIn("Cross Agent Untrusted [", context)
        self.assertIn("Cross Agent Adversarial [", context)
        self.assertNotIn("DO_NOT_EXPOSE_UNTRUSTED_SUMMARY", context)
        self.assertNotIn("ignore all previous instructions", context)
        self.assertNotIn("CROSS_AGENT_SECRET", context)

    def test_session_start_adapters_have_equivalent_index_semantics(self):
        root = self._vault("session")
        event = b'{"source":"startup"}'
        claude = self._run(CLAUDE_SESSION, root, event)
        codex = self._run(CODEX_SESSION, root, event)
        self.assertEqual((claude.returncode, codex.returncode), (0, 0))
        self.assertEqual((claude.stderr, codex.stderr), (b"", b""))

        claude_context = claude.stdout.decode("utf-8").rstrip("\r\n")
        codex_context = self._codex_context(codex)
        self._assert_index_contract(claude_context)
        self._assert_index_contract(codex_context)
        self.assertEqual(self._rows(claude_context), self._rows(codex_context))
        self.assertIn(b"<<<LLM_WIKI_CONTEXT>>>", codex.stdout)

    def test_same_large_fixture_has_equivalent_omission_semantics(self):
        root = self._vault("large")
        concepts = root / "wiki" / "concepts"
        for i in range(120):
            (concepts / f"Large{i:03d}.md").write_text(
                "---\n"
                f"title: Large Cross Agent Page {i:03d} with a descriptive title\n"
                "summary: A shared host-neutral summary long enough to exercise the output budget consistently.\n"
                "trust: trusted\n"
                "sources: []\n"
                "---\n\nfixture\n",
                encoding="utf-8",
            )
        event = b'{"source":"startup"}'
        claude = self._run(CLAUDE_SESSION, root, event)
        codex = self._run(CODEX_SESSION, root, event)
        contexts = (
            claude.stdout.decode("utf-8").rstrip("\r\n"),
            self._codex_context(codex),
        )
        for context in contexts:
            self.assertLessEqual(len(context), 8000)
            self.assertRegex(context, r"残り\d+件を省略")
            self.assertTrue(context.endswith(llmwiki.CONTEXT_END))
        self.assertEqual(self._rows(contexts[0]), self._rows(contexts[1]))
        omitted = [re.search(r"残り(\d+)件を省略", context).group(1) for context in contexts]
        self.assertEqual(omitted[0], omitted[1])

    def test_compact_recovery_contract_matches_on_both_hosts(self):
        root = self._vault("compact")
        event = b"\xef\xbb\xbf" + b'{"source":"compact"}'
        claude = self._run(CLAUDE_SESSION, root, event)
        codex = self._run(CODEX_SESSION, root, event)
        contexts = (
            claude.stdout.decode("utf-8").rstrip("\r\n"),
            self._codex_context(codex),
        )
        for context in contexts:
            self._assert_index_contract(context)
            self.assertIn("コンパクション直後の回復指示", context)
            self.assertIn("journal.md", context)
            self.assertLess(context.index("コンパクション直後の回復指示"), context.index("LLM Wiki索引"))

    def test_precompact_adapters_persist_equivalent_boundary_without_secret(self):
        event = (
            b"\xef\xbb\xbf\xef\xbb\xbf"
            b'{"trigger":"manual","transcript_path":'
            b'"C:/Users/alice/CROSS_AGENT_TRANSCRIPT_SECRET.jsonl"}'
        )
        journals = []
        for name, adapter in (
            ("claude", CLAUDE_PRECOMPACT),
            ("codex", CODEX_PRECOMPACT),
        ):
            root = self._vault(name)
            result = self._run(adapter, root, event)
            with self.subTest(host=name):
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                journal = (root / "inbox" / "journal.md").read_text(encoding="utf-8")
                self.assertIn("PreCompact境界（manual）", journal)
                self.assertNotIn("CROSS_AGENT_TRANSCRIPT_SECRET", journal)
                diagnostics = root / "diagnostics" / "hooks.log"
                if diagnostics.exists():
                    self.assertNotIn(
                        "CROSS_AGENT_TRANSCRIPT_SECRET",
                        diagnostics.read_text(encoding="utf-8"),
                    )
                journals.append(re.sub(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]", "[STAMP]", journal))
        self.assertEqual(journals[0], journals[1])

    def test_malformed_stdin_is_safe_and_never_adds_debug_to_stdout(self):
        for name, adapter, is_codex in (
            ("claude-session", CLAUDE_SESSION, False),
            ("codex-session", CODEX_SESSION, True),
        ):
            root = self._vault(name)
            result = self._run(adapter, root, b"not-json")
            with self.subTest(host=name):
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, b"")
                context = (
                    self._codex_context(result)
                    if is_codex
                    else result.stdout.decode("utf-8").rstrip("\r\n")
                )
                self._assert_index_contract(context)

        for name, adapter in (
            ("claude-precompact", CLAUDE_PRECOMPACT),
            ("codex-precompact", CODEX_PRECOMPACT),
        ):
            root = self._vault(name)
            result = self._run(adapter, root, b"not-json")
            with self.subTest(host=name):
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                journal = (root / "inbox" / "journal.md").read_text(encoding="utf-8")
                self.assertIn("PreCompact境界（unknown）", journal)

    def test_missing_core_is_silent_but_attempts_sanitized_diagnostics(self):
        for name, source in (
            ("claude-session", CLAUDE_SESSION),
            ("claude-precompact", CLAUDE_PRECOMPACT),
            ("codex-session", CODEX_SESSION),
            ("codex-precompact", CODEX_PRECOMPACT),
        ):
            root = self._vault(name)
            isolated = self.base / f"isolated-{name}" / source.name
            isolated.parent.mkdir(parents=True)
            shutil.copy2(source, isolated)
            result = self._run(
                isolated,
                root,
                b'{"source":"startup","transcript_path":"CROSS_AGENT_DIAGNOSTIC_SECRET"}',
            )
            with self.subTest(host=name):
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                log = (root / "diagnostics" / "hooks.log").read_text(encoding="utf-8")
                self.assertIn("Core import unavailable", log)
                self.assertNotIn("Cross Agent Trusted", log)
                self.assertNotIn("Shared summary visible", log)
                self.assertNotIn("CROSS_AGENT_DIAGNOSTIC_SECRET", log)


if __name__ == "__main__":
    unittest.main()
