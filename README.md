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

### 短い native Drive gate のオフライン準備

`scripts/emission_editor/drivegate_package.py` の内部 API は次の 3 つです。

- `fixture_declaration(token)` は独自 ownership の `ResoLoop_Test_EmissionEditor_Drivegate_<token>` を宣言します。private な int / bool / colorX の ValueField と ObjectRoot だけを持ち、外部対象や既存のパネルを参照しません。
- `prepare_package(destination, token, library_path=...)` は **まだ存在しない、checkout 外のディレクトリ** に `flux/emission-editor/drivegate/` の 3 PG をコピーします。コピーへの同期 check / build と、別々の親・world state・Flux state を持つ 3 manifest の検証がすべて成功した場合だけ封印します。state ファイルは作りません。失敗した出力先は再利用せず、ログを確認してください。
- `verify_package(path)` は入力、SDK の生成 record / sidecar、manifest、検証ログの全 file-set と SHA-256 を照合します。改変・不足・未知の追加・symlink・範囲外参照を拒否します。seal 自体の checksum も確認しますが、両方を整合的に書き換えた相手を認証する仕組みではありません。

package は CLI / SDK の版、DLL 一式の SHA-256 による識別情報、準備時の code HEAD も記録します。元の PG と隣接する既存生成物には書き込みません。封印した package を deploy の再ビルド先や runtime state の保存先にしてはいけません。将来の実行では別コピーを作り、封印物との照合と完全な preflight が別途必要です。

`DriveGateProbe.pg` は host-only の Command 入口を 1 つだけ持ちます。0 は no-op、1 は元の完全色と bool を一度だけ保存、2 は A / true、3 と 4 は B / false、5 は C / false、6 は保存した値の native 復元です。A / C は HDR・Linear・非 1 alpha、B は別の RGBA / sRGB です。対象の書き込みは mutable port を使い、OnWritten=1 / OnFail=2 を色と bool で別々に記録し、完全色と bool の readback 後に Done を更新します。2–6 は capture 前には動きません。ColorDrive / BoolDrive は eventless で A / true を Drive するだけです。helper の所有確認・削除・実機観測はここでは行いません。

この API は live 入口に接続しません。ビルド成功や Done だけで native 成功にはせず、`nativeSucceeded` / `complete` / `executionPermission` は常に false です。実機の保全、完全 preflight、実行コピー、各 phase の readback 判定は後続作業です。この短い gate は S4 全体や C9 / C30 / C23 / C31 の合格ではありません。
