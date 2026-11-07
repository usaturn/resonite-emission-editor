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
