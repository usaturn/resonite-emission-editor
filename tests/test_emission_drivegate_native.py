"""S: bind offline native-operation evidence without overwrites or false success."""
from copy import deepcopy
import hashlib
import json
import os
import sys

from test_emission_drivegate_evidence import sealed_fixture
from scripts.emission_editor import drivegate_native as native


def setup_inputs(root, monkeypatch):
    package, _ = sealed_fixture(root)
    library, sdk = root / "native-dlls", root / "sdk-dlls"
    library.mkdir()
    sdk.mkdir()
    (library / "test.dll").write_bytes(b"native-library")
    (sdk / "FluxSDK.Build.dll").write_bytes(b"sdk-library")
    seal = json.loads((package / "seal.json").read_bytes())
    seal["dllSha256"] = {"test.dll": hashlib.sha256(b"native-library").hexdigest()}
    (package / "seal.json").write_text(json.dumps(seal))
    (package / "seal.sha256").write_text(hashlib.sha256((package / "seal.json").read_bytes()).hexdigest())
    executable = root / "dotnet"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
args = sys.argv[1:]
if args == ['--info']:
 print('fake toolchain 1'); sys.exit(0)
if args[0] == 'restore':
 print('offline restore'); sys.exit(0)
assert args[0] == 'run' and '--' in args
project, module, library, bindings, output = args[args.index('--')+1:]
assert pathlib.Path(project, module+'.pg').read_text() == 'module '+module
assert json.loads(pathlib.Path(bindings).read_text()) == {'inputs':{},'outputs':{'Driven':'private:Driven'}}
config = json.loads(pathlib.Path(__file__).with_name('fake-config.json').read_text())
doc = config['document']
if os.environ.get('MSBuildSDKsPath') or os.environ.get('DOTNET_STARTUP_HOOKS'):
 doc['operations'][1]['data']['componentType'] = 'environment-substituted-proxy'
pathlib.Path(output).write_text(json.dumps(doc))
print('offline-export')
sys.exit(config.get('exitCode', 0))
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ["PATH"])
    document = {"format": "emission-native-layout-v1", "module": "BoolDrive", "sdkVersion": "test",
                "operationCount": 2, "operations": [
        {"operation": "AddSlot", "data": {"id": "module", "name": {"value": "BoolDrive"},
            "parent": {"targetId": "native-layout-parent"}}},
        {"operation": "AddComponent", "containerSlotId": "module", "data": {
            "id": "proxy", "componentType": "test-proxy", "members": {
                "Drive": {"$type": "reference", "id": "drive-member", "targetId": "private:Driven"}}}}]}
    (root / "fake-config.json").write_text(json.dumps({"document": document}))
    return package, library, sdk, document


def collect(package, library, sdk, output):
    return native.collect_native_layout(package, "BoolDrive", output,
                                        library_path=library, sdk_directory=sdk)


def test_export_binds_sealed_inputs_exporter_dlls_and_raw_output(tmp_path, monkeypatch):
    # S: a plausible substituted operation graph must not be accepted as the
    # compiler's output, even if the process itself exited successfully.
    package, library, sdk, _ = setup_inputs(tmp_path, monkeypatch)
    output = tmp_path / "capture"
    result = collect(package, library, sdk, output)
    assert result.status == "passed", result.issues
    assert not result.nativeSucceeded and not result.executionPermission and not result.complete
    kwargs = dict(library_path=library, sdk_directory=sdk)
    assert native.load_native_layout(package, "BoolDrive", output, **kwargs).status == "passed"
    (sdk / "FluxSDK.Build.dll").write_bytes(b"different-sdk")
    assert native.load_native_layout(package, "BoolDrive", output, **kwargs).status != "passed"
    (sdk / "FluxSDK.Build.dll").write_bytes(b"sdk-library")
    layout = output / "layout.json"
    layout.write_text(layout.read_text().replace("test-proxy", "foreign-proxy"))
    assert native.load_native_layout(package, "BoolDrive", output, **kwargs).status != "passed"


def test_existing_or_input_destination_is_never_overwritten(tmp_path, monkeypatch):
    # S: evidence/source loss, not merely validating an option error.
    package, library, sdk, _ = setup_inputs(tmp_path, monkeypatch)
    output = tmp_path / "capture"
    assert collect(package, library, sdk, output).status == "passed"
    original = (output / "layout.json").read_bytes()
    assert collect(package, library, sdk, output).status != "passed"
    assert (output / "layout.json").read_bytes() == original
    original_source = (package / "sources" / "BoolDrive.pg").read_bytes()
    assert collect(package, library, sdk, package / "sources").status != "passed"
    assert (package / "sources" / "BoolDrive.pg").read_bytes() == original_source


def test_dotdot_destination_cannot_write_inside_protected_execution(tmp_path, monkeypatch):
    # S: a lexical spelling must not bypass the no-foreign-write boundary.
    package, library, sdk, _ = setup_inputs(tmp_path, monkeypatch)
    (tmp_path / "prefix").mkdir()
    destination = tmp_path / "prefix" / ".." / "execution" / "pollution"
    assert collect(package, library, sdk, tmp_path / "allowed").status == "passed"
    assert collect(package, library, sdk, destination).status != "passed"
    assert not (tmp_path / "execution" / "pollution").exists()


def test_nonzero_export_cannot_be_reported_success_even_with_complete_output(tmp_path, monkeypatch):
    # S: output existence is not evidence of a successful producer run.
    package, library, sdk, _ = setup_inputs(tmp_path, monkeypatch)
    config = tmp_path / "fake-config.json"
    data = json.loads(config.read_bytes())
    data["exitCode"] = 7
    config.write_text(json.dumps(data))
    result = collect(package, library, sdk, tmp_path / "capture")
    assert result.status != "passed"
    receipt = json.loads((tmp_path / "capture" / "receipt.json").read_bytes())
    assert receipt["exitCode"] == 7


def test_build_environment_cannot_substitute_compiler_or_startup_hook(tmp_path, monkeypatch):
    # S: inherited MSBuild/.NET code injection must not produce plausible but
    # different evidence from the same hashed exporter sources.
    package, library, sdk, _ = setup_inputs(tmp_path, monkeypatch)
    monkeypatch.setenv("MSBuildSDKsPath", "/untrusted/compiler")
    monkeypatch.setenv("DOTNET_STARTUP_HOOKS", "/untrusted/hook.dll")
    result = collect(package, library, sdk, tmp_path / "isolated")
    assert result.status == "passed", result.issues
    assert result.data[0]["operations"][1]["data"]["componentType"] == "test-proxy"


def test_export_rejects_foreign_reference_and_wrong_native_library(tmp_path, monkeypatch):
    # S: an operation source must not silently expand the eventual write-set.
    package, library, sdk, document = setup_inputs(tmp_path, monkeypatch)
    assert collect(package, library, sdk, tmp_path / "valid").status == "passed"
    foreign = deepcopy(document)
    foreign["operations"][1]["data"]["members"]["Drive"]["targetId"] = "another-panel"
    (tmp_path / "fake-config.json").write_text(json.dumps({"document": foreign}))
    assert collect(package, library, sdk, tmp_path / "foreign").status != "passed"
    (tmp_path / "fake-config.json").write_text(json.dumps({"document": document}))
    (library / "test.dll").write_bytes(b"wrong-native-library")
    assert collect(package, library, sdk, tmp_path / "wrong-library").status != "passed"
    assert not (tmp_path / "wrong-library").exists()
