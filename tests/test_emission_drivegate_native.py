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
assert args[:2] == ['run', '--project'] and '--' in args
project, module, library, bindings, output = args[args.index('--')+1:]
assert pathlib.Path(project, module+'.pg').read_text() == 'module '+module
assert json.loads(pathlib.Path(bindings).read_text()) == {'inputs':{},'outputs':{'Driven':'private:Driven'}}
pathlib.Path(output).write_text(os.environ['FAKE_NATIVE_LAYOUT'])
print('offline-export')
sys.exit(int(os.environ.get('FAKE_NATIVE_EXIT', '0')))
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
    monkeypatch.setenv("FAKE_NATIVE_LAYOUT", json.dumps(document))
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


def test_nonzero_export_cannot_be_reported_success_even_with_complete_output(tmp_path, monkeypatch):
    # S: output existence is not evidence of a successful producer run.
    package, library, sdk, _ = setup_inputs(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_NATIVE_EXIT", "7")
    result = collect(package, library, sdk, tmp_path / "capture")
    assert result.status != "passed"
    receipt = json.loads((tmp_path / "capture" / "receipt.json").read_bytes())
    assert receipt["exitCode"] == 7


def test_export_rejects_foreign_reference_and_wrong_native_library(tmp_path, monkeypatch):
    # S: an operation source must not silently expand the eventual write-set.
    package, library, sdk, document = setup_inputs(tmp_path, monkeypatch)
    assert collect(package, library, sdk, tmp_path / "valid").status == "passed"
    foreign = deepcopy(document)
    foreign["operations"][1]["data"]["members"]["Drive"]["targetId"] = "another-panel"
    monkeypatch.setenv("FAKE_NATIVE_LAYOUT", json.dumps(foreign))
    assert collect(package, library, sdk, tmp_path / "foreign").status != "passed"
    monkeypatch.setenv("FAKE_NATIVE_LAYOUT", json.dumps(document))
    (library / "test.dll").write_bytes(b"wrong-native-library")
    assert collect(package, library, sdk, tmp_path / "wrong-library").status != "passed"
    assert not (tmp_path / "wrong-library").exists()
