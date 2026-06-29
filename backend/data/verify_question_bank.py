"""One-off verification script — not part of the app.
Runs canonical reference solutions against every test case in question_bank.json
to prove the `expected` values are actually correct (not LLM-guessed)."""
import json
import os

# ── Canonical reference solutions ────────────────────────────────────────────

def two_sum(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        if target - n in seen:
            return [seen[target - n], i]
        seen[n] = i
    return []

def valid_parentheses(s):
    pairs = {')': '(', ']': '[', '}': '{'}
    stack = []
    for c in s:
        if c in '([{':
            stack.append(c)
        elif c in pairs:
            if not stack or stack.pop() != pairs[c]:
                return False
    return not stack

def binary_search(nums, target):
    lo, hi = 0, len(nums) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1

def max_subarray(nums):
    best = cur = nums[0]
    for n in nums[1:]:
        cur = max(n, cur + n)
        best = max(best, cur)
    return best

def valid_anagram(s, t):
    return sorted(s) == sorted(t)

def merge_intervals(intervals):
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged

class LRUCache:
    def __init__(self, capacity):
        from collections import OrderedDict
        self.cache = OrderedDict()
        self.capacity = capacity

    def get(self, key):
        if key not in self.cache:
            return -1
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

class MinStack:
    def __init__(self):
        self.stack = []
        self.min_stack = []

    def push(self, val):
        self.stack.append(val)
        if not self.min_stack or val <= self.min_stack[-1]:
            self.min_stack.append(val)
        else:
            self.min_stack.append(self.min_stack[-1])

    def pop(self):
        self.stack.pop()
        self.min_stack.pop()

    def top(self):
        return self.stack[-1]

    def getMin(self):
        return self.min_stack[-1]


# ── Verification ──────────────────────────────────────────────────────────────

# Only the hand-written seed questions have reference solutions in this file.
# The 210 imported LeetCodeDataset entries were already verified at import time
# (scripts/import_leetcode_dataset.py, run sandboxed in Docker) against their
# own dataset-provided canonical solutions — this script doesn't re-check those.
_HAND_WRITTEN_IDS = {
    "two-sum", "valid-parentheses", "binary-search", "max-subarray",
    "valid-anagram", "merge-intervals", "lru-cache", "min-stack",
}


def main():
    path = os.path.join(os.path.dirname(__file__), "question_bank.json")
    with open(path) as f:
        bank = json.load(f)
    bank = [q for q in bank if q["id"] in _HAND_WRITTEN_IDS]

    ns = globals()
    failures = []
    total = 0
    for question in bank:
        for tc in question["tests"]:
            total += 1
            try:
                actual = eval(compile(tc["call"], "<test>", "exec") if ";" in tc["call"] else "", ns) if False else None
                # use exec-then-eval-last for multi-statement calls (mirrors test_runner.py)
                import ast
                tree = ast.parse(tc["call"], mode="exec")
                last = tree.body[-1]
                if len(tree.body) > 1:
                    exec(compile(ast.Module(body=tree.body[:-1], type_ignores=[]), "<test>", "exec"), ns)
                actual = eval(compile(ast.Expression(body=last.value), "<test>", "eval"), ns)
                expected = eval(tc["expected"])
                if actual != expected:
                    failures.append((question["id"], tc["call"], expected, actual))
            except Exception as e:
                failures.append((question["id"], tc["call"], tc["expected"], f"ERROR: {e}"))

    print(f"Checked {total} test cases across {len(bank)} questions.")
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for qid, call, expected, actual in failures:
            print(f"  [{qid}] {call!r} -> expected {expected!r}, got {actual!r}")
        raise SystemExit(1)
    print("All test cases verified correct against canonical solutions.")


if __name__ == "__main__":
    main()
