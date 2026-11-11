"""Reproducible, package-neutral Linux artifact layers and owned image bindings."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_HEADER = "# Managed by EveJS Launcher: mod images v1\n"


class DockerModImageError(ValueError):
    pass


def _read_json(path, limit):
    data = path.read_bytes()
    if len(data) > limit:
        raise DockerModImageError("Image state exceeds its size limit; retain it for recovery.")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DockerModImageError("Image state contains duplicate fields; retain it for recovery.")
            result[key] = value
        return result
    def reject(_value):
        raise DockerModImageError("Image state contains non-finite values.")
    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=reject)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DockerModImageError("Image state is invalid; retain it for recovery.") from exc


def _plain_path(path: Path):
    for ancestor in (path, *path.parents):
        if ancestor.exists() and (ancestor.is_symlink() or getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400):
            raise DockerModImageError("Image state cannot traverse redirected paths.")


def _atomic(path: Path, data: bytes):
    _plain_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or getattr(path.parent.lstat(), "st_file_attributes", 0) & 0x400:
        raise DockerModImageError("Image state cannot use a redirected directory.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def source_inventory(root: Path):
    _plain_path(root)
    result = []
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            path = Path(current) / name
            info = path.lstat()
            if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise DockerModImageError("Image source cannot contain redirected files or directories.")
        for name in sorted(files):
            path = Path(current) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise DockerModImageError("Image source must contain only ordinary files.")
            if path.stat().st_nlink != 1:
                raise DockerModImageError("Image source files must have independent ownership.")
            total += path.stat().st_size
            if total > 64 * 1024 * 1024 or len(result) >= 2048:
                raise DockerModImageError("Image source exceeds the supported build-input bound.")
            result.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return tuple(sorted(result))


def _source_digest(spec):
    return hashlib.sha256(json.dumps(source_inventory(spec.source_directory), separators=(",", ":")).encode()).hexdigest()


def base_inputs_digest(target):
    root = target.project_directory.resolve(strict=True)
    owned = {root / ".evejs-launcher" / name for name in ("compose.mods.yaml", "compose.images.yaml")}
    files = [path for path in (target.compose_file, *target.override_files) if path not in owned]
    if (root / ".env").is_file():
        files.append(root / ".env")
    entries = []
    for path in files:
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400 or path.stat().st_size > 2 * 1024 * 1024:
            raise DockerModImageError("Base image inputs must be bounded ordinary files.")
        entries.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest()


def _registry(root: Path):
    path = root / ".evejs-launcher" / "docker-images.json"
    _plain_path(path)
    if not path.exists():
        return {}
    if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400 or path.stat().st_size > 128 * 1024:
        raise DockerModImageError("Image registry is not a bounded owned file.")
    data = _read_json(path, 128 * 1024)
    if (not isinstance(data, dict) or set(data) != {"schemaVersion", "mods"}
            or type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1 or not isinstance(data.get("mods"), dict)):
        raise DockerModImageError("Image registry is invalid; retain it for recovery.")
    for value in data["mods"].values():
        validate_image_record(value)
    return data["mods"]


def validate_image_record(value):
    if (not isinstance(value, dict) or set(value) != {"version", "service", "baseImage", "image", "sourceSha256", "artifactSha256", "manifestSha256", "baseInputsSha256"}
            or not isinstance(value["version"],str) or not 1 <= len(value["version"]) <= 64
            or value["service"] not in {"server", "market"}
            or any(not isinstance(value[k], str) or not _IMAGE.fullmatch(value[k]) for k in ("baseImage", "image"))
            or any(not isinstance(value[k], str) or not _HASH.fullmatch(value[k]) for k in ("sourceSha256", "artifactSha256", "manifestSha256", "baseInputsSha256"))):
        raise DockerModImageError("Image registry contains an invalid deployment binding.")

def stage_image(descriptor, target, inspector, runner, *, transaction_id=None):
    """Build immutable candidate image while every selected writer is stopped."""
    spec = descriptor.docker.image
    root = Path(descriptor.root).resolve(strict=True)
    if root != target.project_directory.resolve(strict=True):
        raise DockerModImageError("Image contribution targets a different installation.")
    registry_path = root / ".evejs-launcher" / "docker-images.json"
    registry = _registry(root)
    registry_before = registry_path.read_bytes() if registry_path.exists() else None
    report = inspector.preflight(target)
    base_inputs = base_inputs_digest(target)
    if not report.ok or report.config is None or report.records is None:
        raise DockerModImageError("Docker image staging preflight failed.")
    if any(record.exists and record.raw_state not in {"created", "exited", "dead"} for record in report.records.values()):
        raise DockerModImageError("Stop the selected project's services before staging its mod image.")
    service = report.config.services.get(spec.service)
    if service is None or not service.image:
        raise DockerModImageError("The selected service must declare a local base image.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}", service.image):
        raise DockerModImageError("The selected base image reference is invalid.")
    def image_id(text):
        value = text.strip()
        if not _IMAGE.fullmatch(value):
            raise DockerModImageError("Docker did not return an immutable image identity.")
        return value
    base = runner.run_parsed(("image", "inspect", "--format", "{{.Id}}", service.image),
        cwd=root, parser=image_id, timeout=15)
    inventory = source_inventory(spec.source_directory)
    source_sha = hashlib.sha256(json.dumps(inventory, separators=(",", ":")).encode()).hexdigest()
    manifest_sha = hashlib.sha256(descriptor.manifest_path.read_bytes()).hexdigest()
    image_tx = None
    if transaction_id is not None:
        tx_path,image_tx = _image_transaction(root,transaction_id,descriptor.id)
        if image_tx["phase"] not in {"prepared","publishing","applied"}:
            raise DockerModImageError("This image transaction cannot apply another candidate.")
        if registry.get(descriptor.id) not in (image_tx["before"],image_tx["after"]):
            raise DockerModImageError("The image decision changed outside this update. Retain recovery files.")
        if image_tx["after"] is not None:
            previous = image_tx["after"]
            if (previous["baseImage"] != base or previous["sourceSha256"] != source_sha
                    or previous["manifestSha256"] != manifest_sha or previous["baseInputsSha256"] != base_inputs):
                raise DockerModImageError("The captured image apply inputs changed. Roll back before retrying.")
            if registry.get(descriptor.id) == previous:
                return previous
            raise DockerModImageError("The captured image publication was interrupted. Recover this update before retrying.")
    build_key = hashlib.sha256(json.dumps((base, source_sha, manifest_sha, spec.recipe, spec.toolchain, spec.binary, spec.destination, spec.overlays)).encode()).hexdigest()
    stage = root / ".evejs-launcher" / "image-staging" / build_key
    if any(path.exists() and (path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
           for path in (root / ".evejs-launcher", stage.parent, stage)):
        raise DockerModImageError("Image staging path is redirected.")
    for relative, digest in inventory:
        source = spec.source_directory / relative
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise DockerModImageError("Image source changed during staging.")
        _atomic(stage / "source" / relative, data)
    def inspect_files(image, destinations):
        def parse(text):
            rows = [line.split(maxsplit=1) for line in text.splitlines() if line.strip()]
            if len(rows) != len(destinations) or any(len(row) != 2 or not _HASH.fullmatch(row[0]) for row in rows):
                raise DockerModImageError("The source-layer fingerprints were not returned.")
            return tuple(row[0] for row in rows)
        return runner.run_parsed(("run", "--rm", "--network", "none", "--entrypoint", "/usr/bin/sha256sum", image, *destinations),
            cwd=root, parser=parse, timeout=30)
    # BuildKit interprets FROM sha256:<id> as a registry repository/tag.
    # Pin a private local tag to the already-inspected image and verify it
    # again after the build; the original user tag is never changed.
    base_reference = "evejs-launcher-mod-base:" + base.removeprefix("sha256:")
    runner.run(("image","tag",base,base_reference),cwd=root,timeout=15)
    if spec.recipe == "source-overlay-v1":
        destinations = tuple(row[1] for row in spec.overlays)
        if inspect_files(base, destinations) != tuple(row[2] for row in spec.overlays):
            raise DockerModImageError("The selected base server differs from the overlay's reviewed version. No image was activated.")
        dockerfile = f"FROM {base_reference}\n" + "".join("COPY " + json.dumps(["source/" + row[0], row[1]]) + "\n" for row in spec.overlays)
    else:
        dockerfile = (
        f"FROM {spec.toolchain} AS builder\nWORKDIR /build\nCOPY source/ /build/\n"
        "RUN " + json.dumps(["cargo", "build", "--locked", "--release", "--manifest-path", spec.manifest, "--bin", spec.binary]) + "\n"
        f"FROM {base_reference}\nCOPY --from=builder /build/target/release/{spec.binary} {spec.destination}\n"
        )
    dockerfile += f"LABEL org.evejs-launcher.owner=\"mod-image-v1\" org.evejs-launcher.input=\"{build_key}\"\n"
    _atomic(stage / "Dockerfile", dockerfile.encode())
    tag = "evejs-launcher-mods:" + build_key
    staged_inputs = tuple(sorted([("source/"+name,digest) for name,digest in inventory]
        + [("Dockerfile",hashlib.sha256(dockerfile.encode()).hexdigest())]))
    if source_inventory(stage) != staged_inputs:
        raise DockerModImageError("The generated image context contains unexpected or changed inputs; retain it for review.")
    runner.run(("build", "--pull=false", "--tag", tag, str(stage)), cwd=root, timeout=3600)
    if source_inventory(stage) != staged_inputs:
        raise DockerModImageError("The image context changed during the build. No image was activated.")
    image = runner.run_parsed(("image", "inspect", "--format", "{{.Id}}", tag), cwd=root, parser=image_id, timeout=15)
    def artifact_hash(text):
        value = text.split()[0] if text.split() else ""
        if not _HASH.fullmatch(value):
            raise DockerModImageError("The Linux artifact fingerprint was not returned.")
        return value
    if spec.recipe == "source-overlay-v1":
        actual = inspect_files(image, destinations)
        if actual != tuple(row[3] for row in spec.overlays):
            raise DockerModImageError("The derived source image does not match its declared files.")
        artifact = hashlib.sha256(json.dumps(actual).encode()).hexdigest()
    else:
        artifact = runner.run_parsed(("run", "--rm", "--network", "none", "--entrypoint", "/usr/bin/sha256sum", image, spec.destination),
            cwd=root, parser=artifact_hash, timeout=30)
    final = inspector.preflight(target)
    fresh_base = runner.run_parsed(("image","inspect","--format","{{.Id}}",service.image),cwd=root,parser=image_id,timeout=15)
    pinned_base = runner.run_parsed(("image","inspect","--format","{{.Id}}",base_reference),cwd=root,parser=image_id,timeout=15)
    if fresh_base != base or pinned_base != base:
        raise DockerModImageError("The selected base image changed during the build. No image was activated.")
    if (not final.ok or final.config is None or final.records != report.records
            or final.config.effective_config_digest != report.config.effective_config_digest
            or source_inventory(spec.source_directory) != inventory
            or base_inputs_digest(target) != base_inputs
            or hashlib.sha256(descriptor.manifest_path.read_bytes()).hexdigest() != manifest_sha):
        raise DockerModImageError("Build inputs or Docker authority changed; candidate image was not activated.")
    if (registry_path.read_bytes() if registry_path.exists() else None) != registry_before:
        raise DockerModImageError("The image registry changed during the build. Candidate image was retained without activation.")
    record = {"version": descriptor.version, "service": spec.service, "baseImage": base,
        "image": image, "sourceSha256": source_sha, "artifactSha256": artifact, "manifestSha256": manifest_sha,
        "baseInputsSha256": base_inputs}
    if image_tx is not None:
        if registry.get(descriptor.id) != image_tx["before"]:
            raise DockerModImageError("The image binding changed before candidate publication.")
        image_tx.update(after=record,phase="publishing")
        # Durable after-image precedes registry publication, including a crash
        # between publication and acknowledgement of the apply phase.
        _atomic(tx_path,json.dumps(image_tx,sort_keys=True,indent=2).encode())
    registry[descriptor.id] = record
    _atomic(registry_path,json.dumps({"schemaVersion":1,"mods":registry},sort_keys=True,indent=2).encode())
    if image_tx is not None:
        image_tx["phase"] = "applied"
        _atomic(tx_path,json.dumps(image_tx,sort_keys=True,indent=2).encode())
    return registry[descriptor.id]


def image_bindings(root: Path):
    from src.core.mod_manifest import scan_mods
    registry = _registry(root)
    bindings = {}
    for mod in scan_mods(root):
        spec = getattr(mod.api_descriptor, "docker", None)
        if not mod.valid or not mod.active or spec is None or spec.image is None:
            continue
        record = registry.get(mod.id)
        if (record is None or record["version"] != mod.version
                or record["manifestSha256"] != hashlib.sha256(mod.manifest_path.read_bytes()).hexdigest()
                or record["sourceSha256"] != _source_digest(spec.image)):
            raise DockerModImageError(f"Install or recover the declared Docker image for {mod.name} before starting it.")
        if record["service"] in bindings:
            raise DockerModImageError("Two enabled mods contribute the same service image; resolve the conflict before starting.")
        bindings[record["service"]] = record["image"]
    return bindings


def _image_transaction_path(root, transaction_id):
    import uuid
    try:
        identity = str(uuid.UUID(str(transaction_id)))
    except (ValueError,TypeError,AttributeError):
        raise DockerModImageError("The image transaction ID is invalid.") from None
    path = Path(root) / ".evejs-launcher/image-transactions" / (uuid.UUID(identity).hex+".json")
    _plain_path(path)
    return path, identity


def _image_transaction(root, transaction_id, mod_id):
    path,identity = _image_transaction_path(root,transaction_id)
    if not path.is_file() or path.stat().st_size > 16384:
        raise DockerModImageError("The durable image transaction is missing or invalid. Retain recovery files.")
    value = _read_json(path, 16384)
    if (not isinstance(value,dict) or set(value) != {"schemaVersion","transactionId","modId","before","after","phase"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1 or value["transactionId"] != identity or value["modId"] != mod_id
            or value["phase"] not in {"prepared","publishing","applied","committed","restoring","rolled_back"}):
        raise DockerModImageError("The image transaction does not own this update.")
    for key in ("before","after"):
        if value[key] is not None:
            validate_image_record(value[key])
    return path,value


def prepare_image_transaction(root, transaction_id, mod_id, before):
    if before is not None:
        validate_image_record(before)
    path,identity = _image_transaction_path(root,transaction_id)
    desired = dict(schemaVersion=1,transactionId=identity,modId=mod_id,before=before,after=None,phase="prepared")
    if path.exists():
        _,existing = _image_transaction(root,transaction_id,mod_id)
        if existing != desired:
            raise DockerModImageError("This image transaction is already in use; retain it for recovery.")
        return
    if _registry(Path(root)).get(mod_id) != before:
        raise DockerModImageError("The image binding changed before update preparation.")
    _atomic(path,json.dumps(desired,sort_keys=True,indent=2).encode())


def restore_image_binding(root, descriptor, before, *, transaction_id=None):
    """Restore only the exact before/after decision owned by this update UUID."""
    root = Path(root).resolve(strict=True)
    registry_path = root/".evejs-launcher/docker-images.json"
    registry = _registry(root)
    captured = registry_path.read_bytes() if registry_path.exists() else None
    current = registry.get(descriptor.id)
    if current == before:
        return  # No mutation; also safe for an old pre-image update journal.
    if transaction_id is None:
        raise DockerModImageError("Exact durable image ownership is required for rollback.")
    path,tx = _image_transaction(root,transaction_id,descriptor.id)
    if (tx["before"] != before or tx["after"] is None or current != tx["after"]
            or tx["phase"] not in {"publishing","applied","restoring"}):
        raise DockerModImageError("The candidate image decision changed; automatic rollback was refused.")
    tx["phase"] = "restoring"
    _atomic(path,json.dumps(tx,sort_keys=True,indent=2).encode())
    if before is None:
        registry.pop(descriptor.id)
    else:
        validate_image_record(before)
        registry[descriptor.id] = before
    if (registry_path.read_bytes() if registry_path.exists() else None) != captured:
        raise DockerModImageError("The image registry changed during rollback. Retain recovery files.")
    _atomic(registry_path,json.dumps({"schemaVersion":1,"mods":registry},sort_keys=True,indent=2).encode())
    tx["phase"] = "rolled_back"
    _atomic(path,json.dumps(tx,sort_keys=True,indent=2).encode())


def commit_image_transaction(root,transaction_id,mod_id):
    path,tx = _image_transaction(root,transaction_id,mod_id)
    if tx["after"] is None or _registry(Path(root)).get(mod_id) != tx["after"] or tx["phase"] not in {"applied","publishing","committed"}:
        raise DockerModImageError("The committing image decision changed. Retain recovery files.")
    tx["phase"] = "committed"
    _atomic(path,json.dumps(tx,sort_keys=True,indent=2).encode())


def _render_override(root, bindings):
    from src.core.mod_manifest import scan_mods, ActivationKind
    mounts = []
    for mod in scan_mods(root):
        spec = getattr(mod.api_descriptor, "docker", None)
        if mod.valid and mod.active and spec is not None and spec.image is not None and mod.activation_kind is ActivationKind.JSON_BOOLEAN:
            config = mod.config_path
            relative = config.relative_to(root)
            if relative.parts[0] != "server" or config.is_symlink() or getattr(config.lstat(), "st_file_attributes", 0) & 0x400:
                raise DockerModImageError("Docker source activation must use an ordinary server JSON configuration file.")
            mounts.append((config.as_posix(), "/app/" + relative.as_posix()))
    result = _HEADER + "services:\n"
    for role in sorted(bindings):
        result += f'  {role}:\n    image: {bindings[role]}\n    pull_policy: never\n    environment:\n      EVEJS_MOD_DATA_ROOT: "/var/lib/evejs/mods"\n'
        if role == "server" and mounts:
            result += "    volumes:\n"
            for source, destination in sorted(mounts):
                result += ("      - type: bind\n        source: " + json.dumps(source.replace("$", "$$")) +
                    "\n        target: " + json.dumps(destination) + "\n        read_only: true\n")
    return result


def prepare_image_override(root: Path, *, policy):
    from src.core.service_status import DockerControlPolicy
    if policy is not DockerControlPolicy.MANAGED:
        raise PermissionError("Connect-only mode cannot write mod image bindings.")
    root = Path(root).resolve(strict=True)
    bindings = image_bindings(root)
    path = root / ".evejs-launcher" / "compose.images.yaml"
    _plain_path(path)
    desired = _render_override(root, bindings)
    if path.exists():
        if path.is_symlink() or not path.read_text(encoding="utf-8").startswith(_HEADER):
            raise DockerModImageError("Refusing to replace an unowned image override.")
    if not bindings:
        if path.exists():
            path.unlink()
        return None
    if not path.exists() or path.read_bytes() != desired.encode():
        _atomic(path, desired.encode())
    return path


def attach_image_override(target):
    """Pure attachment: observers and Connect-only never write image state."""
    from dataclasses import replace
    root = target.project_directory.resolve(strict=True)
    bindings = image_bindings(root)
    path = root / ".evejs-launcher" / "compose.images.yaml"
    if not bindings:
        return target
    base_inputs = base_inputs_digest(target)
    if any(record["baseInputsSha256"] != base_inputs for record in _registry(root).values() if record["image"] in bindings.values()):
        raise DockerModImageError("The base Compose image inputs changed. Recover the enabled mod image before starting this world.")
    desired = _render_override(root, bindings)
    if not path.is_file() or path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400 or path.read_bytes() != desired.encode():
        raise DockerModImageError("The Docker image selection is not prepared. Apply Mods before starting this target.")
    if path in target.override_files:
        return target
    return replace(target, override_files=(*target.override_files, path))


def require_current_image_bases(target,inspector,runner):
    """Read-only start guard for an externally retargeted base image tag."""
    from dataclasses import replace
    root=target.project_directory.resolve(strict=True)
    bindings=image_bindings(root)
    if not bindings:
        return
    owned={root/".evejs-launcher"/name for name in ("compose.images.yaml","compose.mods.yaml")}
    base_target=replace(target,override_files=tuple(path for path in target.override_files if path not in owned))
    report=inspector.preflight(base_target)
    if not report.ok or report.config is None:
        raise DockerModImageError("The selected base image authority is unavailable.")
    for record in _registry(root).values():
        if record["image"] not in bindings.values():
            continue
        service=report.config.services.get(record["service"])
        if service is None or not service.image:
            raise DockerModImageError("The selected mod base service is unavailable.")
        current=runner.run_parsed(("image","inspect","--format","{{.Id}}",service.image),cwd=root,
            parser=lambda text:text.strip(),timeout=15)
        if current != record["baseImage"] or base_inputs_digest(base_target) != record["baseInputsSha256"]:
            raise DockerModImageError("The base server image changed. Recover the enabled Docker mod image before starting this world.")
