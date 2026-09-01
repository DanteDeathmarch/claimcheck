# Dogfood run — 2026-08-31

claimcheck pointed at two real silent failures already fixed in this org's own
repos tonight, plus one control case. Goal: does the tool actually catch what
it claims to catch. Misses are the valuable half — recorded honestly, not
softened.

Env: Windows, Spanish system locale (`chcp` → codepage 65001 active, OS strings
in Spanish — confirmed live, matches the real box the em-dash bug happened on).

---

## Case A — em-dash in a REM comment (nightshift `run-shift.cmd`, fixed in `4d2e780`)

Real bug: `REM nightshift worker — runs the queue...` — the em-dash's UTF-8
bytes misparse under the system codepage, cmd.exe throws a parse error to
stderr, and the script still exits 0.

**Repro built:** 3-line `.cmd` with the same em-dash REM line followed by
`echo did real work >> case_a_output.log`.

**Command:**
```
python claimcheck.py cmd ".\case_a_emdash.cmd" --json
```

**Verbatim output:**
```json
{
  "version": "1.0.0",
  "verdict": "VERIFIED",
  "checked": 1,
  "failed": 0,
  "results": [
    {"claim": "cmd .\\case_a_emdash.cmd", "ok": true, "detail": "exit 0, output clean"}
  ]
}
```

Actual subprocess result (captured separately for verification):
```
returncode: 0
stderr: '"M" no se reconoce como un comando interno o externo,\nprograma o archivo por lotes ejecutable.\n'
```

**Verdict: MISS.** claimcheck says `VERIFIED` while a real command-parse
failure is sitting in stderr. Cause: `FAIL_TEXT` in `claimcheck.py` is
English-only (`"command not found"`, `"no such file or directory"`, etc.) and
has no pattern for localized Windows error text. This is not a contrived edge
case — it is the exact box the real bug happened on. On this org's actual
Spanish-locale machine, claimcheck's flagship "exit 0 but the output says it
failed" check does not fire for a native OS error message.

**Caveat on scope:** in the real 5-line `run-shift.cmd`, "the real work ...
never happened" — the em-dash line aborted everything after it. In this
reduced 3-line repro, cmd.exe printed the parse error and *continued* to the
next line (`case_a_output.log` was created). The reduced repro is faithful for
what's under test here — exit 0 with a real error only visible on stderr — but
is not a perfect analog for the full-file abort behavior. Noted rather than
overclaimed.

---

## Case B — swallowed `IntegrityError` (an internal job-scheduler tick)

Real bug: `open_repair()` raised `sqlite3.IntegrityError` on a bad FK every
tick, caught by the tick's own broad `except Exception: pass`, never surfaced.

**Repro built:** Python script that triggers a real FK-violation
`IntegrityError` against an in-memory sqlite db (same violation shape as the
real bug — a non-existent `component_id`), catches it the same way
(`except Exception: pass`), and prints only `"tick complete"`.

**Command:**
```
python claimcheck.py cmd "python case_b_swallowed.py" --json
```

**Verbatim output:**
```json
{
  "version": "1.0.0",
  "verdict": "VERIFIED",
  "checked": 1,
  "failed": 0,
  "results": [
    {"claim": "cmd python case_b_swallowed.py", "ok": true, "detail": "exit 0, output clean"}
  ]
}
```

**Verdict: MISS.** claimcheck says `VERIFIED`. Cause: nothing about a silently
swallowed exception ever reaches stdout/stderr in the first place — there is
no text for `FAIL_TEXT` to match, because the real failure mode here isn't
"the output says it failed," it's "there is no output about the failure at
all." `check_cmd` can only judge what a process prints; it has no mechanism to
detect that an exception was caught and discarded inside the process. This is
a structural blind spot, not a missing regex — no pattern addition fixes it.
(Named plainly: verifying "no exception was silently swallowed" would require
either instrumenting the target code or diffing expected side effects, neither
of which `claimcheck cmd` does today.)

---

## Case D — control: `pushed` on a clean, actually-pushed repo

**Command:**
```
python claimcheck.py pushed ../nightshift --json
```

**Verbatim output** (shown with a relative path rather than the absolute one actually run, so no local machine detail publishes; every other field, including the verdict, is exactly what the tool printed):
```json
{
  "version": "1.0.0",
  "verdict": "VERIFIED",
  "checked": 1,
  "failed": 0,
  "results": [
    {"claim": "pushed ../nightshift", "ok": true, "detail": "in sync with origin/main"}
  ]
}
```

**Verdict: correct.** Working as intended on the trivial case.

---

## Summary

| case | real bug | claimcheck result | caught? |
|---|---|---|---|
| A — em-dash parse error | exit 0, stderr has native error text | VERIFIED | **MISS** — English-only `FAIL_TEXT` |
| B — swallowed IntegrityError | exit 0, no output at all about the failure | VERIFIED | **MISS** — structural: nothing to match on stdout/stderr |
| D — control (pushed, clean) | n/a | VERIFIED | correct |

**2 for 2 misses on the exact cases this tool exists to catch.** Both misses
are real, not test-harness artifacts:

- Case A is fixable — extend `FAIL_TEXT` (or add a `--locale` aware pass) to
  cover common non-English Windows parse/permission errors, or document that
  `claimcheck cmd` is English-output-only.
- Case B is not fixable by adding patterns — it needs a fundamentally different
  check (e.g., a `--python-trace`/exception-hook mode, or requiring the target
  to log its own exceptions somewhere claimcheck can read). Flag as a known
  limitation rather than silently pretending regex coverage will close it.

This is exactly the kind of finding Step 1 of the handoff plan was built to
surface: **the tool didn't catch the two failures it was dogfooded against.**
Recorded, not softened, per instruction.
