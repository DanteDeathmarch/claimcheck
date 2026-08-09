#!/usr/bin/env python3
"""
claimcheck -- verify an AI agent actually did what it said it did.

Every agent-monitoring tool on the market measures cost, tokens, and latency. None of
them answer the question that actually costs you: **was the reported success true?**

An agent says "done, pushed to main." The exit code is 0. The log looks clean. And:
  - the file it wrote is 40 bytes of "# TODO: implement"
  - the commit is local, never pushed
  - the command printed "You've hit your limit" and still exited 0
  - the scheduled task registered fine and has never once executed

All four of those are real failures from one night of unattended agent work.

    claimcheck file dist/report.pdf --min-bytes 5000
    claimcheck cmd "npm test" --no-fail-text
    claimcheck url https://example.com/api --status 200 --contains '"ok":true'
    claimcheck pushed ./myrepo
    claimcheck run claims.json          # a whole batch

Exit: 0 = every claim verified · 1 = a claim is false · 2 = usage error

Stdlib only. No install, no network calls except the URLs you name. MIT.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

__version__ = "1.0.0"

# THE CORE INSIGHT.
#
# A zero exit code is not success. Rate limits, auth failures and quota errors are
# routinely printed to stdout by tools that then exit 0 -- Claude Code returns
# "You've hit your limit" as normal output with exit code 0, so any script watching
# only the exit code reads it as a valid answer and retries forever.
#
# We hit the same class of bug twice in one night: `grep '^ERROR'` reported zero
# failures across 31,000 psql errors, and `$?` after a pipe reported the pipe's status
# instead of the program's.
FAIL_TEXT = [
    # Written loosely on purpose. The first version used "you've hit your limit" and
    # missed "You have hit your limit" in its own test -- the flagship check failing
    # the flagship case because of an apostrophe.
    r"hit (your|the) (usage )?limit", r"limit reached", r"limit exceeded",
    r"rate.?limit", r"quota exceeded", r"quota exhausted", r"429",
    r"permission denied", r"not authoriz", r"unauthorized", r"forbidden",
    r"authentication fail", r"oauth session expired", r"invalid api key",
    r"command not found", r"no such file or directory", r"traceback \(most recent",
    r"fatal:", r"panic:", r"segmentation fault", r"out of memory",
    r"connection refused", r"could not resolve", r"timed out",
    r"\berror\b:", r"\bfailed\b:",
]

# Text that means an agent wrote a stub and called it finished.
STUB_TEXT = [
    r"#\s*TODO", r"//\s*TODO", r"\bFIXME\b", r"\bXXX\b",
    r"lorem ipsum", r"placeholder", r"not implemented",
    r"implement this", r"your code here", r"coming soon",
]


class Result:
    def __init__(self, claim, ok, detail):
        self.claim, self.ok, self.detail = claim, ok, detail


def _match(patterns, text):
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0)
    return None


def check_file(path, min_bytes=1, contains=None, allow_stub=False):
    p = Path(path)
    if not p.exists():
        return Result(f"file {path}", False, "does not exist")
    size = p.stat().st_size
    if size < min_bytes:
        return Result(f"file {path}", False, f"{size} bytes, expected >= {min_bytes}")
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return Result(f"file {path}", False, f"unreadable: {e}")
    if not text.strip():
        return Result(f"file {path}", False, "exists but is empty/whitespace")
    if contains and contains not in text:
        return Result(f"file {path}", False, f"missing expected text: {contains[:40]!r}")
    if not allow_stub:
        # Density, not presence. A 400-line module with one TODO is real work; a
        # 40-byte file that is nothing but "# TODO: implement" is a stub the agent
        # called finished.
        #
        # This tool tripped its own check on the first run, because the file contains
        # the very patterns it searches for. Same self-matching trap as a process
        # query that counts itself. Presence alone is never enough.
        lines = [l for l in text.splitlines() if l.strip()]
        stub_lines = sum(1 for l in lines if _match(STUB_TEXT, l))
        density = stub_lines / len(lines) if lines else 0
        hit = _match(STUB_TEXT, text)
        if hit and (size < 400 or density > 0.25):
            return Result(f"file {path}", False,
                          f"looks like a stub — {stub_lines}/{len(lines)} lines are "
                          f"placeholders (e.g. {hit!r})")
    return Result(f"file {path}", True, f"{size} bytes")


def _pipe_masks_exit(cmd):
    """True if a shell pipeline hides the exit code you probably care about.

    `prog | tail` reports TAIL's status, not prog's. So a failing program inside a
    pipeline looks like success, and every exit-code check downstream is meaningless.

    This has bitten the author of this tool three times in one night: a test harness
    that reported a passing check that had never run, twice; and a service-status
    script that printed ACTIVE while reporting exit 0.

    Excludes pipelines already protected by `set -o pipefail` or ${PIPESTATUS}.
    """
    if "|" not in cmd:
        return False
    # ||  is boolean OR, not a pipe.
    stripped = cmd.replace("||", "")
    if "|" not in stripped:
        return False
    if "pipefail" in cmd or "PIPESTATUS" in cmd:
        return False
    return True


def check_cmd(cmd, expect_exit=0, no_fail_text=True, contains=None, timeout=300):
    if _pipe_masks_exit(cmd) and expect_exit is not None:
        return Result(f"cmd {cmd[:44]}", False,
                      "PIPELINE MASKS THE EXIT CODE — this reports the LAST command's "
                      "status, not the one you care about. Add 'set -o pipefail;' or "
                      "check the program directly.")
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(f"cmd {cmd[:44]}", False, f"timed out after {timeout}s")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != expect_exit:
        return Result(f"cmd {cmd[:44]}", False,
                      f"exit {r.returncode}, expected {expect_exit}")
    # The whole point: a correct exit code with failure text in the output.
    if no_fail_text:
        hit = _match(FAIL_TEXT, out)
        if hit:
            return Result(f"cmd {cmd[:44]}", False,
                          f"exit {r.returncode} BUT output says {hit!r} — "
                          f"success code, failed run")
    if contains and contains not in out:
        return Result(f"cmd {cmd[:44]}", False, f"output missing {contains[:40]!r}")
    return Result(f"cmd {cmd[:44]}", True, f"exit {r.returncode}, output clean")


def _github_api_content(url):
    """For raw.githubusercontent URLs, read via the API instead of the CDN.

    raw.githubusercontent.com caches for minutes. A check run right after a push reads
    the OLD file and calls a true claim false — that happened in real use: the content
    was on the remote, both HEADs matched, and this still said "body missing".
    Cache-busting headers and query params did not beat it.

    The API is authoritative and uncached. Falls back to the CDN if `gh` is absent.
    Returns the body, or None to fall through.
    """
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)", url)
    if not m:
        return None
    owner, repo, ref, path = m.groups()
    path = path.split("?")[0]
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/contents/{path}?ref={ref}",
             "-q", ".content"],
            capture_output=True, text=True, timeout=90)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        import base64
        return base64.b64decode(r.stdout).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return None


def check_url(url, status=200, contains=None, timeout=25):
    api_body = _github_api_content(url)
    if api_body is not None:
        if contains and contains not in api_body:
            return Result(f"url {url[:44]}", False,
                          f"body missing {contains[:40]!r} (via GitHub API)")
        return Result(f"url {url[:44]}", True, "200 (via GitHub API, uncached)")

    headers = {"User-Agent": "claimcheck/1.0",
               "Cache-Control": "no-cache, max-age=0",
               "Pragma": "no-cache"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, body = r.getcode(), r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        code, body = e.code, ""
    except Exception as e:  # noqa: BLE001
        return Result(f"url {url[:44]}", False, f"unreachable: {type(e).__name__}")
    if code != status:
        return Result(f"url {url[:44]}", False, f"HTTP {code}, expected {status}")
    if contains and contains not in body:
        return Result(f"url {url[:44]}", False, f"body missing {contains[:40]!r}")
    return Result(f"url {url[:44]}", True, f"HTTP {code}")


def check_pushed(repo="."):
    """'I pushed it' is the most common false claim. Compare local HEAD to the remote."""
    def git(*a):
        return subprocess.run(["git", "-C", repo, *a], capture_output=True,
                              text=True, timeout=90)
    if not (Path(repo) / ".git").exists():
        return Result(f"pushed {repo}", False, "not a git repo")
    dirty = git("status", "--porcelain").stdout.strip()
    if dirty:
        n = len(dirty.splitlines())
        return Result(f"pushed {repo}", False, f"{n} uncommitted change(s)")
    local = git("rev-parse", "HEAD").stdout.strip()
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git("fetch", "--quiet")
    remote = git("rev-parse", f"origin/{branch}").stdout.strip()
    if not remote:
        return Result(f"pushed {repo}", False, f"no origin/{branch} — never pushed")
    if local != remote:
        ahead = git("rev-list", "--count", f"origin/{branch}..HEAD").stdout.strip()
        return Result(f"pushed {repo}", False,
                      f"{ahead} commit(s) ahead of origin/{branch} — NOT pushed")
    return Result(f"pushed {repo}", True, f"in sync with origin/{branch}")


def check_process(name):
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}"],
                               capture_output=True, text=True, timeout=60)
            alive = name.lower() in r.stdout.lower()
        else:
            r = subprocess.run(["pgrep", "-f", name], capture_output=True,
                               text=True, timeout=60)
            alive = bool(r.stdout.strip())
    except Exception as e:  # noqa: BLE001
        return Result(f"process {name}", False, f"could not check: {type(e).__name__}")
    return Result(f"process {name}", alive,
                  "running" if alive else "NOT running")


DISPATCH = {
    "file": lambda c: check_file(c["path"], c.get("min_bytes", 1),
                                 c.get("contains"), c.get("allow_stub", False)),
    "cmd": lambda c: check_cmd(c["cmd"], c.get("expect_exit", 0),
                               c.get("no_fail_text", True), c.get("contains"),
                               c.get("timeout", 300)),
    "url": lambda c: check_url(c["url"], c.get("status", 200), c.get("contains")),
    "pushed": lambda c: check_pushed(c.get("repo", ".")),
    "process": lambda c: check_process(c["name"]),
}


def run_batch(path):
    p = Path(path)
    if not p.exists():
        print(f"No such claims file: {path}", file=sys.stderr)
        return None
    try:
        claims = json.loads(p.read_text(encoding="utf-8"))["claims"]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Malformed claims file ({type(e).__name__}) — expected "
              f'{{"claims": [...]}}', file=sys.stderr)
        return None
    out = []
    for c in claims:
        kind = c.get("type")
        if kind not in DISPATCH:
            out.append(Result(f"{kind}", False, f"unknown claim type '{kind}'"))
            continue
        try:
            out.append(DISPATCH[kind](c))
        except KeyError as e:
            out.append(Result(f"{kind}", False, f"claim missing field {e}"))
    return out


def report(results, as_json=False):
    failed = [r for r in results if not r.ok]
    if as_json:
        print(json.dumps({
            "version": __version__,
            "verdict": "VERIFIED" if not failed else "FALSE_CLAIM",
            "checked": len(results), "failed": len(failed),
            "results": [{"claim": r.claim, "ok": r.ok, "detail": r.detail}
                        for r in results]}, indent=2))
    else:
        for r in results:
            print(f"  {'OK  ' if r.ok else 'FALSE'}  {r.claim:<46} {r.detail}")
        print("-" * 78)
        if failed:
            print(f"  FALSE CLAIMS: {len(failed)} of {len(results)}")
            print("  The agent reported success. Reality disagrees.")
        else:
            print(f"  all {len(results)} claim(s) verified")
    return 1 if failed else 0


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("--help", "-h"):
        print(__doc__)
        return 0 if a else 2
    if a[0] == "--version":
        print(f"claimcheck {__version__}")
        return 0

    as_json = "--json" in a
    def opt(flag, default=None, cast=str):
        return cast(a[a.index(flag) + 1]) if flag in a else default

    cmd = a[0]
    try:
        if cmd == "file":
            res = [check_file(a[1], opt("--min-bytes", 1, int),
                              opt("--contains"), "--allow-stub" in a)]
        elif cmd == "cmd":
            res = [check_cmd(a[1], opt("--expect-exit", 0, int),
                             "--allow-fail-text" not in a, opt("--contains"))]
        elif cmd == "url":
            res = [check_url(a[1], opt("--status", 200, int), opt("--contains"))]
        elif cmd == "pushed":
            res = [check_pushed(a[1] if len(a) > 1 and not a[1].startswith("--") else ".")]
        elif cmd == "process":
            res = [check_process(a[1])]
        elif cmd == "run":
            res = run_batch(a[1])
            if res is None:
                return 2
        else:
            print(f"Unknown command '{cmd}'. See --help.", file=sys.stderr)
            return 2
    except IndexError:
        print(f"'{cmd}' needs an argument. See --help.", file=sys.stderr)
        return 2

    return report(res, as_json)


if __name__ == "__main__":
    sys.exit(main())
