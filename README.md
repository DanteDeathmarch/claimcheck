# claimcheck

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/python-3.8%2B-blue) ![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)

**Verify an AI agent actually did what it said it did.**

For anyone running Claude Code, Cursor, Aider, or any agent unattended — and finding out
the next morning that "done, pushed to main" wasn't true.

```bash
claimcheck cmd "npm run deploy"
```

```
  FALSE  cmd npm run deploy    exit 0 BUT output says 'hit your limit' — success code, failed run
------------------------------------------------------------------------------
  FALSE CLAIMS: 1 of 1
  The agent reported success. Reality disagrees.
```

## Why this exists

Every agent-monitoring tool measures **cost, tokens, and latency**. ccusage, ccflare,
Portkey, LiteLLM, Langfuse, Helicone — all excellent, all answering *"how much did it
spend and how fast."*

**None of them answer the question that actually costs you: was the reported success
true?**

An agent says "done." Exit code 0. Log looks clean. And:

- the file it wrote is 40 bytes of `# TODO: implement`
- the commit is local and was never pushed
- the command printed `You've hit your limit` **and still exited 0**
- the scheduled task registered fine and has never once executed

All four are real failures from a single night of unattended agent work. Every one
passed a conventional health check.

### The exit-code-zero lie

This is the failure mode worth the whole tool.

Claude Code returns `You've hit your limit` as **normal output with exit code 0**. Any
script watching the exit code reads that as a valid answer — so it retries, burns
tokens, and reports success. Anthropic's own post-mortem documents a hook chain that
*"recursed without timeout or depth limit"* and hung past its wall-clock budget.

Same class of bug bites everywhere: `grep '^ERROR'` reporting zero failures across
31,000 database errors because they were prefixed with `file:line:`. And `$?` after a
pipe reporting the *pipe's* status, not the program's.

**A zero exit code is a claim, not evidence.**

## Install

Nothing to install. Python 3.8+, standard library only.

```bash
curl -O https://raw.githubusercontent.com/DanteDeathmarch/claimcheck/main/claimcheck.py
```

## Usage

```bash
claimcheck file dist/report.pdf --min-bytes 5000     # exists, real size, not a stub
claimcheck cmd "npm test"                            # exit 0 AND no failure text
claimcheck url https://api.example.com --contains '"ok":true'
claimcheck pushed ./myrepo                           # local HEAD == remote HEAD
claimcheck process node                              # still alive
claimcheck run claims.json                           # a whole batch
```

**Exit codes:** `0` every claim verified · `1` a claim is false · `2` usage error

## What each check actually proves

| Check | Catches |
|---|---|
| `file` | missing · empty · under-size · **stub content** (density-based, not keyword presence) · missing required text |
| `cmd` | wrong exit code · **correct exit code with failure text in output** · missing expected output · timeout |
| `url` | wrong status · unreachable · body missing what was promised |
| `pushed` | uncommitted changes · no remote branch · **commits ahead of origin — "I pushed it" when it didn't** |
| `process` | claimed-running process that is not running |

### Stub detection uses density, not presence

A 400-line module with one `TODO` is real work. A 40-byte file that is nothing but
`# TODO: implement` is a stub an agent called finished. claimcheck flags on **ratio of
placeholder lines to real lines**, plus file size.

This tool tripped its own check on the first run, because the source contains the very
patterns it searches for. Presence alone is never enough — that's the same self-matching
trap as a process query that counts itself.

## Batch mode

```json
{
  "claims": [
    {"type": "file",    "path": "dist/bundle.js", "min_bytes": 10000},
    {"type": "cmd",     "cmd": "npm test"},
    {"type": "pushed",  "repo": "."},
    {"type": "url",     "url": "https://myapp.com/health", "contains": "ok"},
    {"type": "process", "name": "node"}
  ]
}
```

```bash
claimcheck run claims.json --json    # machine-readable, for CI
```

Drop it at the end of any agent run:

```bash
claude -p "build the thing" && claimcheck run claims.json || echo "AGENT LIED"
```

## What it does NOT do

- **It does not judge quality.** A file can be the right size, non-stub, and still
  wrong. This verifies claims, not correctness.
- It cannot detect a lie it has no check for. Write the claim you care about.
- `pushed` runs `git fetch` — it needs network and remote access.
- A verified claim is true for the moment it ran, nothing more.

**Treat a pass as "the specific claims I made were true", never as "the work is good."**

## License

MIT.