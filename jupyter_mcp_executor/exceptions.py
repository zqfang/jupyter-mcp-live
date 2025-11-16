"""Custom exceptions for jupyter-mcp-executor.

Provides a consistent error hierarchy for the service layer.
"""


class ServiceError(Exception):
    """Base exception for all service layer errors.

    All custom exceptions in the service layer should inherit from this class.
    This allows for easy catching of all service-related errors.
    """


class NotebookNotFoundError(ServiceError):
    """Raised when a notebook file cannot be found."""


class KernelError(ServiceError):
    """Base exception for kernel-related errors."""


class KernelNotFoundError(KernelError):
    """Raised when a kernel cannot be found or has died."""


class KernelStartupError(KernelError):
    """Raised when a kernel fails to start."""


class ExecutionError(ServiceError):
    """Raised when code execution fails in a kernel."""


class CellNotFoundError(ServiceError):
    """Raised when a cell cannot be found by the specified identifier."""


class InvalidCellPositionError(ServiceError):
    """Raised when a cell position is out of range."""


class ServerContextError(ServiceError):
    """Raised when Jupyter server context is not available."""


class AdapterError(ServiceError):
    """Base exception for notebook adapter errors."""


class YDocError(AdapterError):
    """Raised when YDoc operations fail."""
