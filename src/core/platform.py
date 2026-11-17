"""Host implementation selected before any operating-system API is imported."""

from __future__ import annotations

from .host import IS_LINUX, IS_WINDOWS

if IS_WINDOWS:
    from . import platform_win as _implementation
elif IS_LINUX:
    from . import platform_linux as _implementation
else:
    raise RuntimeError("EveJS Launcher supports Windows and Linux hosts.")


# Re-export everything so callers do ``from .platform import create_directory_link``.
__all__ = [
    "build_tool_batch_command",
    "center_tool_window_for_process_tree",
    "close_job",
    "create_kill_on_close_job",
    "create_directory_link",
    "find_and_focus_eve_window",
    "find_and_focus_eve_window_for_pid",
    "find_eve_window",
    "get_client_exe_name",
    "get_client_exe_path",
    "get_client_process_flags",
    "get_eve_settings_path",
    "get_exe_file_filter",
    "get_graceful_server_process_flags",
    "get_hidden_process_flags",
    "has_visible_window_for_pid",
    "get_suspended_hidden_process_flags",
    "get_market_binary_name",
    "hard_exit",
    "launch_eve_client",
    "launch_tool_wrapper",
    "open_text_editor",
    "prepare_evejs_client_certificate_trust",
    "remove_directory_link",
    "request_graceful_server_shutdown",
    "resume_process",
    "run_updater",
    "serialize_evejs_client_trust_and_spawn",
    "terminate_job",
    "terminate_process_tree",
]

for _name in __all__:
    globals()[_name] = getattr(_implementation, _name)
