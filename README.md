# resonite-emission-editor

Resonite のアバター EmissionColor を編集するツールの、実験中のソースです。

このパッケージはオフラインの検査と、明示した project context での宣言生成までです。live の inspect / register / unregister は無効です。実機への切替は別の計画と許可が要ります。

元機能の設計・実装計画は `docs/superpowers/` にあります。Issue #3の現在の作業・検証方針は、create-reso-worldの`docs/superpowers/specs/2026-11-07-emission-issue-3-native-gate-design.md`と対応Planに定義します。

## 開発

- Python 3.14 以上
- 依存の取得とテスト: `uv sync --locked` のあと `uv run pytest`
- ソース、テスト、ライセンス、このツールの設計と実装計画はこのリポジトリに置きます。レビュー記録は create-reso-world に置きます。
- clone は create-reso-world の共通 CLI から行います。

ProtoFlux の check / build は、`RESONITE_MANAGED_DATA_PATH` に managed DLL のディレクトリを明示して、作業用のコピーに対して実行します。

### 読み取り証跡の内部基盤

`scripts/emission_editor/read_receipts.py` は、限定した読み取り要求から CLI 引数を生成し、raw stdout/stderr と実終了コードを新規ディレクトリへ保存する内部モジュールです。読み込み時に要求・引数・raw のハッシュを照合します。既存の live CLI 入口には接続していません。

`transportSucceeded` は CLI の終了コードと JSON envelope の確認だけです。native の動作、doctor の全項目、対象の session、結線、詳細 diff、全 preflight の成功を示しません。`complete` と `executionPermission` は常に false で、書き込みを許可しません。必要な二重読み・保全・封印資料との照合は別に行います。receipt と raw をすべて整合的に書き換えた場合の出所を認証する仕組みではありません。

### Issue #3 作業の比較・自動再試行方針

Root全体の値の完全一致は必要ありません。Rootの位置・回転やsolver等の時点差分は記録し、操作対象のsession/所有/write-set、明示した保護対象、private Before/完全色/型/readback/Doneを別に検証します。不一致・接続/検査失敗は調査・修正・再観測・自動再試行へ進め、最初の差分でIssue全体を止めません。書込/deploy/削除の再試行は、適用済みか読み戻してから行い、盲目的な二重送信を避けます。

本Issueの作業全体に限り、保護範囲や比較対象の実質的な見直しも追加確認は不要です。担当が理由・新範囲・検証を記録して進めます。他Issueへの一般化、対象外への変更、未知情報の成功化、過去のraw/seal/Beforeの上書きはしません。

`compare_root_context`はRootのIDと完全構造、明示した保護subtreeとその親の経路を検査し、Root全値差分は診断として返します。作成前の`assess_precreate_scope`と、Rootを安定親とする最終cleanupはこの区別を使います。`compare_root_deletion`は所有fixtureの消失と所有外slot/componentの不消失を検査し、所有外の値変化や追加は診断にします。並行した所有外の消失を当該操作の結果と断定しませんが、削除範囲の保全を立証できたとは扱いません。明示した保護subtreeは完全値・参照・型・親の経路を引き続き厳密に検査します。

厳密な`compare_subtree`と非Root所有親の`compare_deletion`は保持します。過去のone-use Root一致/初回失敗停止スクリプトは現在の進行条件として再利用しません。`drivegate_controller.run_command`は内部のprivate Command 1–6と独立した再capture要求だけを扱います。新しい`NativeDeployedStage`を再検証して、完全値/Before/Doneが適用済みなら再送せず、元checkpointと一致する未適用なら別attemptで再試行します。部分適用や不明状態は書き込まず再観測し、反復時はsource調査へ戻します。executionごとのwriter lockと元baselineの変更拒否、launch intent/プロセス終了/raw/各観測をcheckout外の専用journalへ保存します。任意argv/writer callbackは受け付けず、既存live CLI入口にも接続しません。

`drivegate_reader.collect_native_stage`は、検証済みの元NativeDeployedStageをanchorとして、session前後・fixture・保護対象・詳細diffを新しい証跡へ採取する内部の読取処理です。安定構造/metadata/IDはanchorのまま、moduleは元layout/current DLL modelから期待を構成します。型とIDを固定したprivate Valueの現在値だけを分類データとして取り出し、元Beforeやphase期待には採用しません。state bytesが前後で変わった場合や最終native検査が不合格なら、失敗した証跡を残してNativeReadFailureとなり、書込みは行いません。初回anchorを作るAPIではなく、実機での検証も未実施です。

初回Commandのbaselineは、native全構造の検証後に`drivegate_initial.compare_initial_fields`で封印宣言の全private initialFieldsとも照合します。未使用フラグだけで任意のBefore/Targetを採用しません。採取したCLI0.2.0の`ValueCodec.ColorX`はRGBAだけを設定し、current FrooxEngineの`ToEngine`はnull Profileをenum1（sRGB）として扱うことを実DLLのILで確認しています。初期宣言からこのcodecまでの呼出経路と、実行CLIの同一性の照合は後続preflightに残ります。色のprofileも完全値で検証し、別CLI版/current DLL model不一致ならsource調査が必要です。pureなphase比較APIは、独立に与えたbaselineの比較用途を維持します。

このCommand/reader部分だけで全mutation controller完了とはしません。create-onlyの認証と初回anchor、create/deploy/helper削除/最終cleanupの適用確認と回復、currentHEAD package・fullpreflight・実機0–6は後続です。実機接続前のfullpreflightでは、実行に使うPATH上のCLIの解決先と実versionを記録し、封印した`cliVersion`/SDK/sourceと照合する必要があります。このCommand API単体はCLI実行ファイルのinventoryを認証しません。また、journalのbaseline bindingはstage descriptorとstate bytesのfingerprintであり、raw全体の暗号学的な認証とは呼びません。readerは安定treeを元anchor、module treeを封印layout/modelから作ります。private Valueの型付き現在値は適用状況の分類入力だけであり、phase成功期待は元baselineから生成する必要があります。Root診断やfake CLIの検証をnative成功にしません。

### 短い native Drive gate のオフライン準備

`scripts/emission_editor/drivegate_package.py` の内部 API は次の 4 つです。

- `fixture_declaration(token)` は独自 ownership の `ResoLoop_Test_EmissionEditor_Drivegate_<token>` を宣言します。private な int / bool / colorX の ValueField と ObjectRoot だけを持ち、外部対象や既存のパネルを参照しません。
- `prepare_package(destination, token, library_path=...)` は **まだ存在しない、checkout 外のディレクトリ** に `flux/emission-editor/drivegate/` の 3 PG をコピーし、同期 check / build します。同時に兄弟ディレクトリ `<destination名>-execution` を新規作成し、入力と生成物の実行コピーを置きます。3 manifest の source / projectDirectory と別々の world state / Flux state は、この実行側だけを指します。3 manifest の検証と実行コピーの照合がすべて成功した場合だけ、元の package を封印します。state ファイルは作りません。どちらかの出力先が既存なら拒否します。失敗時は出力先を再利用せず、ログを確認してください。
- `verify_package(path)` は封印側の入力、SDK の生成 record / sidecar、manifest、検証ログの全 file-set と SHA-256 を照合します。改変・不足・未知の追加・symlink・不正な manifest 参照を拒否します。封印側だけの移動・コピーは可能で、manifest の実行先は変わりません。seal 自体の checksum も確認しますが、両方を整合的に書き換えた相手を認証する仕組みではありません。
- `verify_execution(package_path, execution_path)` は package を検証したうえで、指定先が seal の `executionRoot` と一致すること、実行側の入力・record / sidecar・fixture・manifest の全 file-set と SHA-256 が封印物と一致することを確認します。state を含む追加ファイルも拒否します。別途許可された利用側へ manifest を渡す直前に実行してください。実行後の state や観測結果の検証、同時書き込みの排他は行いません。

package は CLI / SDK の版、DLL 一式の SHA-256 による識別情報、準備時の code HEAD も記録します。準備の前後に未コミット変更（未追跡ファイルを含む）がないことと HEAD が変わっていないことを確認し、不一致では封印しません。元の PG と隣接する既存生成物には書き込みません。封印した package を deploy の再ビルド先や runtime state の保存先にはせず、manifest が指す実行側だけを使います。実行先を変更・再利用する場合は新しい package から準備し直します。形式は v3 で、旧 v1 / v2 は受け付けません。ResoLoop 0.2.0 が生成ルートへ自動追加する `AI_GeneratedContent.Source` を、入力宣言へ重複記述せず、seal の `normalizedFixture` に正規化後の期待として保持します。未確認の CLI 版では封印せず、precreate・配備後の全 key / 所有照合はこの期待モデルを使います。marker の正確な親・型・Source を検証し、余分な Component を無視したり、marker を書込対象の ValueField として扱ったりしません。完全な preflight と実行許可は別途必要です。

`DriveGateProbe.pg` は host-only の Command 入口を 1 つだけ持ちます。0 は no-op、1 は元の完全色と bool を一度だけ保存、2 は A / true、3 と 4 は B / false、5 は C / false、6 は保存した値の native 復元です。A / C は HDR・Linear・非 1 alpha、B は別の RGBA / sRGB です。対象の書き込みは mutable port を使い、OnWritten=1 / OnFail=2 を色と bool で別々に記録し、完全色と bool の readback 後に Done を更新します。2–6 は capture 前には動きません。ColorDrive / BoolDrive は eventless で A / true を Drive するだけです。helper の所有確認・削除・実機観測はここでは行いません。

保全用の全 subtree 比較では、installed CLI の名前付き非有限値（JSON文字列の `Infinity` / `-Infinity` / `NaN`）、完全な RGBA と非空 profile 文字列、既知の `Array_float3` / `Array_floatQ` の全要素・軸・内外 ID を失わずに照合します。これらは保全専用であり、private writer の有限値・Linear / sRGB 判定を広げません。未知 kind、欠落、不完全な配列を一致扱いにはしません。

### native layout のオフライン入力（Task2b-a）

`scripts/emission_editor/drivegate_native.py` の `collect_native_layout(package, module, directory, library_path=..., sdk_directory=...)` は、検証済み package の PG と `tools/drivegate-native-layout/` を新規の checkout 外ディレクトリへコピーし、installed SDK の `Build.moduleCompiler` → `Link.batchAddProtoGraphNodes` を呼びます。.NET 10 SDK と、ResoLoop が使用する Flux SDK DLL ディレクトリを明示してください。SDK/native DLL の全 inventory、封印した PG、exporter のソース、実終了コード・stdout/stderr・全生成ファイルのハッシュを receipt に結びます。`load_native_layout` はそれらを再照合します。`dotnet --info` と各段階の原出力・終了コード、上位ディレクトリのビルド設定のハッシュも保存します。子の環境は限定し、Directory.Build と利用者側 wildcard の自動 import を無効にします。取得先が空の専用 NuGet.Config を使い、`dotnet msbuild -noAutoResponse` で restore/build した後、生成 DLL を直接実行して再ビルドしません。MSBuild の自動 response file 読込を無効にし、上位ディレクトリの response file は hash 記録します。telemetry は子プロセスだけ無効にします。これは悪意あるツールチェーンへの認証・sandbox 保証ではありません。元 PG、package、実行コピーは変更しません。既存出力先や `..` を含むパスを拒否し、失敗した試行も残します。現行 exporter/DLL と異なる過去資料はこの loader で適用可能とせず、当時の結果を履歴として保持します。

出力は passive metadata を含む全 AddSlot/AddComponent 操作です。private port は `private:<port>`、親は `native-layout-parent` の記号で、実 ID を指定・適用する入口はありません。link の初期化・Query・Loader を呼ばず、native 接続も行いません。未知の操作、所有外参照、入力/出力の改変、非 0 終了を成功にしません。

`passed` は**オフライン生成証跡の検証だけ**です。BSON の 3D 配置や metadata を native の期待値として丸写ししないための入力です。`nativeSucceeded` / `complete` / `executionPermission` は false のままです。

### native 全構造と runtime 意味の照合（Task2b-b）

`drivegate_layout.py` の `compare_native_layout` は、全操作と `drivegate_native_model.json` の独立した native 型・既定値から期待値を作り、所有親配下の全 slot/component/member を照合します。slot は親・Name・PG の source-span Tag、component は型・必要に応じて VariableName、member は所有と名前から対応付けます。同名兄弟の並び順や、別コンパイルの alias 値の一致を所有の根拠にしません。内部 member alias、private port、null、System.Type の型文字列、list と targetType も含めて検査し、曖昧な対応、未知 payload、余分な要素・member は拒否します。

モデルは対象 native DLL のハッシュに結び付いています。版を固定して導入する機構ではなく、異なる DLL へ古い解釈を流用しないための比較条件です。native が更新されたら source 根拠を調査・レビューしてモデルを更新します。実観測値を既定値として採用しません。

`compare_native_runtime` は全構造の合格後だけ、BSON の runtime 意味検査へ接続します。除くことができるのは、すでに全構造で検証した独立既定値の member だけです。passive metadata の無条件除外で件数を合わせません。pure 比較への入力の出所は呼出側が保証する必要があります。実行側は `drivegate_runtime.assess_native_owned_graph` と `NativeDeployedStage` を使い、元の layout/BSON receipt、同じ package、現在の DLL、所有 state、private field を結びます。従来の `assess_owned_graph` / `DeployedStage` は旧オフライン検査用であり、現在の native gate の代用にしません。

この照合器のオフライン検証やモデルから作ったサンプルの一致は、実機照合に成功した証拠ではありません。Root診断とprivate Command部分の接続とは別に、create/deploy/削除を含む全mutation controller、現在HEADの新package、実機の全phase・復元・cleanupは未完了です。

この API は live 入口に接続しません。ビルド成功や Done だけで native 成功にはせず、`nativeSucceeded` / `complete` / `executionPermission` は常に false です。実機の保全、完全 preflight、各 phase の readback 判定は後続作業です。この短い gate は S4 全体や C9 / C30 / C23 / C31 の合格ではありません。
