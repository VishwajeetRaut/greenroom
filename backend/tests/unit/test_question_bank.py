"""Unit tests for question_bank.parse_function_name.

Covers the two function_name conventions in the bank: plain functions
(CodeContests-derived, e.g. "two_sum") and LeetCode-style class methods
encoded as "Solution().methodName" (needed verbatim by test_runner, which
executes tests["call"] as literal Python/JS — see parse_function_name's
docstring for why this isn't a data bug to "fix" at the source).
"""
import pytest

from services.question_bank import parse_function_name


def test_plain_function_name():
    assert parse_function_name("two_sum") == (None, "two_sum")


def test_class_method_function_name():
    assert parse_function_name("Solution().longestPalindromicSubsequence") == (
        "Solution", "longestPalindromicSubsequence",
    )


def test_class_method_with_different_class_name():
    assert parse_function_name("LRUCache().get") == ("LRUCache", "get")


@pytest.mark.parametrize("raw", [None, ""])
def test_empty_or_none_returns_empty_method(raw):
    assert parse_function_name(raw) == (None, "")


def test_malformed_looking_string_falls_back_to_raw():
    # No "()." separator — treated as a plain (if unusual) function name rather
    # than guessed apart, so callers still get *something* usable.
    assert parse_function_name("weird.name") == (None, "weird.name")
