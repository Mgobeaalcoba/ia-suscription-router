import os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "check_dco.sh"


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="t@example.com")
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=True).stdout.strip()


class DcoCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        git(self.d, "init", "-q", "-b", "main")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "base")
        git(self.d, "checkout", "-q", "-b", "feature")

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, msg):
        git(self.d, "commit", "-q", "--allow-empty", "-m", msg)

    def check(self):
        return subprocess.run(["bash", str(SCRIPT), "main", "HEAD"], cwd=self.d, capture_output=True, text=True)

    def test_signed_commits_pass(self):
        self.commit("feat: algo\n\nSigned-off-by: Ana Pérez <ana@example.com>")
        git(self.d, "commit", "-q", "--allow-empty", "-s", "-m", "fix: otro")             # firmado con `git commit -s`
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("firmados", r.stdout)

    def test_an_unsigned_commit_fails_and_is_named(self):
        self.commit("feat: firmado\n\nSigned-off-by: Ana <ana@example.com>")
        self.commit("fix: sin firma")
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("sin firma", r.stdout)
        self.assertNotIn("feat: firmado", r.stdout)
        self.assertIn("git commit -s", r.stdout)

    def test_malformed_signoffs_do_not_count(self):
        for bad in ("Signed-off-by: sin email", "signed-off-by: x <x@y.com>", "Signed-off-by: <@>", "Co-Authored-By: A <a@b.com>"):
            self.commit(f"fix: x\n\n{bad}")
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.count("Falta la firma"), 4)

    def test_merge_commits_are_ignored_and_empty_range_passes(self):
        self.assertEqual(self.check().returncode, 0)                                      # sin commits propios
        self.commit("feat: ok\n\nSigned-off-by: Ana <ana@example.com>")
        git(self.d, "checkout", "-q", "main")
        git(self.d, "merge", "-q", "--no-ff", "-m", "Merge sin firma", "feature")
        git(self.d, "checkout", "-q", "-b", "otra", "main~0")
        self.assertEqual(subprocess.run(["bash", str(SCRIPT), "main~1", "main"], cwd=self.d, capture_output=True, text=True).returncode, 0)

    def test_requires_a_base(self):
        self.assertNotEqual(subprocess.run(["bash", str(SCRIPT)], cwd=self.d, capture_output=True, text=True).returncode, 0)


class GovernanceFilesTests(unittest.TestCase):
    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_files_exist_and_link_each_other(self):
        for rel in ("LICENSE", "NOTICE", "DCO", "CONTRIBUTING.md", "CITATION.cff", ".github/pull_request_template.md",
                    ".github/ISSUE_TEMPLATE/bug_report.md", ".github/ISSUE_TEMPLATE/feature_request.md", ".github/workflows/dco.yml"):
            self.assertTrue((ROOT / rel).exists(), rel)
        c = self.read("CONTRIBUTING.md")
        for needle in ("DCO", "git commit -s", "Signed-off-by", "NOTICE", "Apache", "AGENTS.md", "tools/check_dco.sh", "solo la librería estándar"):
            self.assertIn(needle, c, needle)
        self.assertIn("CONTRIBUTING.md", self.read("README.md"))
        self.assertIn("CONTRIBUTING.md", self.read("AGENTS.md"))
        self.assertIn("CONTRIBUTING.md", self.read(".github/pull_request_template.md"))

    def test_the_dco_is_the_official_text(self):
        t = self.read("DCO")
        self.assertIn("Developer Certificate of Origin", t)
        self.assertIn("Version 1.1", t)
        for point in ("(a)", "(b)", "(c)", "(d)"):
            self.assertIn(point, t)

    def test_the_workflow_runs_the_same_script_on_pull_requests(self):
        w = self.read(".github/workflows/dco.yml")
        self.assertIn("pull_request", w)
        self.assertIn("tools/check_dco.sh", w)
        self.assertIn("fetch-depth: 0", w)                               # sin historial completo no se puede comparar con la base
        self.assertNotIn("secrets.", w)

    def test_license_files_are_declared_in_the_package(self):
        p = self.read("pyproject.toml")
        self.assertIn('license = "Apache-2.0"', p)
        self.assertIn('"LICENSE", "NOTICE"', p)


if __name__ == "__main__":
    unittest.main()
