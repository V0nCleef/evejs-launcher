"""Host wrappers and Compose operations retain separate execution boundaries."""
from src.core import host
from src.core.service_status import DockerControlPolicy, RuntimeBackend
from src.core.tool_catalog import resolve_tools, ToolDispatchKind


def test_linux_native_exposes_maintenance_without_enabling_batch_wrappers(monkeypatch):
    monkeypatch.setattr(host, 'IS_LINUX', True)
    tools = resolve_tools(None)
    assert tools[0].definition.id == 'linux-maintenance'
    assert tools[0].actions[0].dispatch_kind is ToolDispatchKind.LINUX_MAINTENANCE
    assert all(not tool.available for tool in tools[1:])


def test_linux_managed_docker_keeps_semantic_tools_and_rejects_windows_wrappers(monkeypatch, tmp_path):
    monkeypatch.setattr(host, 'IS_LINUX', True)
    (tmp_path / 'compose.yaml').write_text('services: {}\n')
    tools = resolve_tools(tmp_path, backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_policy=DockerControlPolicy.MANAGED)
    actions = [action for tool in tools for action in tool.actions]
    assert any(action.dispatch_kind is ToolDispatchKind.DOCKER_COMPOSE for action in actions)
    assert all(action.dispatch_kind is not ToolDispatchKind.NATIVE_WRAPPER for action in actions)
    readonly = resolve_tools(tmp_path, backend=RuntimeBackend.DOCKER_COMPOSE,
        docker_policy=DockerControlPolicy.CONNECT_ONLY)
    assert not any(action.available for tool in readonly for action in tool.actions)
