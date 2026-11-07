# resonite-emission-editor

Resonite のアバター EmissionColor を編集するツールの、実験中のソースです。

このパッケージはオフラインの検査と、明示した project context での宣言生成までです。live の inspect / register / unregister は無効です。実機への切替は別の計画と許可が要ります。

このツールの設計と実装計画は `docs/superpowers/` にあります。

## 開発

- Python 3.14 以上
- 依存の取得とテスト: `uv sync --locked` のあと `uv run pytest`
- ソース、テスト、ライセンス、このツールの設計と実装計画はこのリポジトリに置きます。レビュー記録は create-reso-world に置きます。
- clone は create-reso-world の共通 CLI から行います。

ProtoFlux の check / build は、`RESONITE_MANAGED_DATA_PATH` に managed DLL のディレクトリを明示して、作業用のコピーに対して実行します。

### 読み取り証跡の内部基盤

`scripts/emission_editor/read_receipts.py` は、限定した読み取り要求から CLI 引数を生成し、raw stdout/stderr と実終了コードを新規ディレクトリへ保存する内部モジュールです。読み込み時に要求・引数・raw のハッシュを照合します。既存の live CLI 入口には接続していません。

`transportSucceeded` は CLI の終了コードと JSON envelope の確認だけです。native の動作、doctor の全項目、対象の session、結線、詳細 diff、全 preflight の成功を示しません。`complete` と `executionPermission` は常に false で、書き込みを許可しません。必要な二重読み・保全・封印資料との照合は別に行います。receipt と raw をすべて整合的に書き換えた場合の出所を認証する仕組みではありません。
