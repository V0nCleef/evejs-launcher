import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.core.docker_mod_contract import parse_docker_mod_spec
from src.core.mod_api_manifest import read_api_manifest
from src.core.mod_manifest import scan_mods
from src.core.mod_runtime_state import build_mod_runtime_plan, build_docker_mod_runtime_snapshot, ModRuntimeStateError
from src.core.runtime.docker_compose import ComposeTarget, ComposeService, Mount, ContainerRecord
from src.core.runtime.docker_mod_images import (stage_image, prepare_image_override, attach_image_override,
    DockerModImageError, base_inputs_digest, prepare_image_transaction, restore_image_binding)
from src.core.runtime.docker_mods import apply_docker_mod_override, finalize_docker_mod_override
from src.core.service_status import DockerControlPolicy


def setup(tmp_path):
    folder = tmp_path / "mods" / "portable-source"
    (folder / "source").mkdir(parents=True)
    code = b"module.exports = 'portable source';\n"
    (folder / "source" / "feature.js").write_bytes(code)
    (folder / "helper.cjs").write_text("// portable helper\n")
    (tmp_path / "server/src").mkdir(parents=True)
    (tmp_path / "server/src/feature.json").write_text('{"enabled":true,"scale":3}')
    (tmp_path / "compose.yaml").write_text("name: portable\nservices: {}\n")
    manifest = {"schemaVersion":3,"id":"author.portable-source","version":"1.0.0","displayName":"Portable source",
        "description":"Version-checked source fixture", "kind":"source-integrated", "supportedBackends":["native","docker"],
        "activation":{"strategy":"json_boolean","configPath":"server/src/feature.json","property":"enabled"},"restart":"game_server",
        "launcherApi":{"version":1,"helper":{"runtime":"node","path":"helper.cjs"},"capabilities":["install","verify"]},
        "docker":{"schemaVersion":1,"dataDirectory":"portable","serverHelper":{"runtime":"node","path":"helper.cjs","actions":["install","verify"]},
            "hostActions":[],"image":{"service":"server","recipe":"source-overlay-v1","sourceDirectory":"source",
                "files":[{"source":"feature.js","destination":"/app/server/src/feature.js","baseSha256":"b"*64,"sha256":hashlib.sha256(code).hexdigest()}]}}}
    (folder / "evejs-launcher.mod.json").write_text(json.dumps(manifest))
    target = ComposeTarget(tmp_path / "compose.yaml", tmp_path, "portable")
    config = SimpleNamespace(project_name="portable", services={"server": ComposeService("server",True,(),None,
        (Mount("volume","portable-data","/var/lib/evejs"),),"qa-base")},effective_config_digest="c"*64)
    records = {name:ContainerRecord.absent(name) for name in ("server","market")}
    report = SimpleNamespace(ok=True,config=config,records=records)
    return folder, manifest, target, SimpleNamespace(preflight=lambda _:report)


class Runner:
    executable = "docker"
    def __init__(self, after_hash, *, base_hash="b"*64):
        self.after_hash, self.base_hash, self.commands = after_hash, base_hash, []
    def run(self, args, **kwargs):
        self.commands.append(args)
    def run_parsed(self, args, *, parser, **kwargs):
        self.commands.append(args)
        if args[:2] == ("image","inspect"):
            output = "sha256:" + ("b"*64 if (args[-1] == "qa-base" or args[-1].startswith("evejs-launcher-mod-base:")) else "d"*64)
        elif args[0] == "run":
            output = (self.base_hash if "sha256:"+"b"*64 in args else self.after_hash) + "  /app/server/src/feature.js\n"
        else:
            raise AssertionError(args)
        return parser(output)


def test_reused_build_context_cannot_add_an_undeclared_cargo_build_script(tmp_path):
    folder,manifest,target,inspector=setup(tmp_path)
    descriptor=read_api_manifest(tmp_path,folder)
    runner=Runner(manifest["docker"]["image"]["files"][0]["sha256"])
    stage_image(descriptor,target,inspector,runner)
    context=next((tmp_path/".evejs-launcher/image-staging").iterdir())
    (context/"source/build.rs").write_text('fn main(){ panic!("unreviewed input") }')
    registry=(tmp_path/".evejs-launcher/docker-images.json").read_bytes()
    builds=sum(call[0]=="build" for call in runner.commands)
    with pytest.raises(DockerModImageError,match="unexpected or changed inputs"):
        stage_image(descriptor,target,inspector,runner)
    assert (tmp_path/".evejs-launcher/docker-images.json").read_bytes()==registry
    assert sum(call[0]=="build" for call in runner.commands)==builds


@pytest.mark.parametrize("lost_ack",[False,True])
def test_image_rollback_owns_exact_after_record_and_recovers_publication_crash(tmp_path,monkeypatch,lost_ack):
    import uuid
    import src.core.runtime.docker_mod_images as images
    folder,manifest,target,inspector=setup(tmp_path)
    descriptor=read_api_manifest(tmp_path,folder)
    runner=Runner(manifest["docker"]["image"]["files"][0]["sha256"])
    transaction=str(uuid.uuid4())
    prepare_image_transaction(tmp_path,transaction,descriptor.id,None)
    real_atomic=images._atomic
    if lost_ack:
        def publish_then_crash(path,content):
            real_atomic(path,content)
            if path.name=="docker-images.json":
                raise OSError("Simulated lost image publication acknowledgement")
        monkeypatch.setattr(images,"_atomic",publish_then_crash)
        with pytest.raises(OSError):
            stage_image(descriptor,target,inspector,runner,transaction_id=transaction)
        monkeypatch.setattr(images,"_atomic",real_atomic)
    else:
        stage_image(descriptor,target,inspector,runner,transaction_id=transaction)
    path=tmp_path/".evejs-launcher/docker-images.json"
    after=path.read_bytes()
    document=json.loads(after)
    document["mods"][descriptor.id]["image"]="sha256:"+"f"*64
    path.write_text(json.dumps(document))
    third=path.read_bytes()
    with pytest.raises(DockerModImageError,match="decision changed"):
        restore_image_binding(tmp_path,descriptor,None,transaction_id=transaction)
    assert path.read_bytes()==third
    path.write_bytes(after)
    restore_image_binding(tmp_path,descriptor,None,transaction_id=transaction)
    assert json.loads(path.read_bytes())["mods"]=={}
    restore_image_binding(tmp_path,descriptor,None,transaction_id=transaction)


def test_source_overlay_build_and_running_image_attestation(tmp_path):
    folder, manifest, target, inspector = setup(tmp_path)
    descriptor = read_api_manifest(tmp_path, folder)
    runner = Runner(manifest["docker"]["image"]["files"][0]["sha256"])
    binding = stage_image(descriptor, target, inspector, runner)
    assert binding["image"] == "sha256:"+"d"*64
    assert binding["baseInputsSha256"] == base_inputs_digest(target)
    prepare_image_override(tmp_path, policy=DockerControlPolicy.MANAGED)
    deployed = attach_image_override(target)
    assert len(deployed.override_files) == 1
    text = deployed.override_files[0].read_text()
    assert "read_only: true" in text and "/app/server/src/feature.json" in text
    # Preload evidence alone cannot certify a source layer.
    applied = apply_docker_mod_override(tmp_path, (), policy=DockerControlPolicy.MANAGED)
    finalize_docker_mod_override(applied, policy=DockerControlPolicy.MANAGED)
    mods = scan_mods(tmp_path)
    assert len(mods) == 1 and mods[0].valid
    plan = build_mod_runtime_plan(tmp_path,mods,backend="docker_compose",mode="modded",runtime_identity="portable",selected_loader_ids=())
    arguments = dict(effective_node_options_sha256=hashlib.sha256(b"").hexdigest(),runtime_identity="portable-running")
    with pytest.raises(ModRuntimeStateError,match="images"):
        build_docker_mod_runtime_snapshot(plan,mods,**arguments)
    snapshot = build_docker_mod_runtime_snapshot(plan,mods,attested_images=(("server",binding["image"]),),**arguments)
    assert snapshot.effective_for(mods[0]) is True and snapshot.mods[0].evidence == "docker_image"
    # An edited base must not silently keep the obsolete derived image.
    target.compose_file.write_text("name: upgraded-base\n")
    with pytest.raises(DockerModImageError,match="base Compose"):
        attach_image_override(target)


def test_wrong_base_file_is_refused_before_build_or_registry_activation(tmp_path):
    folder, manifest, target, inspector = setup(tmp_path)
    runner = Runner(manifest["docker"]["image"]["files"][0]["sha256"],base_hash="e"*64)
    with pytest.raises(DockerModImageError,match="reviewed version"):
        stage_image(read_api_manifest(tmp_path,folder),target,inspector,runner)
    assert not any(command[0] == "build" for command in runner.commands)
    assert not (tmp_path/".evejs-launcher/docker-images.json").exists()


@pytest.mark.parametrize("path",["/app/server/../outside.js","/etc/passwd","/app/server/./feature.js"])
def test_source_overlay_destinations_cannot_escape_the_server(tmp_path,path):
    folder,manifest,_,_ = setup(tmp_path)
    manifest["docker"]["image"]["files"][0]["destination"] = path
    with pytest.raises(ValueError):
        parse_docker_mod_spec(manifest["docker"],folder)


@pytest.mark.parametrize("document", ['{"schemaVersion":1,"mods":{},"mods":{}}',
    '{"schemaVersion":true,"mods":{}}', '{"schemaVersion":1,"mods":{},"unexpected":42}'])
def test_ambiguous_image_registry_is_not_used_or_replaced(tmp_path, document):
    import src.core.runtime.docker_mod_images as images
    path = tmp_path/".evejs-launcher/docker-images.json"
    path.parent.mkdir()
    path.write_text(document)
    before = path.read_bytes()
    with pytest.raises(DockerModImageError):
        images._registry(tmp_path)
    assert path.read_bytes() == before
