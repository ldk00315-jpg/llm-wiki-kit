# -*- coding: utf-8 -*-
"""search/resolve契約テスト（Step 2: docs/search-resolve-contract.md）。

fixture設計はR-05の教訓に従う: 素朴な誤実装（ファイル名順・recency順・
常にnone/常にlikely）では失敗するよう、順序や閾値を逆向きに配置する。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "core"))
import llmwiki  # noqa: E402


class SearchVaultCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="llmwiki-search-")
        self.root = Path(self._tmp.name) / ".wiki"
        (self.root / "wiki" / "concepts").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _page(self, name, title, summary, extra="", section="concepts"):
        d = self.root / "wiki" / section
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(
            "---\n"
            f"title: {llmwiki.yaml_scalar_encode(title)}\n"
            f"summary: {llmwiki.yaml_scalar_encode(summary)}\n"
            f"{extra}"
            "sources: []\n"
            "---\n\nbody\n", encoding="utf-8")


class TestSearchPages(SearchVaultCase):
    def test_title_hit_ranks_above_summary_hit(self):
        # 誤実装対策: ファイル名順でもrecency順でもsummary側が先に来るfixture
        self._page("AAASummaryHit", "無関係な題", "本文はロックタイムアウトの話",
                   extra="updated: 2026-08-31\n")
        self._page("ZZZTitleHit", "ロックタイムアウトの診断", "別の要約",
                   extra="updated: 2020-01-01\n")
        res = llmwiki.search_pages(self.root, "ロックタイムアウト")
        self.assertEqual(res["total_hits"], 2)
        self.assertEqual(res["results"][0]["stem"], "ZZZTitleHit")

    def test_all_terms_hit_ranks_higher(self):
        # 片方の語しか持たないページが、ファイル名順・更新日順では先に来るfixture
        self._page("AAAPartial", "境界の設計", "s", extra="updated: 2026-08-31\n")
        self._page("ZZZBoth", "境界markerの設計", "s", extra="updated: 2020-01-01\n")
        res = llmwiki.search_pages(self.root, "境界 marker")
        self.assertEqual(res["results"][0]["stem"], "ZZZBoth")

    def test_tags_and_sources_are_searchable(self):
        self._page("Tagged", "音声処理の罠", "要約",
                   extra="tags: [ffmpeg, audio]\n")
        self._page("Sourced", "別の題", "要約2")
        (self.root / "wiki" / "concepts" / "Sourced.md").write_text(
            "---\n"
            "title: 別の題\n"
            "summary: 要約2\n"
            "sources:\n"
            "  - ../../raw/2026-08-31-special-source.md\n"
            "---\n\nbody\n", encoding="utf-8")
        self.assertEqual(
            llmwiki.search_pages(self.root, "ffmpeg")["results"][0]["stem"], "Tagged")
        self.assertEqual(
            llmwiki.search_pages(self.root, "special-source")["results"][0]["stem"],
            "Sourced")

    def test_japanese_substring_matches(self):
        self._page("Taxonomy", "サイレント失敗の分類学", "5型に分類")
        res = llmwiki.search_pages(self.root, "分類学")
        self.assertEqual(res["total_hits"], 1)

    def test_suppressed_summary_not_searchable_or_shown(self):
        # trust=untrusted のsummaryは照合にも表示にも使われない（抑制の迂回路防止）
        self._page("Ext", "外部由来ページ", "極秘キーワードXYZ",
                   extra="trust: untrusted\n")
        self.assertEqual(
            llmwiki.search_pages(self.root, "極秘キーワードXYZ")["total_hits"], 0)
        res = llmwiki.search_pages(self.root, "外部由来")
        self.assertEqual(res["total_hits"], 1)
        out = llmwiki.format_search_output("外部由来", res)
        self.assertIn("- 外部由来ページ [", out)
        self.assertNotIn("極秘キーワードXYZ", out)

    def test_deterministic_and_limit(self):
        for i in range(8):
            self._page(f"P{i}", f"同点ページ{i}", "共通キーワードあり")
        r1 = llmwiki.search_pages(self.root, "共通キーワード", limit=3)
        r2 = llmwiki.search_pages(self.root, "共通キーワード", limit=3)
        self.assertEqual(len(r1["results"]), 3)
        self.assertEqual(r1["total_hits"], 8)
        self.assertEqual([e["rel"] for e in r1["results"]],
                         [e["rel"] for e in r2["results"]])

    def test_empty_query_terms(self):
        self._page("A", "ページ", "要約")
        res = llmwiki.search_pages(self.root, "、。・")
        self.assertEqual(res["total_hits"], 0)
        self.assertIn("該当なし", llmwiki.format_search_output("、。・", res))


class TestResolveTitle(SearchVaultCase):
    def test_variant_of_long_title_is_duplicate_likely(self):
        # 実運用の型: 長い括弧書き補足つきタイトル。基底タイトル比較が効くことを固定
        self._page("Ffmpeg", "ffmpegラウドネス処理の罠（amix自動減衰・統計はstderr・lavfiフラグ）",
                   "要約")
        res = llmwiki.resolve_title(self.root, "ffmpegのラウドネス処理の罠まとめ")
        self.assertEqual(res["verdict"], "duplicate-likely")
        self.assertGreaterEqual(res["best"], 0.5)

    def test_exact_title_is_duplicate_likely(self):
        self._page("Gate", "Codexフックの信頼ゲート", "要約")
        res = llmwiki.resolve_title(self.root, "Codexフックの信頼ゲート")
        self.assertEqual(res["verdict"], "duplicate-likely")
        self.assertEqual(res["best"], 1.0)

    def test_moderate_overlap_is_similar(self):
        self._page("Gate", "Codexフックの信頼ゲート", "要約")
        res = llmwiki.resolve_title(self.root, "信頼ゲートの診断手順")
        self.assertEqual(res["verdict"], "similar")

    def test_novel_topic_is_none(self):
        # 誤実装対策: 「常にlikely/similar」を返す実装はここで落ちる
        self._page("Gate", "Codexフックの信頼ゲート", "要約")
        self._page("Ffmpeg", "ffmpegラウドネス処理の罠", "要約")
        res = llmwiki.resolve_title(self.root, "量子コンピュータの誤り訂正")
        self.assertEqual(res["verdict"], "none")
        out = llmwiki.format_resolve_output("量子コンピュータの誤り訂正", res)
        self.assertIn("新規作成してよい", out)


class TestSearchEntryInIndex(SearchVaultCase):
    def test_header_contains_search_entry(self):
        self._page("A", "ページA", "要約A")
        out = llmwiki.build_index_context(self.root)
        self.assertIn("検索入口", out)
        self.assertIn("resolve --title", out)

    def test_budget_invariant_still_holds_with_entry_line(self):
        for i in range(60):
            self._page(f"P{i:03d}", f"ページ{i:03d}" + "長" * 30, "要約" * 40)
        out = llmwiki.build_index_context(self.root, max_chars=2500)
        self.assertLessEqual(len(out), 2500)
        self.assertTrue(out.endswith(llmwiki.CONTEXT_END))


class TestSearchCli(SearchVaultCase):
    def test_cli_search_and_resolve(self):
        self._page("Taxonomy", "サイレント失敗の分類学", "5型に分類")
        rootarg = ["--wiki-root", str(self.root)]
        self.assertEqual(llmwiki.main(["search", "--query", "分類学"] + rootarg), 0)
        self.assertEqual(llmwiki.main(["resolve", "--title", "新しい題"] + rootarg), 0)
        self.assertEqual(llmwiki.main(["search"] + rootarg), 1)
        self.assertEqual(llmwiki.main(["resolve"] + rootarg), 1)


if __name__ == "__main__":
    unittest.main()
