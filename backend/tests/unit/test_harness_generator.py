"""Unit tests for the Python/JS function-signature verification logic.

Only _verify_signature is tested here — it's the pure, deterministic part of
the pipeline. _generate_signature calls the LLM and is exercised indirectly by
the fail-open contract: if verification rejects the output, callers fall back
to the generic starter code, same as an LLM/network failure.
"""
import pytest

from services.harness_generator import _verify_signature

# ── Python ───────────────────────────────────────────────────────────────────

def test_python_valid_signature_passes():
    code = "def two_sum(nums, target):\n    pass\n"
    assert _verify_signature("python", code, "two_sum")


def test_python_wrong_function_name_fails():
    code = "def sum_two(nums, target):\n    pass\n"
    assert not _verify_signature("python", code, "two_sum")


def test_python_syntax_error_fails():
    code = "def two_sum(nums, target)\n    pass\n"  # missing colon
    assert not _verify_signature("python", code, "two_sum")


def test_python_method_inside_class_passes():
    code = "class LRUCache:\n    def __init__(self, capacity):\n        pass\n\n    def get(self, key):\n        pass\n"
    assert _verify_signature("python", code, "get")


def test_python_empty_code_fails():
    assert not _verify_signature("python", "", "two_sum")


# ── JavaScript ("node") ──────────────────────────────────────────────────────

@pytest.mark.parametrize("code", [
    "function twoSum(nums, target) {\n  // TODO: implement\n}\n",
    "const twoSum = (nums, target) => {\n  // TODO: implement\n};\n",
    "const twoSum = function(nums, target) {\n  // TODO: implement\n};\n",
    "class Solution {\n  twoSum(nums, target) {\n    // TODO: implement\n  }\n}\n",
])
def test_node_valid_signature_passes(code):
    assert _verify_signature("node", code, "twoSum")


def test_node_wrong_function_name_fails():
    code = "function sumTwo(nums, target) {\n  // TODO: implement\n}\n"
    assert not _verify_signature("node", code, "twoSum")


def test_node_empty_code_fails():
    assert not _verify_signature("node", "", "twoSum")


def test_unsupported_language_fails():
    assert not _verify_signature("java", "class Solution {}", "twoSum")
