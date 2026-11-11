"""Read-only checks before starting a registered local Docker world."""
from pathlib import Path
from src.core.installation_profiles import sync_active_profile
from src.core.runtime.docker_setup import DockerSetupDraft, build_compose_target


def require_distinct_installation_resources(cfg, target, inspector):
    profiles = sync_active_profile(cfg)
    entries = profiles["installation_profiles"]
    if len(entries) < 2:
        return
    selected = inspector.preflight(target)
    if not selected.ok or selected.config is None:
        raise RuntimeError("The selected installation's Docker authority could not be checked.")
    resources = set(selected.config.resource_keys)
    for entry in entries:
        values = entry["settings"]
        if entry["id"] == profiles["active_installation_profile"] or values.get("runtime_backend") != "docker_compose":
            continue
        try:
            other = build_compose_target(DockerSetupDraft(values["evejs_root"], values["docker_compose_file"],
                values["docker_project_name"], values["docker_control_policy"], values["docker_keep_running_on_exit"],
                values["client_path"]))
            report = inspector.preflight(other)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Installation '{entry['name']}' could not be checked for shared Docker resources. "
                               "Repair its registration or remove the inactive registration before starting another world.") from exc
        if not report.ok or report.config is None or report.records is None:
            raise RuntimeError(f"Installation '{entry['name']}' could not be checked for shared Docker resources. "
                               "Repair its registration or remove the inactive registration before starting another world.")
        if report.config.project_name == selected.config.project_name:
            raise RuntimeError(f"Installation '{entry['name']}' uses the same Compose project. Choose distinct projects before starting.")
        shared = resources.intersection(report.config.resource_keys)
        # Different bind roots can still overlap (for example data/ and
        # data/gameStore/). Exact string equality misses that shared writer.
        selected_binds = [Path(value) for kind, value in resources if kind == "data bind"]
        other_binds = [Path(value) for kind, value in report.config.resource_keys if kind == "data bind"]
        if any(left == right or left in right.parents or right in left.parents
               for left in selected_binds for right in other_binds):
            shared.add(("data bind", "overlapping directories"))
        running = any(record.exists and record.raw_state not in {"created", "exited", "dead"}
                      for record in report.records.values())
        conflicts = sorted({kind for kind, _ in shared if kind in {"volume", "data bind"} or running})
        if conflicts:
            raise RuntimeError(f"Installation '{entry['name']}' shares Docker resources ({', '.join(conflicts)}). "
                               "Use separate data and endpoints; no resources were renamed or moved.")
