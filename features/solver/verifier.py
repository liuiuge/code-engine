"""Go code verifier: run generated code against example test cases and assert correctness.

The executor only proves the code *compiles*. This module proves it *solves the
problem* by actually running it against the problem's example inputs and comparing
the output to the expected answer.

Design
------
* Test cases come from the problem ``record``:
  - inputs  -> ``record["exampleTestcaseList"]`` (newline-separated fields)
  - expected -> parsed from ``record["description_md"]`` "**Output:**" lines
* A typed harness (``verify_test.go`` + ``verify_helpers.go`` + a copy of the
  solution) is generated into ``<task_dir>/verify`` and executed with
  ``go test -timeout``. The timeout catches infinite loops; a non-zero exit
  (panic / assertion mismatch) is captured and reported.
* ``verify_mode``:
  - ``off``   -> no verification (compile-only, current behavior).
  - ``smoke`` -> run the examples, only catch panics/timeouts (no expected needed).
  - ``assert``-> full correctness assertion against expected outputs.
* When there is no record / no cases / an unsupported type, the case is *skipped*
  (never fails the whole run) and noted in ``verify_details``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from infrastructure.constants import (
    VERIFY_FAIL_PREFIX,
    VERIFY_PASS_MESSAGE,
    VERIFY_SKIP_MESSAGE,
    VERIFY_TIMEOUT_DEFAULT,
)

# --- supported metaData types -------------------------------------------------
_SCALAR_PARSERS = {
    "integer": "cevParseInt",
    "string": "cevParseString",
    "boolean": "cevParseBool",
    "double": "cevParseFloat",
    "integer[]": "cevParseIntSlice",
    "string[]": "cevParseStringSlice",
    "integer[][]": "cevParseIntMatrix",
}
_COMPLEX_TYPES = {"TreeNode", "ListNode"}


def _norm_type(t: str) -> str:
    """Normalize a metaData type to its base (drop a leading '*')."""
    return (t or "").strip().lstrip("*")


def _supported_type(t: str) -> bool:
    base = _norm_type(t)
    return base in _SCALAR_PARSERS or base in _COMPLEX_TYPES


# --------------------------------------------------------------------------- #
# Test-case parsing
# --------------------------------------------------------------------------- #
def parse_test_cases(record: dict | None) -> list[dict]:
    """Return ``[{"inputs": [str,...], "expected": str|None}, ...]`` for a record.

    Inputs are read from ``exampleTestcaseList`` (each entry split on newlines).
    Expected outputs are scraped from the ``**Output:**`` lines of ``description_md``,
    aligned to the inputs by example order.
    """
    if not record:
        return []
    raw_cases = record.get("exampleTestcaseList") or []
    inputs = [c.split("\n") for c in raw_cases]

    expected: list[str | None] = []
    md = record.get("description_md") or ""
    for line in md.splitlines():
        m = re.match(r"^\s*\*\*Output:\*\*\s*(.*\S)\s*$", line)
        if m:
            expected.append(m.group(1).strip())

    cases: list[dict] = []
    for i, fields in enumerate(inputs):
        exp = expected[i] if i < len(expected) else None
        cases.append({"inputs": fields, "expected": exp})
    return cases


def _parse_meta(record: dict | None) -> dict:
    """Parse the problem ``metaData`` JSON string into a dict (best-effort)."""
    if not record:
        return {}
    meta_raw = record.get("metaData") or ""
    if not meta_raw.strip():
        return {}
    try:
        return json.loads(meta_raw)
    except json.JSONDecodeError:
        return {}


# --------------------------------------------------------------------------- #
# Harness generation
# --------------------------------------------------------------------------- #
def _go_major_minor() -> str:
    """Detect the installed Go version (e.g. '1.22') for go.mod; default 1.21."""
    try:
        out = subprocess.run(
            ["go", "version"], capture_output=True, text=True, timeout=10
        ).stdout
        m = re.search(r"go(\d+)\.(\d+)", out)
        if m:
            return f"{m.group(1)}.{m.group(2)}"
    except Exception:
        pass
    return "1.21"


def _emit_helpers(meta: dict) -> tuple[str, set[str]]:
    """Generate ``verify_helpers.go`` and the set of needed stdlib imports."""
    params = meta.get("params", []) or []
    ret = meta.get("return") or {}
    types_used = {_norm_type(p.get("type", "")) for p in params}
    types_used.add(_norm_type(ret.get("type")))

    scalar_needed = {t for t in types_used if t in _SCALAR_PARSERS}
    complex_needed = {t for t in types_used if t in _COMPLEX_TYPES}

    helpers: list[str] = []
    helpers.append(
        "func cevParseInt(s string) int {\n"
        "\tvar v int\n\t_ = json.Unmarshal([]byte(s), &v)\n\treturn v\n}"
    )
    helpers.append(
        "func cevParseIntSlice(s string) []int {\n"
        "\tvar v []int\n\t_ = json.Unmarshal([]byte(s), &v)\n\treturn v\n}"
    )
    helpers.append(
        "func cevParseStringSlice(s string) []string {\n"
        "\tvar v []string\n\t_ = json.Unmarshal([]byte(s), &v)\n\treturn v\n}"
    )
    helpers.append(
        "func cevParseIntMatrix(s string) [][]int {\n"
        "\tvar v [][]int\n\t_ = json.Unmarshal([]byte(s), &v)\n\treturn v\n}"
    )
    helpers.append(
        "func cevParseBool(s string) bool {\n"
        "\tvar v bool\n\t_ = json.Unmarshal([]byte(s), &v)\n\treturn v\n}"
    )
    helpers.append(
        "func cevParseFloat(s string) float64 {\n"
        "\tvar v float64\n\t_ = json.Unmarshal([]byte(s), &v)\n\treturn v\n}"
    )
    helpers.append(
        "func cevSerialize(v interface{}) string {\n"
        "\tb, _ := json.Marshal(v)\n\treturn string(b)\n}"
    )
    # cevEqual / cevSorted: order-insensitive comparison for multi-answer
    # problems only (MODEL_TUNING_SPEC §3.2 / VERIFIER_ACCEPTANCE §8).
    helpers.append(
        "// cevEqual compares two JSON-serialized verifier outputs.\n"
        "// normalize==true (multi_answer problem): scalar-element slices are\n"
        "// order-normalized (parse -> sort -> compare) so valid reorderings such as\n"
        "// two-sum index pairs [0,1] vs [1,0] count as equal. Otherwise the comparison\n"
        "// is order-sensitive (whitespace-stripped string equality), matching\n"
        "// VERIFIER_ACCEPTANCE.md §4.\n"
        "func cevEqual(got, expected string, normalize bool) bool {\n"
        "\tif normalize {\n"
        "\t\tvar g, e interface{}\n"
        "\t\tif json.Unmarshal([]byte(got), &g) == nil &&\n"
        "\t\t\tjson.Unmarshal([]byte(expected), &e) == nil {\n"
        "\t\t\tif gs, ok := g.([]interface{}); ok {\n"
        "\t\t\t\tif es, ok2 := e.([]interface{}); ok2 && len(gs) > 0 {\n"
        "\t\t\t\t\tif _, isScalar := gs[0].(float64); isScalar {\n"
        "\t\t\t\t\t\treturn cevSorted(gs) == cevSorted(es)\n"
        "\t\t\t\t\t}\n"
        "\t\t\t\t}\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "\t}\n"
        "\treturn strings.ReplaceAll(got, \" \", \"\") == "
        "strings.ReplaceAll(strings.TrimSpace(expected), \" \", \"\")\n}"
    )
    helpers.append(
        "// cevSorted stringifies slice elements and returns them sorted, so element\n"
        "// order no longer matters for scalar-element slices.\n"
        "func cevSorted(xs []interface{}) string {\n"
        "\tstrs := make([]string, len(xs))\n"
        "\tfor i, v := range xs {\n"
        "\t\tstrs[i] = fmt.Sprint(v)\n"
        "\t}\n"
        "\tsort.Strings(strs)\n"
        "\treturn strings.Join(strs, \",\")\n}"
    )

    # "fmt" / "sort" are always required by cevSorted, which is always emitted.
    imports = {"encoding/json", "fmt", "sort", "strings"}

    if complex_needed:
        imports.add("strconv")
        helpers.append(
            "func cevToFloat(v interface{}) float64 {\n"
            "\tswitch x := v.(type) {\n"
            "\tcase float64:\n\t\treturn x\n"
            "\tcase int:\n\t\treturn float64(x)\n"
            "\tcase string:\n\t\tf, _ := strconv.ParseFloat(x, 64)\n\t\treturn f\n"
            "\t}\n\treturn 0\n}"
        )
    if "TreeNode" in complex_needed:
        helpers.append(_TREE_HELPERS)
    if "ListNode" in complex_needed:
        helpers.append(_LIST_HELPERS)

    import_block = "import (\n" + "".join(f'\t"{i}"\n' for i in sorted(imports)) + ")"
    body = (
        "package main\n\n"
        + import_block
        + "\n\n"
        + "\n\n".join(helpers)
        + "\n"
    )
    return body, imports


_TREE_HELPERS = (
    "func cevParseTreeNode(s string) *TreeNode {\n"
    "\tvar raw []interface{}\n\t_ = json.Unmarshal([]byte(s), &raw)\n"
    "\tif len(raw) == 0 || raw[0] == nil {\n\t\treturn nil\n\t}\n"
    "\troot := &TreeNode{Val: int(cevToFloat(raw[0]))}\n"
    "\tqueue := []*TreeNode{root}\n\ti := 1\n"
    "\tfor len(queue) > 0 && i < len(raw) {\n"
    "\t\tn := queue[0]\n\t\tqueue = queue[1:]\n"
    "\t\tif i < len(raw) && raw[i] != nil {\n"
    "\t\t\tn.Left = &TreeNode{Val: int(cevToFloat(raw[i]))}\n"
    "\t\t\tqueue = append(queue, n.Left)\n\t\t}\n\t\ti++\n"
    "\t\tif i < len(raw) && raw[i] != nil {\n"
    "\t\t\tn.Right = &TreeNode{Val: int(cevToFloat(raw[i]))}\n"
    "\t\t\tqueue = append(queue, n.Right)\n\t\t}\n\t\ti++\n\t}\n"
    "\treturn root\n}\n"
    "func cevSerializeTree(root *TreeNode) string {\n"
    "\tif root == nil {\n\t\treturn \"[]\"\n\t}\n"
    "\tout := []interface{}{}\n\tqueue := []*TreeNode{root}\n"
    "\tfor len(queue) > 0 {\n\t\tn := queue[0]\n\t\tqueue = queue[1:]\n"
    "\t\tif n == nil {\n\t\t\tout = append(out, nil)\n\t\t\tcontinue\n\t\t}\n"
    "\t\tout = append(out, n.Val)\n\t\tqueue = append(queue, n.Left, n.Right)\n\t}\n"
    "\tfor len(out) > 0 {\n\t\tif out[len(out)-1] == nil {\n\t\t\tout = out[:len(out)-1]\n\t\t\tcontinue\n\t\t}\n\t\tbreak\n\t}\n"
    "\tb, _ := json.Marshal(out)\n\treturn string(b)\n}"
)

_LIST_HELPERS = (
    "func cevParseListNode(s string) *ListNode {\n"
    "\tvar raw []interface{}\n\t_ = json.Unmarshal([]byte(s), &raw)\n"
    "\tdummy := &ListNode{}\n\tcur := dummy\n"
    "\tfor _, v := range raw {\n\t\tif v == nil {\n\t\t\tcontinue\n\t\t}\n"
    "\t\tcur.Next = &ListNode{Val: int(cevToFloat(v))}\n\t\tcur = cur.Next\n\t}\n"
    "\treturn dummy.Next\n}\n"
    "func cevSerializeList(l *ListNode) string {\n"
    "\tout := []interface{}{}\n\tfor l != nil {\n\t\tout = append(out, l.Val)\n\t\tl = l.Next\n\t}\n"
    "\tb, _ := json.Marshal(out)\n\treturn string(b)\n}"
)


def _return_serializer(ret_type: str) -> str:
    base = _norm_type(ret_type)
    if base == "TreeNode":
        return "cevSerializeTree"
    if base == "ListNode":
        return "cevSerializeList"
    return "cevSerialize"


def _emit_test(meta: dict, cases: list[dict], mode: str, normalize: bool = False) -> str:
    """Generate ``verify_test.go`` for the given cases and verify mode.

    ``normalize`` comes from the problem record's ``multi_answer`` flag: when
    True the emitted ``cevEqual`` call compares scalar-element slices
    order-insensitively (two-sum ``[1,0]`` vs ``[0,1]``); otherwise the
    comparison stays order-sensitive.
    """
    name = meta.get("name") or "Solution"
    params = meta.get("params", []) or []

    case_struct_lines: list[str] = []
    skipped: list[int] = []
    for i, c in enumerate(cases):
        if len(c["inputs"]) < len(params):
            skipped.append(i)
            continue
        ins = ", ".join(json.dumps(f) for f in c["inputs"])
        exp = json.dumps(c["expected"]) if c.get("expected") else '""'
        case_struct_lines.append(f"\t\t{{inputs: []string{{{ins}}}, expected: {exp}}},")

    body_lines: list[str] = []
    for j, p in enumerate(params):
        parser = _SCALAR_PARSERS.get(_norm_type(p.get("type", "")))
        if parser is None:
            parser = "cevParseTreeNode" if _norm_type(p.get("type")) == "TreeNode" else "cevParseListNode"
        body_lines.append(f"\t\tp{j} := {parser}(c.inputs[{j}])")
    args = ", ".join(f"p{j}" for j in range(len(params)))
    serializer = _return_serializer((meta.get("return") or {}).get("type", ""))
    body_lines.append(f"\t\tgot := {name}({args})")
    body_lines.append(f"\t\tgotJSON := {serializer}(got)")
    # Keep `gotJSON` used in smoke mode (Go rejects unused vars) without asserting.
    if mode == "smoke":
        body_lines.append("\t\t_ = gotJSON")
    else:
        normalize_lit = "true" if normalize else "false"
        body_lines.append(
            "\t\tif c.expected != \"\" {\n"
            f"\t\t\tif !cevEqual(gotJSON, c.expected, {normalize_lit}) {{\n"
            "\t\t\t\tt.Errorf(\"case %d: expected %s, got %s\", i, c.expected, gotJSON)\n"
            "\t\t\t}\n\t\t}"
        )

    cases_block = "\n".join(case_struct_lines) if case_struct_lines else "\t\t// (no runnable cases)"
    # In smoke mode there is no assertion, so the loop index `i` would be unused.
    loop_header = "\tfor i, c := range cases {" if mode != "smoke" else "\tfor _, c := range cases {"
    test = (
        "package main\n\n"
        "import \"testing\"\n\n"
        "func TestVerifier(t *testing.T) {\n"
        "\tcases := []struct {\n"
        "\t\tinputs   []string\n"
        "\t\texpected string\n"
        "\t}{\n"
        f"{cases_block}\n"
        "\t}\n"
        f"{loop_header}\n"
        + "\n".join(body_lines)
        + "\n\t}\n}"
    )
    return test


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def _parse_go_test(raw: str) -> list[str]:
    """Extract human-readable failure lines from `go test` output.

    ``t.Errorf`` lines are prefixed by Go with ``file:line:`` (e.g.
    ``verify_test.go:32: case 0: expected [0,1], got [1,2]``), so we match on
    the message content rather than assuming the line starts with ``case ``.
    """
    details: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("--- FAIL"):
            details.append(s)
        elif "expected" in s and "got" in s:
            details.append(s)
    return details


def run_go_verify(verify_dir: Path, timeout: int = VERIFY_TIMEOUT_DEFAULT) -> dict:
    """Run ``go test`` in ``verify_dir`` and classify the outcome.

    Returns ``{"ok", "kind", "raw", "details"}``. ``kind`` is one of
    pass | timeout | panic | assert.
    """
    proc = subprocess.run(
        ["go", "test", "-run", "TestVerifier", "-timeout", f"{timeout}s", "-v", "."],
        cwd=str(verify_dir),
        capture_output=True,
        text=True,
    )
    raw = (proc.stdout or "") + "\n" + (proc.stderr or "")
    details = _parse_go_test(raw)

    if proc.returncode == 0:
        return {"ok": True, "kind": "pass", "raw": raw, "details": details}
    if "test timed out" in raw or "panic: test timed out" in raw:
        return {"ok": False, "kind": "timeout", "raw": raw, "details": details}
    if "panic:" in raw:
        return {"ok": False, "kind": "panic", "raw": raw, "details": details}
    return {"ok": False, "kind": "assert", "raw": raw, "details": details}


def verify_go_code(
    code_path: str | Path,
    record: dict | None = None,
    mode: str = "assert",
    timeout: int = VERIFY_TIMEOUT_DEFAULT,
) -> dict:
    """Verify that the Go file at ``code_path`` solves ``record``'s problem.

    Returns ``{"verify_result": str, "verify_details": list}`` where
    ``verify_result`` is one of VERIFY_PASS_MESSAGE / VERIFY_FAIL_PREFIX* /
    VERIFY_SKIP_MESSAGE.
    """
    # --- graceful skip conditions (AT-08 / AT-09) ---
    if mode == "off":
        return {"verify_result": VERIFY_SKIP_MESSAGE, "verify_details": []}

    meta = _parse_meta(record)
    cases = parse_test_cases(record)
    # Keep only cases whose types we can fully drive and that have enough inputs.
    runnable: list[dict] = []
    skipped_notes: list[str] = []
    if meta and cases:
        params = meta.get("params", []) or []
        ret = (meta.get("return") or {}).get("type", "")
        fully_supported = all(_supported_type(p.get("type", "")) for p in params) and _supported_type(ret)
        if fully_supported:
            for c in cases:
                if len(c["inputs"]) >= len(params):
                    runnable.append(c)
                else:
                    skipped_notes.append(f"case with {len(c['inputs'])} inputs "
                                         f"(need {len(params)}) skipped")
        else:
            skipped_notes.append("unsupported param/return type, verification skipped")

    if not runnable:
        note = "; ".join(skipped_notes) or "no example test cases"
        return {
            "verify_result": VERIFY_SKIP_MESSAGE,
            "verify_details": [{"skipped": note}],
        }

    # In assert mode we MUST have expected outputs; otherwise we would falsely
    # "pass" code that merely runs. Degrade to skip instead of a misleading pass.
    assertable = [c for c in runnable if c.get("expected")]
    if mode == "assert" and not assertable:
        return {
            "verify_result": VERIFY_SKIP_MESSAGE,
            "verify_details": [
                {"skipped": "assert mode but no expected outputs parseable from problem description"}
            ]
            + [{"skipped": n} for n in skipped_notes],
        }
    exec_cases = assertable if mode == "assert" else runnable

    task_dir = Path(code_path).parent
    verify_dir = task_dir / "verify"
    if verify_dir.exists():
        shutil.rmtree(verify_dir)
    verify_dir.mkdir(parents=True, exist_ok=True)

    try:
        solution = Path(code_path).read_text(encoding="utf-8")
    except Exception:
        return {"verify_result": VERIFY_SKIP_MESSAGE, "verify_details": [{"skipped": "cannot read code"}]}

    # multi_answer problems (e.g. two-sum) accept any valid ordering of the
    # returned scalars, so their comparison is order-normalized. Everything else
    # keeps the order-sensitive comparison (VERIFIER_ACCEPTANCE §4).
    normalize = bool((record or {}).get("multi_answer"))
    helpers_src, _ = _emit_helpers(meta)
    test_src = _emit_test(meta, exec_cases, mode, normalize=normalize)
    go_mod = f"module codeengineverify\n\ngo {_go_major_minor()}\n"

    (verify_dir / "solution.go").write_text(solution, encoding="utf-8")
    (verify_dir / "verify_helpers.go").write_text(helpers_src, encoding="utf-8")
    (verify_dir / "verify_test.go").write_text(test_src, encoding="utf-8")
    (verify_dir / "go.mod").write_text(go_mod, encoding="utf-8")

    try:
        run = run_go_verify(verify_dir, timeout=timeout)
    except FileNotFoundError:
        # `go` not on PATH — degrade gracefully rather than crash the pipeline.
        return {
            "verify_result": VERIFY_SKIP_MESSAGE,
            "verify_details": [{"skipped": "go toolchain not available"}],
        }

    # Persist a sidecar so the API can surface a `verified` badge without re-running.
    sidecar = {
        "verify_result": "",
        "verify_details": run["details"] or skipped_notes,
        "mode": mode,
    }
    if run["ok"]:
        sidecar["verify_result"] = VERIFY_PASS_MESSAGE
    else:
        if run["kind"] == "timeout":
            msg = f"timeout (exceeded {timeout}s)"
        elif run["kind"] == "panic":
            msg = "runtime panic during execution"
        else:
            msg = "output did not match expected"
        sidecar["verify_result"] = VERIFY_FAIL_PREFIX + msg
    try:
        (task_dir / "verify_result.json").write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    return {
        "verify_result": sidecar["verify_result"],
        "verify_details": sidecar["verify_details"],
    }
