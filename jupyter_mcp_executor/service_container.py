"""Service container for dependency injection.

Provides centralized service instantiation and management using
@cached_property for automatic lazy initialization and caching.
"""

from __future__ import annotations

import logging
from functools import cached_property
from typing import ClassVar, Protocol

from jupyter_mcp_executor.cell_operations import CellOperations
from jupyter_mcp_executor.execution_engine import ExecutionEngine
from jupyter_mcp_executor.kernel_manager import KernelManager
from jupyter_mcp_executor.query_service import QueryService

logger = logging.getLogger(__name__)


class ServiceProvider(Protocol):
    """Protocol defining the service provider interface.

    This protocol ensures type safety and allows for alternative implementations.
    """

    @property
    def execution_engine(self) -> ExecutionEngine:
        """Get ExecutionEngine instance."""
        ...

    @property
    def kernel_manager(self) -> KernelManager:
        """Get KernelManager instance."""
        ...

    @property
    def query_service(self) -> QueryService:
        """Get QueryService instance."""
        ...

    @property
    def cell_operations(self) -> CellOperations:
        """Get CellOperations instance."""
        ...

    def reset(self) -> None:
        """Reset all service instances."""
        ...


class ServiceContainer:
    """Singleton service container with lazy initialization via @cached_property.

    Uses Python's @cached_property decorator for automatic lazy initialization
    and caching. Services are created only when first accessed and then reused.

    This implementation:
    - Eliminates manual if-checks (cached_property handles it)
    - Provides perfect type inference (no type: ignore needed)
    - Uses modern Python stdlib features (functools.cached_property)
    - Supports dependency injection between services
    - Enables easy testing via property setters or override_service()
    """

    _instance: ClassVar[ServiceContainer | None] = None
    _SERVICE_ATTRS: ClassVar[list[str]] = [
        "execution_engine",
        "kernel_manager",
        "query_service",
        "cell_operations",
    ]

    def __new__(cls) -> ServiceContainer:
        """Ensure only one instance exists (singleton pattern)."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @cached_property
    def execution_engine(self) -> ExecutionEngine:
        """Get or create ExecutionEngine instance.

        Automatically initialized on first access and cached for reuse.

        Returns:
            ExecutionEngine singleton instance
        """
        logger.debug("Creating ExecutionEngine instance")
        return ExecutionEngine()

    @cached_property
    def kernel_manager(self) -> KernelManager:
        """Get or create KernelManager instance.

        Automatically initialized on first access and cached for reuse.

        Returns:
            KernelManager singleton instance
        """
        logger.debug("Creating KernelManager instance")
        return KernelManager()

    @cached_property
    def query_service(self) -> QueryService:
        """Get or create QueryService instance.

        Automatically initialized on first access and cached for reuse.

        Returns:
            QueryService singleton instance
        """
        logger.debug("Creating QueryService instance")
        return QueryService()

    @cached_property
    def cell_operations(self) -> CellOperations:
        """Get or create CellOperations instance with ExecutionEngine dependency.

        Automatically initialized on first access and cached for reuse.
        Demonstrates dependency injection - CellOperations receives ExecutionEngine.

        Returns:
            CellOperations singleton instance
        """
        logger.debug("Creating CellOperations instance")
        # Dependency injection: pass execution_engine to CellOperations
        return CellOperations(self.execution_engine)

    def reset(self) -> None:
        """Reset all service instances by clearing cached properties.

        Useful for testing to ensure clean state between tests.
        After reset, services will be re-created on next access.
        """
        for attr in self._SERVICE_ATTRS:
            # Remove from instance __dict__ to force re-creation
            self.__dict__.pop(attr, None)
        logger.debug("Reset all service instances")

    def override_service(self, attr: str, instance: object) -> None:
        """Override a specific service instance (for testing/mocking).

        This is type-safe and validates the service name.

        Args:
            attr: Name of the service property (e.g., "execution_engine")
            instance: Service instance to use

        Raises:
            ValueError: If attr is not a valid service name

        Example:
            container.override_service("execution_engine", mock_engine)
        """
        if attr not in self._SERVICE_ATTRS:
            msg = f"Unknown service: {attr}. Valid services: {', '.join(self._SERVICE_ATTRS)}"
            raise ValueError(msg)

        self.__dict__[attr] = instance
        logger.debug(f"Override {attr} with {type(instance).__name__}")


# Create and export singleton instance with type annotation
container: ServiceProvider = ServiceContainer()
