# resonite-emission-editor

このリポジトリは Emission Editor の公開ソースです。create-reso-world の追跡対象ではありません。

- Python は 3.14 以上。実行とテストは uv を使う。
- 常設 checkout は detached HEAD のままにする。コードの変更は名前付き branch の linked worktree で行う。
- 文書の Spec と Plan は create-reso-world の `docs/superpowers/` に置く。このリポジトリへ複製しない。
- レビュー記録は create-reso-world の `reviews/` に置く。
- live の接続、discover、配備、実機試験は、この移行の範囲に含めない。別の許可があるまで live 入口を有効にしない。
- 元の `worlds/tools` は実機試験用の凍結領域として残す。こちらと同時に編集しない。
- 認証情報、実 session、絶対の個人パス、`.resoloop/` の state、生成物の `.brson` を commit しない。
