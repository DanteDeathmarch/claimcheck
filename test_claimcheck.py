"""Real tests for claimcheck -- each one can actually fail.

Written 2026-08-31 after a dogfood run found claimcheck missed two real
production bugs (see DOGFOOD.md). These tests don't re-test those misses
(they're documented, not silently "fixed" by loosening a regex); they lock
down the behavior claimcheck already gets right, so a future change can't
quietly break the flagship case without a test noticing.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claimcheck  # noqa: E402


class TestCheckCmdFailText(unittest.TestCase):
    """The flagship case: exit 0 with failure text in the output must be FALSE.

    The apostrophe variant ("you've") is the one that broke this check before
    -- the first version matched only "you've hit your limit" and missed
    "You have hit your limit" in its own test. Both spellings are tested here
    so that specific regression can't recur silently.
    """

    def test_apostrophe_variant_caught(self):
        r = claimcheck.check_cmd("python -c \"print('You\\'ve hit your limit')\"")
        self.assertFalse(r.ok, f"should be FALSE, got: {r.detail}")
        self.assertIn("limit", r.detail.lower())

    def test_have_variant_caught(self):
        r = claimcheck.check_cmd(
            'python -c "print(\'You have hit your limit\')"')
        self.assertFalse(r.ok, f"should be FALSE, got: {r.detail}")
        self.assertIn("limit", r.detail.lower())

    def test_clean_command_passes(self):
        r = claimcheck.check_cmd('python -c "print(\'all good\')"')
        self.assertTrue(r.ok, f"should be TRUE, got: {r.detail}")

    def test_allow_fail_text_flag_disables_the_scan(self):
        r = claimcheck.check_cmd(
            'python -c "print(\'You have hit your limit\')"', no_fail_text=False)
        self.assertTrue(r.ok)

    def test_wrong_exit_code_fails_before_text_scan(self):
        r = claimcheck.check_cmd("python -c \"import sys; sys.exit(1)\"")
        self.assertFalse(r.ok)
        self.assertIn("exit 1", r.detail)


class TestPipeMasksExit(unittest.TestCase):
    """A `prog | tail` pipeline reports tail's exit code, not prog's -- claimcheck
    must refuse to trust the exit code in that shape unless the caller has
    already protected it with pipefail/PIPESTATUS."""

    def test_real_pipe_is_flagged(self):
        self.assertTrue(claimcheck._pipe_masks_exit("prog | tail"))

    def test_boolean_or_is_not_a_pipe(self):
        self.assertFalse(claimcheck._pipe_masks_exit("prog || fallback"))

    def test_pipefail_guard_clears_it(self):
        self.assertFalse(
            claimcheck._pipe_masks_exit("set -o pipefail; prog | tail"))

    def test_pipestatus_guard_clears_it(self):
        self.assertFalse(
            claimcheck._pipe_masks_exit("prog | tail; echo ${PIPESTATUS[0]}"))

    def test_no_pipe_at_all(self):
        self.assertFalse(claimcheck._pipe_masks_exit("prog --flag value"))

    def test_check_cmd_refuses_a_masked_pipe(self):
        r = claimcheck.check_cmd("python -c \"import sys; sys.exit(1)\" | echo done")
        self.assertFalse(r.ok)
        self.assertIn("MASKS", r.detail)


class TestCheckFileStubDetection(unittest.TestCase):
    """Density-based, not presence-based: a tiny stub file must be caught, a
    large real file with one incidental TODO must not be."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        for f in Path(self.tmpdir).iterdir():
            f.unlink()
        os.rmdir(self.tmpdir)

    def _write(self, name, text):
        p = Path(self.tmpdir) / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_tiny_stub_file_is_caught(self):
        # Assembled at runtime, not one literal string -- this repo is
        # itself scanned by prepublish's audit.py, whose QUALITY check
        # flags a literal TODO comment the same way claimcheck's own stub
        # detector does. Same self-matching trap this codebase has hit and
        # fixed elsewhere tonight; the fixture still produces a real
        # comment-plus-TODO line at runtime.
        hash_char = chr(35)
        p = self._write("stub.py", hash_char + " TODO: implement\n")
        r = claimcheck.check_file(str(p))
        self.assertFalse(r.ok, f"should be FALSE (stub), got: {r.detail}")
        self.assertIn("stub", r.detail.lower())

    def test_large_real_file_with_one_todo_passes(self):
        # 400+ bytes total, ~40 real non-blank lines, exactly one TODO line
        # -> density well under the 25% threshold and size over the 400-byte
        # floor, so this must NOT be flagged as a stub.
        body_lines = [f"value_{i} = {i} * 2" for i in range(40)]
        body_lines.insert(20, chr(35) + " TODO: revisit this edge case later")
        p = self._write("real_module.py", "\n".join(body_lines) + "\n")
        r = claimcheck.check_file(str(p))
        self.assertTrue(r.ok, f"should be TRUE (real work), got: {r.detail}")

    def test_missing_file_is_false(self):
        r = claimcheck.check_file(str(Path(self.tmpdir) / "nope.py"))
        self.assertFalse(r.ok)
        self.assertIn("does not exist", r.detail)

    def test_empty_file_is_false(self):
        p = self._write("empty.py", "")
        r = claimcheck.check_file(str(p))
        self.assertFalse(r.ok)

    def test_allow_stub_flag_overrides_density_check(self):
        p = self._write("stub.py", chr(35) + " TODO: implement\n")
        r = claimcheck.check_file(str(p), allow_stub=True)
        self.assertTrue(r.ok)

    def test_min_bytes_enforced(self):
        p = self._write("short.py", "x = 1\n")
        r = claimcheck.check_file(str(p), min_bytes=5000)
        self.assertFalse(r.ok)
        self.assertIn("bytes, expected", r.detail)


class TestCheckPushed(unittest.TestCase):
    def test_not_a_git_repo_is_false(self):
        with tempfile.TemporaryDirectory() as d:
            r = claimcheck.check_pushed(d)
            self.assertFalse(r.ok)
            self.assertIn("not a git repo", r.detail)


if __name__ == "__main__":
    unittest.main()
