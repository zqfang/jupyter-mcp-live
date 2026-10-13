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
