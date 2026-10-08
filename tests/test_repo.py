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
        self.commit("feat: something\n\nSigned-off-by: Ana Perez <ana@example.com>")
        git(self.d, "commit", "-q", "--allow-empty", "-s", "-m", "fix: otro")             # signed off with `git commit -s`
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("signed off", r.stdout)

    def test_an_unsigned_commit_fails_and_is_named(self):
        self.commit("feat: signed\n\nSigned-off-by: Ana <ana@example.com>")
        self.commit("fix: unsigned")
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn("unsigned", r.stdout)
        self.assertNotIn("feat: signed", r.stdout)
        self.assertIn("git commit -s", r.stdout)

    def test_malformed_signoffs_do_not_count(self):
        for bad in ("Signed-off-by: no email", "signed-off-by: x <x@y.com>", "Signed-off-by: <@>", "Co-Authored-By: A <a@b.com>"):
            self.commit(f"fix: x\n\n{bad}")
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.count("Missing DCO sign-off"), 4)

    def test_merge_commits_are_ignored_and_empty_range_passes(self):
        self.assertEqual(self.check().returncode, 0)                                      # no commits of its own
        self.commit("feat: ok\n\nSigned-off-by: Ana <ana@example.com>")
        git(self.d, "checkout", "-q", "main")
        git(self.d, "merge", "-q", "--no-ff", "-m", "Merge without sign-off", "feature")
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
        for needle in ("DCO", "git commit -s", "Signed-off-by", "NOTICE", "Apache", "AGENTS.md", "tools/check_dco.sh", "standard library"):
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
        self.assertIn("fetch-depth: 0", w)                               # without the full history it cannot be compared with the base
        self.assertNotIn("secrets.", w)

    def test_license_files_are_declared_in_the_package(self):
        p = self.read("pyproject.toml")
        self.assertIn('license = "Apache-2.0"', p)
        self.assertIn('"LICENSE", "NOTICE"', p)


class ConnectedDocsTests(unittest.TestCase):
    """The README is also the PyPI page: relative paths do not resolve there, and the project URLs must match everywhere."""
    WEB = "https://www.mgatc.com/recursos/ia-router/"
    PYPI = "https://pypi.org/project/ia-router/"
    REPO = "https://github.com/Mgobeaalcoba/ia-suscription-router"
    TAP = "https://github.com/Mgobeaalcoba/homebrew-tap"

    def read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_readme_links_and_images_are_absolute(self):
        import re
        readme = self.read("README.md")
        targets = re.findall(r"\]\(([^)\s]+)", readme)
        self.assertTrue(targets)
        relative = [t for t in targets if not re.match(r"(https?://|#|mailto:)", t)]
        self.assertEqual(relative, [], "PyPI does not resolve relative paths: use absolute URLs")

    def test_readme_points_to_every_place_the_project_lives(self):
        readme = self.read("README.md")
        for url in (self.WEB, self.PYPI, self.REPO, self.TAP):
            self.assertIn(url, readme, url)
        self.assertIn("brew install Mgobeaalcoba/tap/ia-router", readme)
        self.assertIn("pipx install ia-router", readme)

    def test_package_metadata_urls(self):
        p = self.read("pyproject.toml")
        for key, url in (("Homepage", self.WEB), ("Source", self.REPO), ("Issues", self.REPO + "/issues"), ("Homebrew", self.TAP)):
            self.assertIn(f'{key} = "{url}"', p, key)
        for key in ("Documentation", "Changelog"):
            self.assertIn(f"{key} = ", p)
        self.assertIn(f'repository-code: "{self.REPO}"', self.read("CITATION.cff"))

    def test_other_docs_link_to_pypi_and_homebrew(self):
        for rel in ("docs/USAGE.md", "CONTRIBUTING.md"):
            text = self.read(rel)
            for url in (self.WEB, self.PYPI, self.TAP):
                self.assertIn(url, text, f"{rel}: {url}")

    def test_changelog_has_the_current_version(self):
        sys.path.insert(0, str(ROOT))
        import ia_router
        self.assertIn(f"## {ia_router.__version__} ", self.read("CHANGELOG.md"))

    def test_release_script_requires_changelog(self):
        self.assertIn("CHANGELOG.md", self.read("tools/release.sh"))


class LanguageTests(unittest.TestCase):
    """The project is English-only. The one exception is the task classifier, which deliberately recognizes Spanish keywords."""
    SPANISH = "áéíóúñÁÉÍÓÚÑ¿¡"
    ALLOWED = {"ia_router/router.py", "ia_router/chat.py", "ia_router/connectors.py"}   # classifier patterns, chat intent patterns and connector keywords
    # words that only appear in Spanish prose (no accents needed): a guard for text written without accents
    WORDS = ("tarea", "modelo", "métricas", "instalá", "ejecutá", "abrilo", "guía", "elegí", "cuota", "archivo", "carpeta")

    def files(self):
        import subprocess
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        exts = (".py", ".md", ".sh", ".yml", ".toml", ".cff", ".rb", ".json", ".example")
        skip = ("tests/", "ia_router/data/arena.json", "LICENSE", "docs/USO.md")
        return [f for f in out if f.endswith(exts) and not f.startswith(skip) and (ROOT / f).exists()]

    def test_no_spanish_characters_outside_the_classifier(self):
        bad = []
        for rel in self.files():
            if rel in self.ALLOWED:
                continue
            text = (ROOT / rel).read_text(encoding="utf-8")
            bad += [f"{rel}:{n}: {line.strip()[:80]}" for n, line in enumerate(text.splitlines(), 1)
                    if any(c in line for c in self.SPANISH) and "`sí`" not in line]   # the changelog documents the accepted answers
        self.assertEqual(bad, [], "Spanish text found: translate it (only the classifier may keep Spanish keywords)")

    def test_no_unaccented_spanish_words_in_user_facing_files(self):
        import re
        pat = re.compile(r"\b(" + "|".join(self.WORDS) + r")\b", re.I)
        bad = []
        for rel in self.files():
            if rel in self.ALLOWED or rel.startswith("ia_router/data/models.json"):
                continue
            for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line) and "Spanish" not in line:
                    bad.append(f"{rel}:{n}: {line.strip()[:80]}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
