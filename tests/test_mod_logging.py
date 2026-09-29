"""Failed mod operations must leave usable, bounded support diagnostics."""
import logging

import pytest

from src.utils import logger as launcher_logging
from src.workers.mod_operation_worker import ModOperationWorker
from src.core import mod_api_runtime as runtime
from test_mod_api_runtime_protocol import make_mod, helper_runner


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    root = logging.getLogger()
    entry = logging.getLogger("logging-support-test")
    saved = [(item, list(item.handlers), item.level, item.propagate) for item in (root, entry)]
    path = tmp_path / "logs" / "launcher.log"
    monkeypatch.setattr(launcher_logging, "_LOG_DIR", path.parent)
    monkeypatch.setattr(launcher_logging, "_LOG_FILE", path)
    launcher_logging.setup_logger(entry.name)
    yield path
    closed = set()
    for item, handlers, level, propagate in saved:
        for handler in list(item.handlers):
            if handler not in handlers:
                item.removeHandler(handler)
                if handler not in closed:
                    handler.close()
                    closed.add(handler)
        item.setLevel(level)
        item.propagate = propagate


def test_worker_failure_is_written_without_successful_mod_activation(log_file, qapp):
    def failing():
        raise RuntimeError("DLSS5 preparation timed out")

    results = []
    worker = ModOperationWorker(object(), failing)
    worker.completed.connect(results.append)
    worker.run()
    text = log_file.read_text(encoding="utf-8")
    assert "Mod operation started" in text
    assert "Mod operation failed" in text
    assert "DLSS5 preparation timed out" in text
    assert "Traceback" in text and "elapsed_seconds=" in text
    assert len(results) == 1 and not results[0].success


def test_worker_success_is_logged_once_after_repeated_logger_setup(log_file, qapp):
    launcher_logging.setup_logger("logging-support-test")
    worker = ModOperationWorker(object(), lambda: "done")
    worker.run()
    text = log_file.read_text(encoding="utf-8")
    assert text.count("Mod operation completed") == 1
    assert "elapsed_seconds=" in text


def test_failure_message_and_traceback_redact_credentials(log_file, qapp):
    def failing():
        raise RuntimeError('/login:pilot:login-secret password="password-secret" token=token-secret\nAuthorization: Bearer header-secret')

    ModOperationWorker(object(), failing).run()
    text = log_file.read_text(encoding="utf-8")
    for value in ("login-secret", "password-secret", "token-secret", "header-secret"):
        assert value not in text
    assert "[REDACTED]" in text


def test_failed_helper_records_mod_action_and_exact_diagnostic_directory(log_file, tmp_path):
    descriptor, context = make_mod(tmp_path, name="evejs-dlss5", version="0.5.9")
    (context.evejs_root / "package.json").write_text('{"version":"0.12.9"}', encoding="utf-8")
    def fail_reply(reply, request):
        reply.update(success=False, state="failed", message="DLSS5 preparation timed out")
    result = runtime.run_mod_helper(descriptor, "install", context,
        runner=helper_runner(receipt=False, mutate=fail_reply))
    text = log_file.read_text(encoding="utf-8")
    assert "mod=evejs-dlss5 version=0.5.9 action=install" in text
    assert "success=False" in text and "DLSS5 preparation timed out" in text
    assert str(result.request_path.parent) in text
    assert result.request_id in text
    assert "evejs_version=0.12.9" in text
    assert str(context.evejs_root) in text and str(context.client_root) in text


def test_timeout_before_reply_still_records_diagnostics(log_file, tmp_path):
    descriptor, context = make_mod(tmp_path)
    folders = []
    def timeout(command, **kwargs):
        folders.append(kwargs["output_directory"])
        raise runtime.ModApiRuntimeError("The mod helper timed out; its process tree was stopped.")
    with pytest.raises(runtime.ModApiRuntimeError, match="timed out"):
        runtime.run_mod_helper(descriptor, "install", context, runner=timeout)
    text = log_file.read_text(encoding="utf-8")
    assert "Mod helper failed" in text
    assert str(folders[0]) in text and "elapsed_seconds=" in text
    assert (folders[0] / "request.json").exists()


def test_successful_helper_records_completion_without_dumping_output(log_file, tmp_path):
    descriptor, context = make_mod(tmp_path)
    result = runtime.run_mod_helper(descriptor, "verify", context, runner=helper_runner())
    text = log_file.read_text(encoding="utf-8")
    assert "Mod helper completed" in text and "success=True" in text
    assert result.request_id in text
    assert '"settings"' not in text and '"environment"' not in text
