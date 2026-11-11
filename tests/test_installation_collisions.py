from types import SimpleNamespace

import pytest

from src.core.installation_profiles import ensure_profiles, add_profile
from src.core.runtime.docker_compose import ComposeTarget, ContainerRecord
from src.core.runtime.installation_collisions import require_distinct_installation_resources
from src.core.service_status import ServiceState


@pytest.mark.parametrize("kind,other_running,reject", [
    ("volume",False,True),("data bind",False,True),
    ("network",False,False),("network",True,True),
    ("port",False,False),("port",True,True),
])
def test_colliding_data_is_always_blocked_and_running_endpoints_are_blocked(tmp_path,kind,other_running,reject):
    for name in ("first","other"):
        (tmp_path/name).mkdir()
        (tmp_path/name/"compose.yaml").write_text("services: {}")
    cfg=add_profile(ensure_profiles({"evejs_root":str(tmp_path/"first"),"runtime_backend":"docker_compose"}),
        "Other",{"evejs_root":str(tmp_path/"other"),"runtime_backend":"docker_compose"})
    target=ComposeTarget(tmp_path/"first/compose.yaml",tmp_path/"first","first")
    stopped=ContainerRecord.absent("server")
    active=ContainerRecord("server","other-server","other-id",ServiceState.ONLINE,"healthy",None,(),raw_state="running")
    reports=[SimpleNamespace(ok=True,config=SimpleNamespace(project_name="first",resource_keys=((kind,"shared"),))),
        SimpleNamespace(ok=True,config=SimpleNamespace(project_name="other",resource_keys=((kind,"shared"),)),
            records={"server":active if other_running else stopped})]
    inspector=SimpleNamespace(preflight=lambda _: reports.pop(0))
    if reject:
        with pytest.raises(RuntimeError,match="Other.*shares Docker resources"):
            require_distinct_installation_resources(cfg,target,inspector)
    else:
        require_distinct_installation_resources(cfg,target,inspector)


def test_duplicate_compose_project_is_rejected_even_with_no_running_container(tmp_path):
    for name in ("first","other"):
        (tmp_path/name).mkdir()
        (tmp_path/name/"compose.yaml").write_text("services: {}")
    cfg=add_profile(ensure_profiles({"evejs_root":str(tmp_path/"first"),"runtime_backend":"docker_compose"}),
        "Other",{"evejs_root":str(tmp_path/"other"),"runtime_backend":"docker_compose"})
    target=ComposeTarget(tmp_path/"first/compose.yaml",tmp_path/"first","same")
    report=SimpleNamespace(ok=True,config=SimpleNamespace(project_name="same",resource_keys=()),
        records={"server":ContainerRecord.absent("server")})
    with pytest.raises(RuntimeError,match="same Compose project"):
        require_distinct_installation_resources(cfg,target,SimpleNamespace(preflight=lambda _:report))


@pytest.mark.parametrize("unavailable", ["path", "inspection"])
def test_uninspectable_registered_world_is_not_silently_assumed_safe(tmp_path, unavailable):
    for name in ("first", "other"):
        (tmp_path/name).mkdir()
        (tmp_path/name/"compose.yaml").write_text("services: {}")
    cfg = add_profile(ensure_profiles({"evejs_root": str(tmp_path/"first"), "runtime_backend": "docker_compose"}),
        "Other", {"evejs_root": str(tmp_path/"other"), "runtime_backend": "docker_compose"})
    if unavailable == "path":
        (tmp_path/"other/compose.yaml").unlink()
    selected = SimpleNamespace(ok=True, config=SimpleNamespace(project_name="first", resource_keys=()))
    calls = []
    def inspect(target):
        calls.append(target)
        return selected if len(calls) == 1 else SimpleNamespace(ok=False, config=None, records=None)
    with pytest.raises(RuntimeError, match="Other.*could not be checked"):
        require_distinct_installation_resources(cfg, ComposeTarget(tmp_path/"first/compose.yaml", tmp_path/"first"),
            SimpleNamespace(preflight=inspect))


@pytest.mark.parametrize("reverse", [False, True])
def test_parent_child_data_bind_collision_is_rejected(tmp_path, reverse):
    for name in ("first", "other"):
        (tmp_path/name).mkdir()
        (tmp_path/name/"compose.yaml").write_text("services: {}")
    cfg = add_profile(ensure_profiles({"evejs_root": str(tmp_path/"first"), "runtime_backend": "docker_compose"}),
        "Other", {"evejs_root": str(tmp_path/"other"), "runtime_backend": "docker_compose"})
    roots = [tmp_path/"shared-data", tmp_path/"shared-data/gameStore"]
    if reverse:
        roots.reverse()
    reports = [SimpleNamespace(ok=True, config=SimpleNamespace(project_name=name,
        resource_keys=(("data bind", str(root)),)), records={"server": ContainerRecord.absent("server")})
        for name, root in zip(("first", "other"), roots)]
    with pytest.raises(RuntimeError, match="shares Docker resources.*data bind"):
        require_distinct_installation_resources(cfg, ComposeTarget(tmp_path/"first/compose.yaml", tmp_path/"first"),
            SimpleNamespace(preflight=lambda _: reports.pop(0)))
