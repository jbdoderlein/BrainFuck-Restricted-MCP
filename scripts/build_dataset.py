#!/usr/bin/env python3
"""Build data/problems.json and data/tests/{private,semi_private}/<ID>.json
from esolang_benchmark.json.

This script is the single source of truth for the dataset under data/. It:

1. Defines a reference (Python) solution for each of the 80 problems.
2. Verifies every reference solution against the 6 public examples already
   present in esolang_benchmark.json (a correctness check on both the
   reference implementations and the problem specs themselves).
3. Generates additional hidden edge-case tests per problem -- not copies of
   the public examples -- split between semi_private and private suites, with
   expected outputs computed by the verified reference solution.
4. Writes data/problems.json (the public catalog, matching the schema the
   harness in bf_harness/evaluator.py expects) and
   data/tests/{semi_private,private}/<ID>.json.

Re-run this script (`python3 scripts/build_dataset.py`) any time
esolang_benchmark.json or the EXTRA test table below changes, to regenerate
the dataset deterministically.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "esolang_benchmark.json"
OUT_PROBLEMS = ROOT / "data" / "problems.json"
OUT_TESTS_ROOT = ROOT / "data" / "tests"

# ---------------------------------------------------------------------------
# Reference implementations. Each takes the raw stdin text (exactly as the
# harness would provide it -- no implicit trailing newline) and returns the
# expected stdout text.
# ---------------------------------------------------------------------------


def trunc_div(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        q = -q
    return q


def ref_E01(s: str) -> str:
    return "Hello World!"


def ref_E02(s: str) -> str:
    return s


def ref_E03(s: str) -> str:
    return f"Hello, {s}!"


def ref_E04(s: str) -> str:
    a, b = (int(x) for x in s.split())
    return str(a + b)


def ref_E05(s: str) -> str:
    a, b = (int(x) for x in s.split())
    return str(a * b)


def ref_E06(s: str) -> str:
    n = int(s)
    return "even" if n % 2 == 0 else "odd"


def ref_E07(s: str) -> str:
    return str(len(s))


def ref_E08(s: str) -> str:
    return s[::-1]


def ref_E09(s: str) -> str:
    return str(sum(1 for c in s if c in "aeiouAEIOU"))


def ref_E10(s: str) -> str:
    n = int(s)
    return str(n * (n + 1) // 2)


def ref_E11(s: str) -> str:
    n = abs(int(s))
    return str(sum(int(d) for d in str(n)))


def ref_E12(s: str) -> str:
    a, b = (int(x) for x in s.split())
    return str(min(a, b))


def ref_E13(s: str) -> str:
    a, b, c = (int(x) for x in s.split())
    return str(max(a, b, c))


def ref_E14(s: str) -> str:
    n_line, text = s.split("\n", 1)
    n = int(n_line)
    return text * n


def ref_E15(s: str) -> str:
    a, b = s.split("\n", 1)
    return f"{a} {b}"


def ref_E16(s: str) -> str:
    return f"{s[0]} {s[-1]}"


def ref_E17(s: str) -> str:
    return s.upper()


def ref_E18(s: str) -> str:
    return str(s.count(" "))


def ref_E19(s: str) -> str:
    a, b = (int(x) for x in s.split())
    return str(trunc_div(a + b, 2))


def ref_E20(s: str) -> str:
    a, b = (int(x) for x in s.split())
    if a < b:
        return "less"
    if a > b:
        return "greater"
    return "equal"


def ref_M01(s: str) -> str:
    return "yes" if s == s[::-1] else "no"


def ref_M02(s: str) -> str:
    return str(len(s.split()))


def ref_M03(s: str) -> str:
    if not s:
        return ""
    out = []
    prev = s[0]
    count = 1
    for c in s[1:]:
        if c == prev:
            count += 1
        else:
            out.append(f"{prev}{count}")
            prev = c
            count = 1
    out.append(f"{prev}{count}")
    return "".join(out)


def ref_M04(s: str) -> str:
    return "".join(chr((ord(c) - 97 + 3) % 26 + 97) for c in s)


def ref_M05(s: str) -> str:
    a, op, b = s.split()
    a, b = int(a), int(b)
    if op == "+":
        return str(a + b)
    if op == "-":
        return str(a - b)
    return str(a * b)


def ref_M06(s: str) -> str:
    a, b = (int(x) for x in s.split())
    return str(math.gcd(abs(a), abs(b)))


def ref_M07(s: str) -> str:
    return str(math.factorial(int(s)))


def ref_M08(s: str) -> str:
    n = int(s)
    a, b = 1, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return str(a)


def ref_M09(s: str) -> str:
    n = int(s)
    return bin(n)[2:]


def ref_M10(s: str) -> str:
    return str(int(s, 2))


def ref_M11(s: str) -> str:
    text, pattern = s.split("\n", 1)
    if not pattern:
        return "0"
    count = 0
    start = 0
    while True:
        idx = text.find(pattern, start)
        if idx == -1:
            break
        count += 1
        start = idx + 1
    return str(count)


def ref_M12(s: str) -> str:
    return "".join(c for c in s if c not in "aeiouAEIOU")


def ref_M13(s: str) -> str:
    _, nums_line = s.split("\n", 1)
    nums = [int(x) for x in nums_line.split()]
    return " ".join(str(x) for x in sorted(nums))


def ref_M14(s: str) -> str:
    _, nums_line = s.split("\n", 1)
    nums = [int(x) for x in nums_line.split()]
    distinct = sorted(set(nums), reverse=True)
    return str(distinct[1])


def ref_M15(s: str) -> str:
    a, b = s.split("\n", 1)
    return "yes" if Counter(a) == Counter(b) else "no"


def ref_M16(s: str) -> str:
    a, b = s.split("\n", 1)
    return "".join(x + y for x, y in zip(a, b))


def ref_M17(s: str) -> str:
    return s.replace(" ", "_")


def ref_M18(s: str) -> str:
    _, nums_line = s.split("\n", 1)
    nums = [int(x) for x in nums_line.split()]
    return str(sum(nums))


def ref_M19(s: str) -> str:
    return s[0::2]


def ref_M20(s: str) -> str:
    return str(len(set(s)))


def ref_H01(s: str) -> str:
    depth = 0
    for c in s:
        if c == "(":
            depth += 1
        else:
            depth -= 1
            if depth < 0:
                return "no"
    return "yes" if depth == 0 else "no"


def _tokenize_expr(s: str) -> list[str]:
    tokens = []
    num = ""
    for c in s:
        if c.isdigit():
            num += c
        else:
            if num:
                tokens.append(num)
                num = ""
            tokens.append(c)
    if num:
        tokens.append(num)
    return tokens


def ref_H02(s: str) -> str:
    tokens = _tokenize_expr(s)
    # First pass: resolve multiplication.
    stack = [int(tokens[0])]
    i = 1
    while i < len(tokens):
        op = tokens[i]
        val = int(tokens[i + 1])
        if op == "*":
            stack[-1] *= val
        else:
            stack.append(val if op == "+" else -val)
        i += 2
    return str(sum(stack))


def ref_H03(s: str) -> str:
    n = int(s)
    if n < 2:
        return "0"
    sieve = bytearray([1]) * (n + 1)
    sieve[0] = sieve[1] = 0
    for i in range(2, int(n**0.5) + 1):
        if sieve[i]:
            for j in range(i * i, n + 1, i):
                sieve[j] = 0
    return str(sum(sieve))


def ref_H04(s: str) -> str:
    k = int(s)
    count = 0
    candidate = 1
    while count < k:
        candidate += 1
        is_prime = candidate > 1
        i = 2
        while i * i <= candidate:
            if candidate % i == 0:
                is_prime = False
                break
            i += 1
        if is_prime:
            count += 1
    return str(candidate)


def ref_H05(s: str) -> str:
    a, b = s.split("\n", 1)
    return str(int(a) + int(b))


def ref_H06(s: str) -> str:
    words = s.split(" ")
    longest = words[0]
    for w in words[1:]:
        if len(w) > len(longest):
            longest = w
    return longest


def ref_H07(s: str) -> str:
    lines = s.split("\n")
    n = int(lines[0])
    words = lines[1 : 1 + n]
    prefix = words[0]
    for w in words[1:]:
        while not w.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def ref_H08(s: str) -> str:
    counts = [0] * 10
    for c in s:
        counts[int(c)] += 1
    return " ".join(str(x) for x in counts)


def ref_H09(s: str) -> str:
    k_line, text = s.split("\n", 1)
    k = int(k_line) % 26
    return "".join(chr((ord(c) - 97 + k) % 26 + 97) for c in text)


def ref_H10(s: str) -> str:
    if not s:
        return ""
    out = [s[0]]
    for c in s[1:]:
        if c != out[-1]:
            out.append(c)
    return "".join(out)


def ref_H11(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        count = int(s[i + 1])
        out.append(c * count)
        i += 2
    return "".join(out)


def ref_H12(s: str) -> str:
    return str(sum(ord(c) for c in s))


def ref_H13(s: str) -> str:
    lines = s.split("\n")
    n = int(lines[0])
    coeffs = [int(x) for x in lines[1].split()]
    x = int(lines[2])
    assert len(coeffs) == n + 1
    result = 0
    for c in coeffs:
        result = result * x + c
    return str(result)


def ref_H14(s: str) -> str:
    n = int(s)
    divisors = []
    i = 1
    while i * i <= n:
        if n % i == 0:
            divisors.append(i)
            if i != n // i:
                divisors.append(n // i)
        i += 1
    return " ".join(str(x) for x in sorted(divisors))


def ref_H15(s: str) -> str:
    x = 0
    for c in s:
        x += 1 if c == "R" else -1
    return str(x)


def ref_H16(s: str) -> str:
    if not s:
        return "0"
    best = 1
    cur = 1
    for i in range(1, len(s)):
        if s[i] == s[i - 1]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return str(best)


def ref_H17(s: str) -> str:
    _, nums_line = s.split("\n", 1)
    nums = [int(x) for x in nums_line.split()]
    counts = Counter(nums)
    best_val = None
    best_count = -1
    for v in sorted(counts):
        if counts[v] > best_count:
            best_count = counts[v]
            best_val = v
    return str(best_val)


def ref_H18(s: str) -> str:
    total = sum(int(c) for c in s)
    return "yes" if total % 3 == 0 else "no"


def ref_H19(s: str) -> str:
    x = 0
    for c in s:
        if c == "+":
            x += 1
        elif c == "-":
            x -= 1
        else:
            x = 0
    return str(x)


def ref_H20(s: str) -> str:
    lines = s.split("\n")
    n = int(lines[0])
    words = lines[1 : 1 + n]
    return " ".join(sorted(words))


def ref_X01(s: str) -> str:
    n = int(s)
    factors = []
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors.append(d)
            n //= d
        d += 1
    if n > 1:
        factors.append(n)
    return " ".join(str(x) for x in factors)


def ref_X02(s: str) -> str:
    _, nums_line = s.split("\n", 1)
    nums = [int(x) for x in nums_line.split()]
    tails: list[int] = []
    for x in nums:
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(x)
        else:
            tails[lo] = x
    return str(len(tails))


def ref_X03(s: str) -> str:
    lines = s.split("\n")
    m, n, p = (int(x) for x in lines[0].split())
    idx = 1
    a = []
    for _ in range(m):
        a.append([int(x) for x in lines[idx].split()])
        idx += 1
    b = []
    for _ in range(n):
        b.append([int(x) for x in lines[idx].split()])
        idx += 1
    i, j = (int(x) for x in lines[idx].split())
    value = sum(a[i][k] * b[k][j] for k in range(n))
    return str(value)


def ref_X04(s: str) -> str:
    tokens = s.split()
    stack: list[int] = []
    for tok in tokens:
        if tok in "+-*/":
            b = stack.pop()
            a = stack.pop()
            if tok == "+":
                stack.append(a + b)
            elif tok == "-":
                stack.append(a - b)
            elif tok == "*":
                stack.append(a * b)
            else:
                stack.append(trunc_div(a, b))
        else:
            stack.append(int(tok))
    return str(stack[-1])


def ref_X05(s: str) -> str:
    a_line, b_line = s.split("\n", 1)
    a = [int(x) for x in a_line.split()] if a_line.strip() != "" else []
    b = [int(x) for x in b_line.split()] if b_line.strip() != "" else []
    merged = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            merged.append(a[i])
            i += 1
        else:
            merged.append(b[j])
            j += 1
    merged.extend(a[i:])
    merged.extend(b[j:])
    return " ".join(str(x) for x in merged)


def ref_X06(s: str) -> str:
    b, e, m = (int(x) for x in s.split())
    return str(pow(b, e, m))


def ref_X07(s: str) -> str:
    if not s:
        return "0"
    best = 1
    n = len(s)

    def expand(l: int, r: int) -> int:
        while l >= 0 and r < n and s[l] == s[r]:
            l -= 1
            r += 1
        return r - l - 1

    for i in range(n):
        best = max(best, expand(i, i), expand(i, i + 1))
    return str(best)


def ref_X08(s: str) -> str:
    l, r = (int(x) for x in s.split())

    def popcount_upto(n: int) -> int:
        # total set bits in binary representations of 0..n inclusive
        total = 0
        for i in range(n + 1):
            total += bin(i).count("1")
        return total

    return str(popcount_upto(r) - popcount_upto(l - 1) if l > 0 else popcount_upto(r))


def ref_X09(s: str) -> str:
    depth = 0
    best = 0
    for c in s:
        if c == "(":
            depth += 1
            best = max(best, depth)
        else:
            depth = max(0, depth - 1)
    return str(best)


def ref_X10(s: str) -> str:
    a, b = s.split("\n", 1)
    if len(a) != len(b):
        return "no"
    return "yes" if b in (a + a) else "no"


def ref_X11(s: str) -> str:
    _, nums_line = s.split("\n", 1)
    nums = [int(x) for x in nums_line.split()]

    def sort_count(arr: list[int]) -> tuple[list[int], int]:
        if len(arr) <= 1:
            return arr, 0
        mid = len(arr) // 2
        left, inv_l = sort_count(arr[:mid])
        right, inv_r = sort_count(arr[mid:])
        merged = []
        i = j = 0
        inversions = inv_l + inv_r
        while i < len(left) and j < len(right):
            if left[i] <= right[j]:
                merged.append(left[i])
                i += 1
            else:
                merged.append(right[j])
                j += 1
                inversions += len(left) - i
        merged.extend(left[i:])
        merged.extend(right[j:])
        return merged, inversions

    _, inv = sort_count(nums)
    return str(inv)


def ref_X12(s: str) -> str:
    a, b = (int(x) for x in s.split())
    return str(abs(a * b) // math.gcd(a, b))


def ref_X13(s: str) -> str:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for c in s:
        if c in "([{":
            stack.append(c)
        else:
            if not stack or stack.pop() != pairs[c]:
                return "no"
    return "yes" if not stack else "no"


def ref_X14(s: str) -> str:
    _, nums_line = s.split("\n", 1)
    nums = [int(x) for x in nums_line.split()]
    result = [-1] * len(nums)
    stack: list[int] = []
    for i, v in enumerate(nums):
        while stack and nums[stack[-1]] < v:
            result[stack.pop()] = v
        stack.append(i)
    return " ".join(str(x) for x in result)


def ref_X15(s: str) -> str:
    lines = s.split("\n")
    r, c = (int(x) for x in lines[0].split())
    grid = [[int(x) for x in lines[1 + i].split()] for i in range(r)]
    out = []
    top, bottom, left, right = 0, r - 1, 0, c - 1
    while top <= bottom and left <= right:
        for x in range(left, right + 1):
            out.append(grid[top][x])
        top += 1
        for y in range(top, bottom + 1):
            out.append(grid[y][right])
        right -= 1
        if top <= bottom:
            for x in range(right, left - 1, -1):
                out.append(grid[bottom][x])
            bottom -= 1
        if left <= right:
            for y in range(bottom, top - 1, -1):
                out.append(grid[y][left])
            left += 1
    return " ".join(str(x) for x in out)


def ref_X16(s: str) -> str:
    a, b = (int(x) for x in s.split())
    return str(bin(a ^ b).count("1"))


def ref_X17(s: str) -> str:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, c in enumerate(s):
        v = values[c]
        if i + 1 < len(s) and values[s[i + 1]] > v:
            total -= v
        else:
            total += v
    return str(total)


def ref_X18(s: str) -> str:
    n = int(s)
    table = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out = []
    for value, symbol in table:
        while n >= value:
            out.append(symbol)
            n -= value
    return "".join(out)


def ref_X19(s: str) -> str:
    lines = s.split("\n")
    n = int(lines[0])
    nums = [int(x) for x in lines[1].split()]
    return "yes" if sorted(nums) == list(range(1, n + 1)) else "no"


def ref_X20(s: str) -> str:
    n, k = (int(x) for x in s.split())
    people = list(range(1, n + 1))
    idx = 0
    while len(people) > 1:
        idx = (idx + k - 1) % len(people)
        del people[idx]
    return str(people[0])


REFERENCES = {
    name[4:]: obj
    for name, obj in list(globals().items())
    if name.startswith("ref_") and callable(obj)
}


def verify_reference_solutions(problems: list[dict]) -> None:
    """Run every reference solution against the public examples; abort on any mismatch."""
    missing = [p["id"] for p in problems if p["id"] not in REFERENCES]
    if missing:
        print("Missing reference implementations for:", missing, file=sys.stderr)
        sys.exit(1)

    failures = []
    for p in problems:
        pid = p["id"]
        fn = REFERENCES[pid]
        for ex in p["input_output_examples"]:
            try:
                got = fn(ex["input"])
            except Exception as exc:  # noqa: BLE001
                failures.append((pid, ex["input"], ex["output"], f"EXC:{exc!r}"))
                continue
            if got != ex["output"]:
                failures.append((pid, ex["input"], ex["output"], got))

    if failures:
        print(f"{len(failures)} example mismatches:", file=sys.stderr)
        for pid, inp, expected, got in failures:
            print(f"  {pid}: input={inp!r} expected={expected!r} got={got!r}", file=sys.stderr)
        sys.exit(1)

    total = sum(len(p["input_output_examples"]) for p in problems)
    print(f"All {total} given examples verified OK across {len(problems)} problems.")


# ---------------------------------------------------------------------------
# Dataset assembly: runtime limits, extra hidden tests, and file output.
# ---------------------------------------------------------------------------

RUNTIME_BY_DIFFICULTY = {
    "easy": {
        "cell_bits": 8,
        "cell_overflow": "wrap",
        "negative_tape": "error",
        "eof_value": 0,
        "encoding": "utf-8",
        "input_has_implicit_newline": False,
        "timeout_seconds": 2.0,
        "memory_bytes": 536870912,
        "max_output_bytes": 65536,
        "max_source_bytes": 1048576,
        "max_input_bytes": 1048576,
        "max_artifact_files": 128,
        "max_text_file_bytes": 1048576,
        "max_artifact_bytes": 4194304,
    },
    "medium": {
        "cell_bits": 8,
        "cell_overflow": "wrap",
        "negative_tape": "error",
        "eof_value": 0,
        "encoding": "utf-8",
        "input_has_implicit_newline": False,
        "timeout_seconds": 5.0,
        "memory_bytes": 536870912,
        "max_output_bytes": 65536,
        "max_source_bytes": 1048576,
        "max_input_bytes": 1048576,
        "max_artifact_files": 128,
        "max_text_file_bytes": 1048576,
        "max_artifact_bytes": 4194304,
    },
    "hard": {
        "cell_bits": 8,
        "cell_overflow": "wrap",
        "negative_tape": "error",
        "eof_value": 0,
        "encoding": "utf-8",
        "input_has_implicit_newline": False,
        "timeout_seconds": 8.0,
        "memory_bytes": 536870912,
        "max_output_bytes": 131072,
        "max_source_bytes": 2097152,
        "max_input_bytes": 1048576,
        "max_artifact_files": 128,
        "max_text_file_bytes": 1048576,
        "max_artifact_bytes": 4194304,
    },
    "extra_hard": {
        "cell_bits": 8,
        "cell_overflow": "wrap",
        "negative_tape": "error",
        "eof_value": 0,
        "encoding": "utf-8",
        "input_has_implicit_newline": False,
        "timeout_seconds": 12.0,
        "memory_bytes": 536870912,
        "max_output_bytes": 131072,
        "max_source_bytes": 2097152,
        "max_input_bytes": 1048576,
        "max_artifact_files": 128,
        "max_text_file_bytes": 1048576,
        "max_artifact_bytes": 4194304,
    },
}

# Extra hidden test inputs per problem, split into semi_private (visible to
# the model via evaluate_solution) and private (only checked on submit).
# Every input here is deliberately NOT one of the 6 public examples, and
# every input respects the domain stated in that problem's description
# (e.g. H03 requires N >= 2, X04 requires single-digit operands).
EXTRA: dict[str, dict[str, list[str]]] = {
    "E01": {
        "semi_private": [""],
        "private": [""],
    },
    "E02": {
        "semi_private": ["", " leading", "trailing "],
        "private": ["private-test-01", "tabs\tstay", "  double  space  "],
    },
    "E03": {
        "semi_private": ["", "van Gogh", "O'Brien"],
        "private": ["Zoe", "multi word name", "42"],
    },
    "E04": {
        "semi_private": ["0 -1", "-1 -1", "123456 654321"],
        "private": ["7 -7", "-1000000 1000000", "1 1"],
    },
    "E05": {
        "semi_private": ["-1 -1", "1000 1000", "0 -5"],
        "private": ["-6 7", "13 13", "1 -1"],
    },
    "E06": {
        "semi_private": ["-1", "2", "-2"],
        "private": ["999999", "-1000000", "3"],
    },
    "E07": {
        "semi_private": ["ab cd ef", "  ", "."],
        "private": ["a" * 40, "tab\there", "1234567890"],
    },
    "E08": {
        "semi_private": ["", "  ab  ", "!?"],
        "private": ["Palindrome", "0123456789", "z"],
    },
    "E09": {
        "semi_private": ["a e i o u", "bcdfg", "Queueing"],
        "private": ["", "AaEeIiOoUu", "sky"],
    },
    "E10": {
        "semi_private": ["2", "50", "7"],
        "private": ["1000", "4", "20"],
    },
    "E11": {
        "semi_private": ["-1", "1000000", "-1000000"],
        "private": ["7", "-99999", "10203"],
    },
    "E12": {
        "semi_private": ["-1 1", "0 -1", "42 42"],
        "private": ["-100 -50", "1 0", "-7 -7"],
    },
    "E13": {
        "semi_private": ["-1 -2 -3", "0 0 0", "5 -5 0"],
        "private": ["7 7 6", "-100 0 100", "3 1 2"],
    },
    "E14": {
        "semi_private": ["1\n", "2\n ", "3\nz"],
        "private": ["0\nxyz", "6\nq", "2\nab cd"],
    },
    "E15": {
        "semi_private": ["\nb", "a\n", "\n"],
        "private": ["  spaced  \nline", "x\ny", "line1\ntrailing space line2 "],
    },
    "E16": {
        "semi_private": ["aa", "!Z", "1"],
        "private": ["mid dle", "  x  ", "AZ"],
    },
    "E17": {
        "semi_private": ["", "  spaced out  ", "café"],
        "private": ["zZzZ", "123", "PascalCase"],
    },
    "E18": {
        "semi_private": ["", "no spaces here at all", " "],
        "private": ["a  b   c", "     ", "tab\tnotspace"],
    },
    "E19": {
        "semi_private": ["-3 -4", "-1 1", "0 -1"],
        "private": ["-5 4", "7 -3", "-7 -8"],
    },
    "E20": {
        "semi_private": ["-5 -3", "0 0", "1 -1"],
        "private": ["-1000000 1000000", "5 5", "-2 -1"],
    },
    "M01": {
        "semi_private": ["", "aa", "abcba"],
        "private": ["noon", "abccba", "abcdcba x"],
    },
    "M02": {
        "semi_private": ["x", "a b c d e f g", "brainfuck is an esoteric language"],
        "private": ["one", "  ", "a b"],
    },
    "M03": {
        "semi_private": ["aabbaa", "abababab", "wwwwwwww"],
        "private": ["p", "qqrrrsss", "aabaa"],
    },
    "M04": {
        "semi_private": ["wxy", "bcdefghijklmnopqrstuvwxyz", "qrs"],
        "private": ["z", "abcxyz", "nop"],
    },
    "M05": {
        "semi_private": ["0 - 0", "1 * 1", "-5 + 5"],
        "private": ["20 - 30", "7 * 7", "1000 + 1"],
    },
    "M06": {
        "semi_private": ["1 1", "1000000 1", "13 26"],
        "private": ["270 192", "9 3", "1 1000000"],
    },
    "M07": {
        "semi_private": ["2", "4", "6"],
        "private": ["8", "9", "2"],
    },
    "M08": {
        "semi_private": ["3", "20", "4"],
        "private": ["6", "12", "9"],
    },
    "M09": {
        "semi_private": ["2", "16", "1023"],
        "private": ["7", "64", "3"],
    },
    "M10": {
        "semi_private": ["10", "100", "110011"],
        "private": ["01111", "10101010", "1000000"],
    },
    "M11": {
        "semi_private": ["aaaa\na", "banana\nana", "abc\nabcd"],
        "private": ["mississippi\nss", "zzzz\nzz", "abcabcabc\nabca"],
    },
    "M12": {
        "semi_private": ["", "aeiouAEIOU", "sky"],
        "private": ["The Quick Brown Fox", "bcdfg", "a"],
    },
    "M13": {
        "semi_private": ["2\n-1 -2", "5\n0 0 0 0 0", "4\n-3 5 -1 2"],
        "private": ["1\n-7", "6\n6 5 4 3 2 1", "3\n2 2 1"],
    },
    "M14": {
        "semi_private": ["3\n1 1 2", "4\n-1 -1 -2 -2", "5\n7 7 7 7 8"],
        "private": ["2\n5 -5", "4\n0 1 2 3", "3\n9 8 9"],
    },
    "M15": {
        "semi_private": ["aabbcc\nabcabc", "z\ny", "aabb\nabab"],
        "private": ["abcd\ndcba", "aaab\naaba", "xy\nyx"],
    },
    "M16": {
        "semi_private": ["ab\nba", "abcde\nvwxyz", "mn\nop"],
        "private": ["p\nq", "aaaa\nbbbb", "hi\nyo"],
    },
    "M17": {
        "semi_private": ["  a  b  ", "no_underscore_needed", " "],
        "private": ["end with space ", " start with space", "a b c d e"],
    },
    "M18": {
        "semi_private": ["3\n-1 -2 -3", "1\n0", "5\n1000 1000 1000 1000 1000"],
        "private": ["2\n-5 5", "4\n-10 20 -30 40", "6\n1 1 1 1 1 1"],
    },
    "M19": {
        "semi_private": ["", "abcdefgh", "x"],
        "private": ["1234567890abcdef", "ab", "abcdefghi"],
    },
    "M20": {
        "semi_private": ["", "zzzzzzzzzz", "abcabcabc"],
        "private": ["aAbBcC", "The quick brown fox", "!!!???"],
    },
    "H01": {
        "semi_private": ["()", "(((", ")))"],
        "private": ["()(())()", "(()", "()) (", "((())())"],
    },
    "H02": {
        "semi_private": ["2*3*4*5", "9-9+9", "1*2+3*4"],
        "private": ["10+20*3-5", "100*2+1", "7-2-2", "2*2*2*2*2"],
    },
    "H03": {
        "semi_private": ["3", "7", "5"],
        "private": ["1000", "997", "6"],
    },
    "H04": {
        "semi_private": ["2", "20", "50"],
        "private": ["4", "6", "100"],
    },
    "H05": {
        "semi_private": ["1\n999999999999999999999999", "0\n123", "999\n999"],
        "private": ["1000000000000000000\n1", "5\n5", "111222333\n888777666"],
    },
    "H06": {
        "semi_private": ["equal length words", "single", "a bb ccc dddd"],
        "private": ["x yy zzz", "abcdefg abcdef abcdefg", "tie tie tied"],
    },
    "H07": {
        "semi_private": ["2\nabc\nabd", "3\nsame\nsame\nsame", "2\na\nb"],
        "private": ["4\nprefixaaa\nprefixbbb\nprefixccc\nprefixddd", "2\nab\nab", "3\nxy\nxyz\nx"],
    },
    "H08": {
        "semi_private": ["5555555555", "0", "13579"],
        "private": ["10000000009", "2468024680", "1"],
    },
    "H09": {
        "semi_private": ["25\nabc", "-25\nabc", "27\nz"],
        "private": ["-13\nhello", "5\nzzzzz", "-2\nbcd"],
    },
    "H10": {
        "semi_private": ["", "aabbaabb", "abcabc"],
        "private": ["aaaaaaaa", "aabbbcccc", "abababab"],
    },
    "H11": {
        "semi_private": ["a1", "z9y9", "b5c1d9"],
        "private": ["a4b4c4d4", "x2", "m1n2o3p4"],
    },
    "H12": {
        "semi_private": ["", " ", "z"],
        "private": ["ZZ", "brainfuck", "09"],
    },
    "H13": {
        "semi_private": ["1\n0 0\n5", "2\n3 0 0\n0", "0\n-4\n1"],
        "private": ["4\n1 0 0 0 0\n2", "2\n-1 2 -3\n3", "1\n5 5\n0"],
    },
    "H14": {
        "semi_private": ["2", "17", "60"],
        "private": ["999", "13", "49"],
    },
    "H15": {
        "semi_private": ["", "R", "L"],
        "private": ["RLRLRLRLRL", "LLLLRRRR", "RRRLLLRRR"],
    },
    "H16": {
        "semi_private": ["a", "aabbaabb", "abcabcabc"],
        "private": ["zzzzzzzzzzz", "aabbbbaaaa", "abababbba"],
    },
    "H17": {
        "semi_private": ["4\n1 1 2 2", "3\n-5 -5 3", "2\n0 0"],
        "private": ["5\n3 1 3 1 3", "6\n2 3 2 3 1 1", "1\n-1"],
    },
    "H18": {
        "semi_private": ["3", "10", "300"],
        "private": ["111", "222222222", "5"],
    },
    "H19": {
        "semi_private": ["", "0", "+0-"],
        "private": ["+++---", "0+0-0", "-0+0-0+"],
    },
    "H20": {
        "semi_private": ["2\nb\na", "3\nsame\nsame\nsame", "1\nonly"],
        "private": ["4\nd\nc\nb\na", "3\nApple\napple\nBanana", "2\nabc\nab"],
    },
    "X01": {
        "semi_private": ["3", "997", "1024"],
        "private": ["999983", "128", "999999"],
    },
    "X02": {
        "semi_private": ["4\n2 2 2 2", "1\n5", "6\n1 3 6 7 9 4"],
        "private": ["5\n5 1 5 1 5", "9\n0 1 0 3 2 3 4 3 5", "3\n3 3 3"],
    },
    "X03": {
        "semi_private": [
            "2 2 1\n1 2\n3 4\n5\n6\n1 0",
            "1 2 2\n2 3\n1 0\n0 1\n0 1",
            "3 1 1\n1\n2\n3\n4\n2 0",
        ],
        "private": [
            "2 2 2\n0 0\n0 0\n1 2\n3 4\n1 0",
            "1 1 1\n0\n0\n0 0",
            "2 3 1\n1 2 3\n4 5 6\n1\n1\n1\n1 0",
        ],
    },
    "X04": {
        "semi_private": ["4 2 -", "8 4 /", "6 2 3 * -"],
        "private": ["7 8 +", "4 9 /", "3 3 3 * *"],
    },
    "X05": {
        "semi_private": ["\n1 2 3", "1 2 3\n", "0\n0"],
        "private": ["-10 -5\n-8 -6 0", "1 1 1\n1 1", "100\n1 2 3 200"],
    },
    "X06": {
        "semi_private": ["5 0 7", "0 5 7", "2 20 1000000007"],
        "private": ["3 100 1", "6 4 100", "9 1 2"],
    },
    "X07": {
        "semi_private": ["", "aa", "forgeeksskeegfor"],
        "private": ["z", "aaaaaa", "abacdfgdcaba"],
    },
    "X08": {
        "semi_private": ["0 0", "2 2", "16 16"],
        "private": ["0 1000", "100 100", "31 31"],
    },
    "X09": {
        "semi_private": ["", "(", ")"],
        "private": ["(((())))", "()(()", ")()(())("],
    },
    "X10": {
        "semi_private": ["a\na", "ab\nba", "aaaa\naaaa"],
        "private": ["abcdef\ndefabc", "xyz\nzyx", "aab\naba"],
    },
    "X11": {
        "semi_private": ["4\n1 1 1 1", "1\n5", "4\n1 2 3 4"],
        "private": ["7\n7 6 5 4 3 2 1", "5\n2 2 2 1 1", "6\n1 5 2 4 3 6"],
    },
    "X12": {
        "semi_private": ["1 1", "9 6", "1000000 1"],
        "private": ["17 13", "8 12", "100 25"],
    },
    "X13": {
        "semi_private": ["", "(((((((())))))))", "[({})]"],
        "private": ["([)", "{[}]", "((([[[{{{}}}]]])))"],
    },
    "X14": {
        "semi_private": ["1\n5", "4\n1 1 1 1", "5\n2 2 2 2 3"],
        "private": ["6\n6 5 4 3 2 1", "3\n1 3 3", "4\n10 5 6 7"],
    },
    "X15": {
        "semi_private": ["1 1\n5", "5 1\n1\n2\n3\n4\n5", "1 5\n1 2 3 4 5"],
        "private": [
            "3 4\n1 2 3 4\n5 6 7 8\n9 10 11 12",
            "4 3\n1 2 3\n4 5 6\n7 8 9\n10 11 12",
            "2 4\n1 2 3 4\n5 6 7 8",
        ],
    },
    "X16": {
        "semi_private": ["0 1", "5 5", "1024 0"],
        "private": ["255 255", "1 1000000", "17 20"],
    },
    "X17": {
        "semi_private": ["XL", "XC", "CDXLIV"],
        "private": ["MMMCMXCIX", "DCCCLXXXVIII", "XIV"],
    },
    "X18": {
        "semi_private": ["40", "90", "444"],
        "private": ["3999", "1", "888"],
    },
    "X19": {
        "semi_private": ["2\n2 1", "3\n1 1 3", "5\n5 4 3 2 0"],
        "private": ["6\n6 5 4 3 2 1", "4\n0 1 2 3", "1\n2"],
    },
    "X20": {
        "semi_private": ["2 1", "3 2", "8 3"],
        "private": ["9 4", "13 2", "20 5"],
    },
}


def build() -> None:
    src = json.loads(SRC.read_text(encoding="utf-8"))
    problems_src = src["problems"]

    verify_reference_solutions(problems_src)

    known_ids = {p["id"] for p in problems_src}
    assert set(EXTRA) == known_ids, sorted(set(EXTRA) ^ known_ids)

    public_problems = []
    tests_written = {"semi_private": 0, "private": 0}

    for p in problems_src:
        pid = p["id"]
        fn = REFERENCES[pid]

        # Deduplicate the public examples while preserving order (the source
        # data repeats identical examples for problems with no real input
        # space, e.g. E01).
        seen = set()
        public_examples = []
        for ex in p["input_output_examples"]:
            key = (ex["input"], ex["output"])
            if key in seen:
                continue
            seen.add(key)
            public_examples.append({"input": ex["input"], "output": ex["output"]})

        difficulty = p["difficulty"]
        public_problems.append(
            {
                "id": pid,
                "difficulty": difficulty,
                "title": p["title"],
                "description": p["description"],
                "runtime": RUNTIME_BY_DIFFICULTY[difficulty],
                "input_output_examples": public_examples,
            }
        )

        for suite in ("semi_private", "private"):
            inputs = EXTRA[pid][suite]
            tests = []
            for raw_input in inputs:
                expected = fn(raw_input)
                tests.append({"input": raw_input, "output": expected})
            suite_dir = OUT_TESTS_ROOT / suite
            suite_dir.mkdir(parents=True, exist_ok=True)
            out_path = suite_dir / f"{pid}.json"
            out_path.write_text(
                json.dumps({"problem_id": pid, "tests": tests}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(out_path, 0o644)
            tests_written[suite] += len(tests)

    catalog = {
        "metadata": src["metadata"],
        "problems": public_problems,
    }
    OUT_PROBLEMS.parent.mkdir(parents=True, exist_ok=True)
    OUT_PROBLEMS.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(OUT_PROBLEMS, 0o644)

    print(f"Wrote {OUT_PROBLEMS} with {len(public_problems)} problems.")
    print(
        f"Wrote {tests_written['semi_private']} semi_private tests, "
        f"{tests_written['private']} private tests under {OUT_TESTS_ROOT}."
    )


if __name__ == "__main__":
    build()
