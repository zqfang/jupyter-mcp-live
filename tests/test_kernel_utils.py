"""Tests for kernel utilities."""

import pytest

from jupyter_mcp_executor.kernel_utils import (
    detect_kernel_language_from_name,
    detect_kernel_language_from_spec,
    find_kernel_by_language,
    get_kernel_info,
    resolve_kernel_name,
)


class TestDetectKernelLanguageFromSpec:
    """Test kernel language detection from spec."""

    def test_detect_from_spec(self):
        """Test detection from kernel spec."""
        spec = {"spec": {"language": "python", "display_name": "Python 3"}}
        assert detect_kernel_language_from_spec(spec) == "python"

    def test_detect_r_from_spec(self):
        """Test R detection from spec."""
        spec = {"spec": {"language": "R", "display_name": "R"}}
        assert detect_kernel_language_from_spec(spec) == "R"

    def test_detect_custom_kernel_from_spec(self):
        """Test custom kernel with language in spec."""
        spec = {"spec": {"language": "python", "display_name": "RAPIDS (Python)"}}
        assert detect_kernel_language_from_spec(spec) == "python"

    def test_detect_no_language_in_spec(self):
        """Test spec without language field."""
        spec = {"spec": {"display_name": "Custom"}}
        assert detect_kernel_language_from_spec(spec) is None

    def test_detect_empty_spec(self):
        """Test empty spec."""
        assert detect_kernel_language_from_spec({}) is None


class TestDetectKernelLanguageFromName:
    """Test kernel language detection from name."""

    def test_detect_python_variants(self):
        """Test Python kernel name variants."""
        assert detect_kernel_language_from_name("python3") == "python"
        assert detect_kernel_language_from_name("python3.11") == "python"
        assert detect_kernel_language_from_name("python3.12") == "python"
        assert detect_kernel_language_from_name("ipython") == "python"
        assert detect_kernel_language_from_name("py3") == "python"
        # "rapids" cannot be detected without kernel spec
        assert detect_kernel_language_from_name("rapids") is None

    def test_detect_r_variants(self):
        """Test R kernel name variants."""
        assert detect_kernel_language_from_name("ir") == "R"
        assert detect_kernel_language_from_name("r") == "R"
        assert detect_kernel_language_from_name("irkernel") == "R"
        assert detect_kernel_language_from_name("r45") == "R"
        assert detect_kernel_language_from_name("R4.5") == "R"

    def test_detect_julia_variants(self):
        """Test Julia kernel name variants."""
        assert detect_kernel_language_from_name("julia-1.9") == "julia"
        assert detect_kernel_language_from_name("julia-1.10") == "julia"
        assert detect_kernel_language_from_name("julia") == "julia"

    def test_detect_javascript_variants(self):
        """Test JavaScript kernel name variants."""
        assert detect_kernel_language_from_name("javascript") == "javascript"
        assert detect_kernel_language_from_name("node") == "javascript"
        assert detect_kernel_language_from_name("nodejs") == "javascript"

    def test_detect_unknown_kernel(self):
        """Test unknown kernel name."""
        assert detect_kernel_language_from_name("unknown_kernel") is None
        assert detect_kernel_language_from_name("custom") is None


class TestFindKernelByLanguage:
    """Test finding kernel by language."""

    def test_find_python_kernel(self):
        """Test finding Python kernel."""
        specs = {
            "python3": {"spec": {"language": "python"}},
            "rapids": {"spec": {"language": "python"}},
            "ir": {"spec": {"language": "R"}},
        }
        result = find_kernel_by_language(specs, "python")
        assert result in ["python3", "rapids"]

    def test_find_r_kernel(self):
        """Test finding R kernel."""
        specs = {
            "python3": {"spec": {"language": "python"}},
            "ir": {"spec": {"language": "R"}},
            "r45": {"spec": {"language": "R"}},
        }
        result = find_kernel_by_language(specs, "R")
        assert result in ["ir", "r45"]

    def test_find_with_preference(self):
        """Test finding with preferred name."""
        specs = {
            "python3": {"spec": {"language": "python"}},
            "rapids": {"spec": {"language": "python"}},
        }
        result = find_kernel_by_language(specs, "python", prefer_name="rapids")
        assert result == "rapids"

    def test_find_case_insensitive(self):
        """Test case-insensitive language matching."""
        specs = {
            "ir": {"spec": {"language": "R"}},
        }
        assert find_kernel_by_language(specs, "r") == "ir"
        assert find_kernel_by_language(specs, "R") == "ir"

    def test_find_not_found(self):
        """Test when language not found."""
        specs = {
            "python3": {"spec": {"language": "python"}},
        }
        assert find_kernel_by_language(specs, "julia") is None


class TestResolveKernelName:
    """Test kernel name resolution."""

    def test_exact_match(self):
        """Test exact kernel name match."""
        specs = {
            "python3": {"spec": {"language": "python"}},
            "rapids": {"spec": {"language": "python"}},
        }
        assert resolve_kernel_name("rapids", specs) == "rapids"

    def test_language_match(self):
        """Test language-based resolution."""
        specs = {
            "ir": {"spec": {"language": "R"}},
            "r45": {"spec": {"language": "R"}},
        }
        result = resolve_kernel_name("r", specs)
        assert result in ["ir", "r45"]

    def test_pattern_detection(self):
        """Test pattern-based detection."""
        specs = {
            "python3": {"spec": {"language": "python"}},
            "rapids": {"spec": {"language": "python"}},
        }
        # "python" in name should match python kernels
        result = resolve_kernel_name("python", specs)
        assert result in ["python3", "rapids"]

    def test_unresolvable_raises_with_available_kernels(self):
        """Without a fallback, an unknown kernel raises listing what exists."""
        specs = {
            "python3": {"spec": {"language": "python"}},
        }
        with pytest.raises(ValueError, match="python3"):
            resolve_kernel_name("nonexistent", specs)

    def test_explicit_fallback(self):
        """An explicit fallback is honored instead of raising."""
        specs = {
            "python3": {"spec": {"language": "python"}},
        }
        assert (
            resolve_kernel_name("nonexistent", specs, fallback="python3") == "python3"
        )

    def test_custom_fallback(self):
        """Test custom fallback kernel."""
        specs = {
            "python3": {"spec": {"language": "python"}},
            "rapids": {"spec": {"language": "python"}},
        }
        result = resolve_kernel_name("nonexistent", specs, fallback="rapids")
        assert result == "rapids"


class TestGetKernelInfo:
    """Test getting kernel information."""

    def test_get_info_from_spec(self):
        """Test getting info from kernel spec."""
        specs = {
            "rapids": {
                "spec": {"language": "python", "display_name": "RAPIDS (Python)"}
            }
        }
        info = get_kernel_info("rapids", specs)
        assert info["language"] == "python"
        assert info["display_name"] == "RAPIDS (Python)"

    def test_get_info_r_kernel(self):
        """Test getting info for R kernel."""
        specs = {"r45": {"spec": {"language": "R", "display_name": "R 4.5"}}}
        info = get_kernel_info("r45", specs)
        assert info["language"] == "R"
        assert info["display_name"] == "R 4.5"

    def test_get_info_fallback(self):
        """Test fallback when kernel not in specs."""
        specs = {}
        info = get_kernel_info("custom_kernel", specs)
        assert info["display_name"] == "custom_kernel"
        # Without spec or detectable pattern, uses kernel name as language
        assert info["language"] == "custom_kernel"

    def test_get_info_with_pattern_detection(self):
        """Test fallback with pattern detection."""
        specs = {}
        # r45 should be detected as R from pattern
        info = get_kernel_info("r45", specs)
        assert info["language"] == "R"
        assert info["display_name"] == "r45"

        # python3 should be detected as python
        info = get_kernel_info("python3", specs)
        assert info["language"] == "python"
        assert info["display_name"] == "python3"


class TestIntegrationScenarios:
    """Test real-world integration scenarios."""

    def test_ai_agent_creates_rapids_notebook(self):
        """Test AI agent creating notebook with RAPIDS kernel."""
        specs = {
            "python3": {"spec": {"language": "python", "display_name": "Python 3"}},
            "rapids": {
                "spec": {"language": "python", "display_name": "RAPIDS (Python)"}
            },
        }

        # Agent requests "rapids" - should get exact match
        kernel_name = resolve_kernel_name("rapids", specs)
        assert kernel_name == "rapids"

        info = get_kernel_info(kernel_name, specs)
        assert info["language"] == "python"
        assert info["display_name"] == "RAPIDS (Python)"

    def test_ai_agent_creates_r45_notebook(self):
        """Test AI agent creating notebook with R 4.5 kernel."""
        specs = {
            "python3": {"spec": {"language": "python", "display_name": "Python 3"}},
            "r45": {"spec": {"language": "R", "display_name": "R 4.5"}},
        }

        # Agent requests "r45" - should get exact match
        kernel_name = resolve_kernel_name("r45", specs)
        assert kernel_name == "r45"

        info = get_kernel_info(kernel_name, specs)
        assert info["language"] == "R"
        assert info["display_name"] == "R 4.5"

    def test_ai_agent_requests_r_language(self):
        """Test AI agent requesting 'R' language gets custom R kernel."""
        specs = {
            "python3": {"spec": {"language": "python", "display_name": "Python 3"}},
            "r45": {"spec": {"language": "R", "display_name": "R 4.5"}},
        }

        # Agent requests "R" language - should find r45
        kernel_name = resolve_kernel_name("R", specs)
        assert kernel_name == "r45"

    def test_ai_agent_requests_python_gets_rapids(self):
        """Test AI agent requesting 'python' can get RAPIDS."""
        specs = {
            "rapids": {
                "spec": {"language": "python", "display_name": "RAPIDS (Python)"}
            },
            "python3": {"spec": {"language": "python", "display_name": "Python 3"}},
        }

        # Agent requests "python" - should get first match (rapids)
        kernel_name = resolve_kernel_name("python", specs)
        assert kernel_name in ["rapids", "python3"]

    def test_backwards_compatibility(self):
        """Test backwards compatibility with old kernel names."""
        specs = {
            "python3": {"spec": {"language": "python", "display_name": "Python 3"}},
            "ir": {"spec": {"language": "R", "display_name": "R"}},
        }

        # Old-style requests should still work
        assert resolve_kernel_name("python3", specs) == "python3"
        assert resolve_kernel_name("ir", specs) == "ir"
        assert resolve_kernel_name("r", specs) == "ir"
        assert resolve_kernel_name("irkernel", specs) == "ir"
