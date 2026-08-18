# クロスエージェント契約テスト

Phase 2-5では、同じhost-neutral fixture VaultをClaude Code／Codexの薄い
アダプターへ渡し、文字列の完全一致ではなく意味契約の充足を検証する。

## 対象

| 契約 | Claude Code | Codex | Hermes |
|---|---|---|---|
| SessionStart索引 | 対象 | 対象 | Phase 4で設計・現時点はテスト対象外 |
| compact回復注入 | 対象 | 対象 | 相当イベント未確認・Phase 4で判定 |
| PreCompact境界記録 | 対象 | 対象 | 相当イベント未確認・Phase 4で判定 |
| Skills構造 | 共通 `SKILL.md` | 共通 `SKILL.md` | Phase 4の導入実証で確認 |

「未確認」をN/Aと断定しない。ホストに相当イベントが無いと実測で確定した時点で、
理由つきN/Aとして固定する。

## fixtureとアサーション

`tests/fixtures/vault-basic/.wiki` に通常・`trust: untrusted`・命令文型summaryの
3ページとjournalを置く。`tests/test_cross_agent.py` が一時領域へコピーし、次を
検証する。

- 両SessionStartが同じタイトル・安全なsummary・パス集合を返す
- untrusted／命令文型summaryをどちらも注入しない
- delimiter、8,000文字上限、省略件数、compact回復指示が両方で成立する
- Claudeは平文、CodexはJSON包装というホスト固有形式を守る
- 両PreCompactが同じ意味の境界をjournalへ追記する
- BOM・壊れたstdinでもexit 0、stdoutへdebugを混ぜない
- transcriptのパス、Vault本文、fixture内の秘密文字列を診断ログへ出さない
- Core欠落時は安全に沈黙し、可能なら内容を含まない診断を残す

CIは通常の全unit discoveryに加え、Windows／Ubuntuの `adapter-contracts` ジョブで
このファイルを独立実行する。timeoutや非ゼロ終了に対する**Codex／Claude本体側**の
UI挙動はアダプタープロセス単体の責務外であり、ホスト実機E2Eで確認する。
Codex 0.145.0のSessionStart実機E2Eは完了済み。Claude Code実機E2EはClaude側の
環境で確認する。

