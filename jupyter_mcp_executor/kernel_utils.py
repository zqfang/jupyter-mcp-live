"""Utilities for kernel detection and language resolution."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def is_kernel_alive(serverapp: Any, kernel_id: str | None) -> bool:
    """Return True if `kernel_id` is still running on this server.

    Do not inline this as `except KeyError`. jupyter_client's MultiKernelManager
    raises KeyError for an unknown id, but jupyter_server's MappingKernelManager
    overrides _check_kernel_id to raise ``tornado.web.HTTPError(404)`` instead
    (kernelmanager.py: 'Kernel does not exist: %s'). A KeyError-only guard therefore
    never fires under a real server: the 404 escapes to the MCP caller as a tool
    error, every "kernel died -> fall back" branch becomes dead code, and the caller
    is pushed into force_new_kernel, which is how one notebook ends up with several
    kernels.
    """
    if not serverapp or not kernel_id:
        return False
    try:
        serverapp.kernel_manager.get_kernel(kernel_id)
    except Exception:  # KeyError (jupyter_client) or web.HTTPError 404 (jupyter_server)
        return False
    return True


def detect_kernel_language_from_spec(kernel_spec: dict[str, Any]) -> str | None:
    """Detect programming language from kernel spec.

    Args:
        kernel_spec: Kernel specification dictionary from jupyter kernelspecs

    Returns:
        Language name (e.g., "python", "R", "julia") or None if not detectable

    Example:
        >>> spec = {"spec": {"language": "python", "display_name": "Python 3"}}
        >>> detect_kernel_language_from_spec(spec)
        'python'

        >>> spec = {"spec": {"language": "R", "display_name": "RAPIDS (Python)"}}
        >>> detect_kernel_language_from_spec(spec)
        'R'
    """
    # Try to get language from spec
    if isinstance(kernel_spec, dict):
        spec = kernel_spec.get("spec", {})
        if "language" in spec:
            return spec["language"]

    return None


def detect_kernel_language_from_name(kernel_name: str) -> str | None:
    """Detect programming language from kernel name using pattern matching.

    This is a fallback heuristic when kernel spec is not available.
    It handles custom kernel names like 'rapids' (python), 'r45' (R), etc.

    Args:
        kernel_name: Kernel name (e.g., "rapids", "r45", "python3.11")

    Returns:
        Language name or None if not detectable

    Example:
        >>> detect_kernel_language_from_name("rapids")
        None  # Cannot detect without kernel spec

        >>> detect_kernel_language_from_name("r45")
        'R'

        >>> detect_kernel_language_from_name("python3.11")
        'python'

        >>> detect_kernel_language_from_name("custom_kernel")
        None
    """
    kernel_lower = kernel_name.lower()

    # Priority order: check longer/more specific patterns first
    # Python patterns - must start with or be the pattern
    python_patterns = ["python", "py", "ipython"]
    for pattern in python_patterns:
        if kernel_lower.startswith(pattern) or kernel_lower == pattern:
            return "python"

    # R patterns - be more specific to avoid false matches
    # Match if starts with 'r' followed by digit, or exact matches
    if (
        kernel_lower.startswith("r")
        and len(kernel_lower) > 1
        and kernel_lower[1].isdigit()
    ):
        return "R"
    if kernel_lower in ["r", "ir", "irkernel"]:
        return "R"

    # Julia patterns
    julia_patterns = ["julia", "jl"]
    for pattern in julia_patterns:
        if kernel_lower.startswith(pattern) or kernel_lower == pattern:
            return "julia"

    # JavaScript patterns
    javascript_patterns = ["javascript", "node"]
    for pattern in javascript_patterns:
        if kernel_lower.startswith(pattern) or kernel_lower == pattern:
            return "javascript"

    return None


def find_kernel_by_language(
    kernelspecs: dict[str, Any], language: str, prefer_name: str | None = None
) -> str | None:
    """Find a kernel name that supports the specified language.

    Args:
        kernelspecs: Available kernel specifications
        language: Desired language (e.g., "python", "R", "julia")
        prefer_name: Preferred kernel name if multiple matches

    Returns:
        Kernel name or None if not found

    Example:
        >>> specs = {
        ...     "python3": {"spec": {"language": "python"}},
        ...     "rapids": {"spec": {"language": "python"}},
        ...     "ir": {"spec": {"language": "R"}},
        ... }
        >>> find_kernel_by_language(specs, "python")
        'python3'

        >>> find_kernel_by_language(specs, "python", prefer_name="rapids")
        'rapids'

        >>> find_kernel_by_language(specs, "R")
        'ir'
    """
    matches = []

    for kernel_name, kernel_spec in kernelspecs.items():
        detected_lang = detect_kernel_language_from_spec(kernel_spec)

        # Normalize language comparison (case-insensitive)
        if detected_lang and detected_lang.lower() == language.lower():
            matches.append(kernel_name)

    if not matches:
        return None

    # If preferred name is in matches, return it
    if prefer_name and prefer_name in matches:
        return prefer_name

    # Return first match (typically the "default" one)
    return matches[0]


def resolve_kernel_name(
    requested_name: str, kernelspecs: dict[str, Any], fallback: str | None = None
) -> str:
    """Resolve a kernel name to an available kernel.

    This function handles:
    1. Exact match: kernel name exists in kernelspecs
    2. Language match: requested name is a language, find matching kernel
    3. Fallback: return fallback kernel if provided, otherwise raise

    Args:
        requested_name: Requested kernel name or language
        kernelspecs: Available kernel specifications
        fallback: Fallback kernel name if resolution fails. When None
            (default), an unresolvable name raises ValueError instead of
            silently running code on the wrong kernel.

    Returns:
        Resolved kernel name

    Raises:
        ValueError: If the name cannot be resolved and no fallback is given.
            The message lists the available kernels.

    Example:
        >>> specs = {
        ...     "python3": {"spec": {"language": "python"}},
        ...     "rapids": {"spec": {"language": "python"}},
        ...     "r45": {"spec": {"language": "R"}},
        ... }

        # Exact match
        >>> resolve_kernel_name("rapids", specs)
        'rapids'

        # Language match
        >>> resolve_kernel_name("r", specs)
        'r45'

        >>> resolve_kernel_name("python", specs)
        'python3'

        # Explicit fallback
        >>> resolve_kernel_name("nonexistent", specs, fallback="python3")
        'python3'
    """
    # 1. Check for exact match
    if requested_name in kernelspecs:
        return requested_name

    # 2. Try language-based resolution
    # First check if requested_name IS a language
    language_match = find_kernel_by_language(kernelspecs, requested_name)
    if language_match:
        logger.info(
            f"Resolved language '{requested_name}' to kernel '{language_match}'"
        )
        return language_match

    # 3. Try to detect language from requested name pattern
    detected_language = detect_kernel_language_from_name(requested_name)
    if detected_language:
        language_match = find_kernel_by_language(kernelspecs, detected_language)
        if language_match:
            logger.info(
                f"Detected language '{detected_language}' from '{requested_name}', "
                f"using kernel '{language_match}'"
            )
            return language_match

    # 4. Fallback or fail loudly
    available = list(kernelspecs.keys())
    if fallback is not None:
        logger.warning(
            f"Kernel '{requested_name}' not found. "
            f"Available: {available}. Using fallback: {fallback}"
        )
        return fallback

    msg = (
        f"Kernel '{requested_name}' not found and no matching language kernel "
        f"is installed. Available kernels: {available}"
    )
    raise ValueError(msg)


def get_kernel_info(kernel_name: str, kernelspecs: dict[str, Any]) -> dict[str, str]:
    """Get kernel information including language and display name.

    Args:
        kernel_name: Kernel name
        kernelspecs: Available kernel specifications

    Returns:
        Dict with 'language' and 'display_name' keys

    Example:
        >>> specs = {
        ...     "rapids": {
        ...         "spec": {"language": "python", "display_name": "RAPIDS (Python)"}
        ...     }
        ... }
        >>> get_kernel_info("rapids", specs)
        {'language': 'python', 'display_name': 'RAPIDS (Python)'}
    """
    if kernel_name in kernelspecs:
        spec = kernelspecs[kernel_name].get("spec", {})
        return {
            "language": spec.get("language", kernel_name),
            "display_name": spec.get("display_name", kernel_name),
        }

    # Fallback: try to detect language from name
    detected_language = detect_kernel_language_from_name(kernel_name)
    return {"language": detected_language or kernel_name, "display_name": kernel_name}
