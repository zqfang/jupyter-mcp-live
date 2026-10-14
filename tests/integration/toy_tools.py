"""Minimal tools registered by the integration tests."""


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


try:
    from jupyter_server.auth import Authorizer
except ImportError:  # pragma: no cover - jupyter_server is a dependency
    Authorizer = object


class DenyKernelExecuteAuthorizer(Authorizer):
    """Authorizer that forbids executing code in kernels, allows the rest."""

    def is_authorized(self, handler, user, action, resource) -> bool:
        return not (action == "execute" and resource == "kernels")


class ReadOnlyContentsAuthorizer(Authorizer):
    """Read-only collaborator: may run code, may not modify files."""

    def is_authorized(self, handler, user, action, resource) -> bool:
        return not (action == "write" and resource == "contents")


class NoContentsReadAuthorizer(Authorizer):
    """May run code, may not read files (each contents check on its own)."""

    def is_authorized(self, handler, user, action, resource) -> bool:
        return not (action == "read" and resource == "contents")
