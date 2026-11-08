# resonite-emission-editor

このリポジトリは Emission Editor の公開ソースです。create-reso-world の追跡対象ではありません。

- Python は 3.14 以上。実行とテストは uv を使う。
- 常設 checkout は detached HEAD のままにする。コードの変更は名前付き branch の linked worktree で行う。
- このツールの設計と実装計画は、このリポジトリの `docs/superpowers/` に置く。
- リポジトリの移行や共通基盤の Spec と Plan、レビュー記録は create-reso-world に置く。
- 公開CLIのlive入口を移行だけで有効にしない。別途委任済みのIssue #3のcontrollerによる準備/実機確認とは区別する。
- 元の `worlds/tools` は実機試験用の凍結領域として残す。こちらと同時に編集しない。
- 認証情報、実 session、絶対の個人パス、`.resoloop/` の state、生成物の `.brson` を commit しない。

## Issue #3 の現在の作業規則

create-reso-world の `docs/superpowers/specs/2026-11-07-emission-issue-3-native-gate-design.md`を、Issue #3の作業・検証方針の定義元とする。利用者がcommit `19870487`のA案（単独パネルを維持、最小の実用動作を優先）を選択した。このrepoの元機能Spec/Planや下記の過去の部分検証記録より、この簡略Spec/Planを優先する。専用Drivegate/fullpreflight/全native・全DLL照合/旧全465手順/指定モデルレビューは必須条件ではない。既存ガードを削除するだけの作業はせず、本体の実演を先に進める。

- Root全体の値の完全一致は不要。全Rootの時点差分だけを不合格/停止理由にしない。
- 不一致・切断・検査失敗は、現在状態の確認と原因調査/修正/回復を行い、自動再試行する。初回でIssue全体を終了しない。write/deploy/削除は先に適用状況と所有を読み戻し、二重適用しない。
- 保護範囲・比較対象の実質変更も、今回のIssue作業全体では利用者への追加確認不要。理由・対象・検証方法を記録して進める。セッション限定ではなく、Issue完了まで引き継ぐ。

この明示委任は、既存パネル/実アバター等への対象外操作、失敗の成功化、原raw/seal/Beforeの上書きを許可するものではない。今回は旧gateを実装し直すのでなく、簡略Planの3 Taskを実行する。変更対象と元値の保持、当該値の読み戻し、失敗を成功としない最低限の確認は残す。
