# -*- coding: utf-8 -*-
"""schema（運用契約）の内容契約テスト。

由来（2026-08-19）: 共有Vault運用で合意した配置・git運用ルール C-1〜C-5 が、
レポートには書かれたのに **schemaへ実装されていなかった**。
そのとき unittest 65件とCore lintは全PASSしており、欠落を検出できなかった。
決定論的な検査が「ファイルが在る／構造が正しい」しか見ておらず、
**契約の中身が揃っているか**を見ていなかったためである。

このテストは、合意した規約がschemaに実在することを固定する。
規約を増やしたらここへキーを足すこと。
"""
import re
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
SCHEMA = KIT / "template" / ".wiki" / "schema" / "AGENTS.llm-wiki.md"

# 各規約が満たすべき「見出しに現れる識別子」と「本文に必ず含まれる語」
CONTRACT_RULES = {
    "C-1": ["index", "live"],          # 索引を信じず現物を照合する
    "C-2": ["title", "summary"],       # 追記前に主題適合を確認する
    "C-3": ["cover", "not"],           # 扱う/扱わないを宣言する
    "C-4": ["commit", "coordinator"],  # 共有treeで自動commitしない
    "C-5": ["restore", "revert"],      # 復旧は読むことから始める
    "C-6": ["Re-read", "expect_snapshot"],   # lock外編集の楽観的並行制御
    "C-7": ["frontmatter", "index"],   # 本文を弱めたらfrontmatterも弱める
    "C-8": ["verbatim", "fail closed"],  # WAL checkpointは原文をlossless保全
}

SECTION_HEADING = "## Shared-Vault operating rules"


def _sections(text: str) -> dict:
    """`### ...` 見出し単位に本文を切り出す。"""
    out = {}
    current = None
    buf = []
    for line in text.split("\n"):
        if line.startswith("### "):
            if current is not None:
                out[current] = "\n".join(buf)
            current = line[4:].strip()
            buf = []
        elif line.startswith("## "):
            if current is not None:
                out[current] = "\n".join(buf)
            current = None
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf)
    return out


class TestSchemaContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCHEMA.read_text(encoding="utf-8")
        cls.sections = _sections(cls.text)

    def test_schema_exists(self):
        self.assertTrue(SCHEMA.is_file(), f"schema not found: {SCHEMA}")

    def test_shared_vault_section_exists(self):
        """C-1〜C-7 が1箇所に集まっていること（契約が散ると探せない）。"""
        self.assertIn(
            SECTION_HEADING,
            self.text,
            "共有Vault規約のセクションが無い。契約が複数箇所に散っている可能性がある",
        )

    def test_all_contract_rules_present(self):
        """C-1〜C-7 それぞれの見出しが存在すること。"""
        missing = []
        for rule in CONTRACT_RULES:
            if not any(rule in heading for heading in self.sections):
                missing.append(rule)
        self.assertEqual(
            [], missing,
            f"schemaに実装されていない規約がある: {missing}\n"
            f"レポートで合意しただけで実装漏れになっていないか確認すること",
        )

    def test_contract_rules_have_substance(self):
        """各規約の本文に、その規約を成立させる語が含まれること。

        見出しだけ作って中身が空、という実装漏れを防ぐ。
        """
        problems = []
        for rule, keywords in CONTRACT_RULES.items():
            heading = next((h for h in self.sections if rule in h), None)
            if heading is None:
                continue  # 見出し自体の欠落は別テストが報告する
            body = self.sections[heading]
            if len(body.strip()) < 120:
                problems.append(f"{rule}: 本文が短すぎる（{len(body.strip())}文字）")
                continue
            for kw in keywords:
                if kw.lower() not in body.lower():
                    problems.append(f"{rule}: 本文に '{kw}' が無い")
        self.assertEqual([], problems, "\n".join(problems))

    def test_optional_source_layer_documented(self):
        """S-4（source層の任意化）がschemaに残っていること。"""
        self.assertIn("Source pages are optional", self.text)
        self.assertNotIn(
            "one summary page per ingested raw source",
            self.text,
            "旧規則（raw 1件につきsource page 1枚）が残っている",
        )

    def test_frontmatter_updated_is_current_form(self):
        """schema自身のfrontmatterが本文と整合していること（C-7の自己適用）。"""
        m = re.match(r"^---\n(.*?)\n---\n", self.text, re.DOTALL)
        self.assertIsNotNone(m, "schemaにfrontmatterが無い")
        front = m.group(1)
        self.assertIn("summary:", front)
        self.assertRegex(front, r"updated:\s*\d{4}-\d{2}-\d{2}")
        self.assertIn(
            "C-1", front,
            "summaryが共有Vault規約に触れていない。"
            "本文を拡張したらfrontmatterも合わせること（C-7）",
        )


if __name__ == "__main__":
    unittest.main()
