"""Resolve a host Node executable without changing Windows command contracts."""
from .host import IS_LINUX, external_process_environment


def node_executable(root):
    if IS_LINUX:
        from .linux_installation import LinuxInstallation
        return str(LinuxInstallation.load(root).node)
    return 'node'


def node_environment(root):
    if IS_LINUX:
        from .linux_installation import LinuxInstallation
        return LinuxInstallation.load(root).server_environment()
    return external_process_environment()
