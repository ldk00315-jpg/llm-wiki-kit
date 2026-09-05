# -*- coding: utf-8 -*-
"""skills/*/SKILL.md の構造契約（Phase 2-4）。

設計書 決定#7: 共通部は SKILL.md + frontmatter(name/description) +
Markdown本文 + 相対パス参照の補助資産に限定する。ホスト固有メタデータは
アダプター側へ分離し、共通のSKILL.mdには入れない。

参照した公式資料と確認日は docs/cross-agent-design.md のホスト互換マトリクス
（2026-08-13）にある。数値バージョンは公式に示されていないため固定しない。
"""
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
SKILLS = KIT / "skills"
COMMANDS = KIT / "commands"

# v1.2 からの 6 本（旧 commands/*.md と 1:1 対応する集合）
EXPECTED_LEGACY_PAIRED = {
    "wiki-ingest",
    "wiki-query",
    "wiki-health",
    "wiki-lint",
    "wiki-graph",
    "wiki-overview",
}
# merge 2 で追加。commands/ 側の旧 slash command は持たない（新規は Skills のみ）
EXPECTED_NEW = {"wiki-distill"}
EXPECTED = EXPECTED_LEGACY_PAIRED | EXPECTED_NEW

# 共通部分集合。ホスト固有のキーが混ざっていないことを検査する
ALLOWED_KEYS = {"name", "description"}


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, f"frontmatter not found: {path}"
    fields = {}
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class TestSkillStructure(unittest.TestCase):
    def test_all_skills_exist(self):
        found = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
        self.assertEqual(found, EXPECTED)

    def test_frontmatter_is_minimal_common_subset(self):
        for skill in sorted(EXPECTED):
            fm = _frontmatter(SKILLS / skill / "SKILL.md")
            with self.subTest(skill=skill):
                self.assertEqual(set(fm), ALLOWED_KEYS)
                self.assertEqual(fm["name"], skill)  # nameはディレクトリ名と一致
                self.assertTrue(fm["description"])
                # descriptionは1行説明。長すぎると発見層として機能しない
                self.assertLessEqual(len(fm["description"]), 120)

    def test_no_host_specific_placeholders(self):
        """$ARGUMENTS 等のホスト固有の展開記法を共通部に持ち込まない。"""
        for skill in sorted(EXPECTED):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill):
                self.assertNotIn("$ARGUMENTS", text)
                self.assertNotIn("argument-hint", text)

    def test_cli_reference_uses_python_core(self):
        """CLIの正本はPython Core（Phase 1）。ps1は互換wrapperとして併記のみ。

        Core CLI は Vault 操作が `core/llmwiki.py`、蒸留トラックが `core/distill.py`。
        どちらも Python Core であり、Skill 本文はそのどちらかを参照する。
        """
        for skill in sorted(EXPECTED):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill):
                self.assertTrue("llmwiki.py" in text or "core/distill.py" in text,
                                "Python Core CLI（llmwiki.py / core/distill.py）を参照すること")

    def test_argument_taking_skills_document_arguments(self):
        for skill in ("wiki-ingest", "wiki-query"):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill):
                self.assertIn("## 引数", text)

    def test_direct_write_lock_boundary_is_explicit(self):
        """Core未対応の本文生成までlock済みと誤認させない（決定#8の限定）。

        対象は**本文を直接書く Skill**（v1.2 の 6 本）。操作系 Skill（wiki-distill）は
        書き込みが全て CLI 経由で lock 内のため、別の assertion（下）で境界を検査する。
        """
        for skill in sorted(EXPECTED_LEGACY_PAIRED):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill):
                self.assertIn("lock対象外", text)
                self.assertRegex(text, r"保存直前に\s*再読込")

    def test_operation_skills_state_cli_lock_boundary(self):
        """操作系 Skill は「書き込みは CLI 経由・直接書きはしない」を明示する。"""
        for skill in sorted(EXPECTED_NEW):
            text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill):
                self.assertIn("lock対象外", text)
                self.assertIn("CLI 経由", text)

    def test_legacy_commands_kept_for_compatibility(self):
        """決定#11の段階移行: 旧配置は互換のため残す（削除はv2で検討）。"""
        legacy = {p.stem for p in COMMANDS.glob("wiki-*.md")}
        self.assertEqual(legacy, EXPECTED_LEGACY_PAIRED,
                         "旧 commands/ は v1.2 の 6 本のみ。新規 Skill に slash command は作らない")


if __name__ == "__main__":
    unittest.main()
