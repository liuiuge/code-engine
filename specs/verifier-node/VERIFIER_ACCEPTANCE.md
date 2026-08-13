# Verifier Acceptance Tests

Test-first spec for the `code_verifier_node` feature. The goal of the feature is to
stop the pipeline from declaring success on code that **compiles but does not actually
solve the problem**. Everything below must pass before the implementation is considered done.

Acceptance tests are written as Given/When/Then against observable behavior of the
pipeline (`run_pipeline` / `app.invoke`) and the public surface of `features/solver/verifier.py`.
They are later convertible 1:1 into `pytest` cases (see §7).

---

## 1. State / output contract (pinned here, implemented later)

The verifier introduces these state keys and sentinels. Tests assert against them.

| Symbol | Value | Meaning |
|--------|-------|---------|
| `StateKey.VERIFY_RESULT` | `"verify_result"` | Node output: pass / fail / skip message |
| `StateKey.VERIFY_DETAILS` | `"verify_details"` | Per-case results (list of dicts) |
| `VERIFY_PASS_MESSAGE` | `"verification passed"` | All asserted cases matched |
| `VERIFY_FAIL_PREFIX` | `"verified_fail: "` | Prefix of any failure message |
| `VERIFY_SKIP_MESSAGE` | `"verification skipped"` | No test data / mode off → no blocking |

`verify_result` values are exactly one of:
- `VERIFY_PASS_MESSAGE` → pipeline takes the **pass** route to `END` (`verified=true`)
- starts with `VERIFY_FAIL_PREFIX` → pipeline routes to `code_fixer` (if `retry_count < 3`) or
  ends flagged `verified=false` (max retries)
- `VERIFY_SKIP_MESSAGE` → pipeline proceeds to `END` without blocking (compile-only behavior)

---

## 2. Fixtures

Place under `features/solver/tests/fixtures/`.

### 2.1 Problem records (from live `output/problems/*.json`)
- `two-sum.json` — `exampleTestcaseList: ['[2,7,11,15]\n9', '[3,2,4]\n6', '[3,3]\n6']`,
  `metaData.name="twoSum"`, params `nums integer[]`, `target integer`, return `integer[]`.
  Expected outputs (canonical LeetCode): `[0,1]`, `[1,2]`, `[0,1]`.
  NOTE: two-sum has multiple valid index answers; the verifier does **exact match**
  against these canonical outputs, so a correct-but-differently-ordered answer is a
  known false-negative risk (see §8).
- `climbing-stairs.json` — `exampleTestcaseList: ['2','3']`, return `integer`.
  `description_md` contains `**Output:** 2` and `**Output:** 3` (used for parser tests).
- `add-two-numbers.json` — `ListNode` params, used for linked-list harness test.

### 2.2 Go code samples (one file per behavior)
- `two-sum-correct.go` — returns correct indices for all examples.
- `two-sum-wrong.go` — compiles, but always returns `[0,0]` (the core bug we must catch).
- `two-sum-panic.go` — compiles, panics with index-out-of-range on example 1.
- `two-sum-loop.go` — compiles, infinite `for {}` loop on example 1.

### 2.3 Freeform input (no record)
- `freeform_question` = a plain coding question string with **no** LeetCode record
  (no `exampleTestcaseList`, no `description_md`).

---

## 3. Acceptance tests

### AT-01 — Correct solution is verified
- **Given** `two-sum.json` and `two-sum-correct.go`.
- **When** the pipeline runs to completion.
- **Then** `code_executor` compiles OK → `code_verifier` runs and all cases match.
- **And** `state[VERIFY_RESULT] == VERIFY_PASS_MESSAGE`.
- **And** pipeline ends via the **pass** route; `verified=true`.

### AT-02 — Compiles-but-wrong is NOT declared success  ← the bug fix
- **Given** `two-sum.json` and `two-sum-wrong.go` (compiles, returns `[0,0]`).
- **When** the pipeline runs.
- **Then** `code_verifier` detects a mismatch on ≥1 case.
- **And** `state[VERIFY_RESULT]` starts with `VERIFY_FAIL_PREFIX` and includes the
  per-case diff (expected `[0,1]` vs got `[0,0]`).
- **And** pipeline routes to `code_fixer` (retry<3); if unfixable, ends `verified=false`.
- **And** the result is **never** reported as a successful solve (regression guard for the
  original compile-only behavior).

### AT-03 — Runtime panic is caught
- **Given** `two-sum.json` and `two-sum-panic.go`.
- **When** `run_go_verify` executes the harness.
- **Then** the non-zero exit / captured `stderr` containing `panic:` is detected.
- **And** `state[VERIFY_RESULT] == VERIFY_FAIL_PREFIX + "runtime panic during execution"`.
- **And** pipeline routes to `code_fixer`.

### AT-04 — Infinite loop / timeout is caught
- **Given** `two-sum.json` and `two-sum-loop.go`.
- **When** `run_go_verify` executes with a configured timeout (default 30s,
  `VERIFY_TIMEOUT_DEFAULT`).
- **Then** the process is killed when the timeout elapses.
- **And** `state[VERIFY_RESULT] == VERIFY_FAIL_PREFIX + "timeout (exceeded 30s)"`.
- **And** pipeline routes to `code_fixer`.

### AT-05 — Expected-output parser reads `description_md`
- **Given** `climbing-stairs.json` (`**Output:** 2`, `**Output:** 3`).
- **When** `parse_test_cases(record)` runs.
- **Then** expected outputs parse to `[2, 3]`, aligned by example order to the two
  `exampleTestcaseList` inputs.
- **And** no exception is raised for the `**Output:**` bold prefix format.

### AT-06 — Typed harness for scalar + array (two-sum)
- **Given** `two-sum.json` `metaData` + `exampleTestcaseList`.
- **When** `verifier` writes the harness into `<task_dir>/verify/`:
  `solution.go` (copy of the generated code), `verify_helpers.go` (typed input
  parsers + JSON serializers), `verify_test.go` (`TestVerifier` calling the
  solution against each case), and `go.mod`.
- **Then** the harness parses line 1 as `[]int`, line 2 as `int`, calls
  `twoSum(nums, target)`, serializes the result as JSON.
- **And** `go test` of the harness yields JSON the comparator can match to expected.

### AT-07 — Typed harness for ListNode (add-two-numbers)
- **Given** `add-two-numbers.json` (`ListNode` params) + `exampleTestcaseList`
  (`'[2,4,3]\n[5,6,4]'`, ...).
- **When** the harness is built and run.
- **Then** inputs are parsed into linked lists, `addTwoNumbers` is called, and the
  result list is compared to expected (list equality, not pointer equality).

### AT-08 — No test data degrades gracefully (no-op)
- **Given** `freeform_question` with **no** record.
- **When** the pipeline reaches `code_verifier_node`.
- **Then** `state[VERIFY_RESULT] == VERIFY_SKIP_MESSAGE`.
- **And** pipeline proceeds to `END` without blocking; behavior identical to today
  (compile-only). No `go run` is attempted.

### AT-09 — `verify_mode=off` skips verification
- **Given** `verify_mode=off`.
- **When** the pipeline runs.
- **Then** `code_verifier` short-circuits to `VERIFY_SKIP_MESSAGE`; no `go run` is performed.

### AT-10 — `verify_mode=smoke` catches crashes without expected outputs
- **Given** `verify_mode=smoke` and expected-output parsing unavailable.
- **When** the verifier runs examples.
- **Then** it executes each example and checks only for panic/timeout.
- **And** `verify_result` = pass when no crash, fail on crash; it **never** fails due to
  a missing expected output.

### AT-11 — Retry budget is shared and capped
- **Given** code that compiles but fails verification.
- **When** `code_fixer` retries.
- **Then** compile + verify failures share one counter; after 3 total retries the
  pipeline ends with `verified=false` and does **not** loop forever.

### AT-12 — Verification is surfaced through the API
- **Given** a verified solution exists.
- **When** `service.run_pipeline` returns and `GET /api/go-code` (+ `/api/go-code/<task>`) is called.
- **Then** the result dict and the API responses include `verify_result` and
  `verify_details`, and the list/detail shows a `verified` badge.

### AT-13 — Fixer is steered by the verification failure
- **Given** verification failed.
- **When** `code_fixer_node` runs.
- **Then** its prompt contains the `{verify_result}` mismatch detail **in addition to**
  `{build_result}`, so the fixer targets correctness, not just compilation.

---

## 4. Comparator rules (pinned for AT-02/06/07)
- Arrays / slices: compared as JSON (order-sensitive).
- Numeric: equal within float tolerance when the declared type is `double`.
- Linked lists / trees: structural equality (value traversal), not pointer equality.
- Fallback: normalized string compare (trim + collapse whitespace) when JSON parse fails.
- Unknown `metaData` param/return type → that case is **skipped** (counted as
  "not asserted") and noted in `verify_details`; it must not fail the whole run.

---

## 5. `verify_mode` matrix

| mode   | compile check | run examples | compare expected | no-record behavior |
|--------|---------------|--------------|------------------|--------------------|
| `off`  | yes           | no           | no               | skip               |
| `smoke`| yes           | yes          | no               | skip               |
| `assert`| yes          | yes          | yes              | skip               |

Default target after Phase B: `assert`. Phase A ships `smoke`.

---

## 6. Pass criteria
All of AT-01 … AT-13 pass. In particular, **AT-02 must flip the original behavior**:
a compiling-but-wrong solution is no longer reported as success.

---

## 7. Mapping to implementation units (for the later `pytest` conversion)

| Test | Unit under test |
|------|-----------------|
| AT-05 | `verifier.parse_test_cases` |
| AT-06, AT-07 | `verifier._emit_test` + `verifier._emit_helpers` (harness generation) |
| AT-03, AT-04 | `verifier.run_go_verify` (timeout + capture) |
| AT-01, AT-02 | `verifier.verify_go_code` + `code_verifier_node` |
| AT-08 … AT-11 | `code_verifier_node` routing + `verify_mode` |
| AT-12 | `service.run_pipeline` + `web/routes/problems.py` + `schemas.py` |
| AT-13 | `nodes.code_fixer_node` + `prompts/code_fixer.md` |

---

## 8. Known limitations

- **Exact-match fragility for multi-answer problems.** Verification compares the
  solution output to the canonical expected output parsed from `description_md`
  (whitespace-normalized). Problems whose correct answer is not unique — most
  notably `two-sum` (any valid index pair `nums[a]+nums[b]==target` is acceptable)
  — can produce a **false negative**: a correct solution that returns a
  differently-ordered-but-valid answer is reported as `verified_fail`. The canonical
  LeetCode outputs are deterministic, so the bundled fixtures use them; this caveat
  applies mainly to user-supplied problems with ambiguous expected outputs.
- **Expected-output parsing depends on `description_md`.** If a record lacks
  `**Output:**` lines (older cached records), `assert` mode degrades to `VERIFY_SKIP_MESSAGE`
  rather than falsely passing (see `verify_go_code` `assertable` guard).
- **Harness scope.** The generated `verify/` dir and `verify_result.json` sidecar
  are written under the task directory; the sidecar lets the API surface a `verified`
  badge without re-running `go test`.

### Regression test

The acceptance tests are exercised by
`features/solver/tests/test_verifier_regression.py` (runs the real pipeline with the
LLM stubbed). Run with:

```bash
PYTHONPATH=. python features/solver/tests/test_verifier_regression.py
```
