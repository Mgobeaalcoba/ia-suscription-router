import os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("ROUTER_HOME", tempfile.mkdtemp())

from ia_router import envfile  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_syntax(self):
        text = """
# comentario
ARTIFICIAL_ANALYSIS_API_KEY=abc123
export OTHER="with quotes"
SIMPLE='single quotes'
WITH_COMMENT=value # this is ignored
VACIA=
  SPACES  =  trimmed
no_equals
1INVALID=x
WITH-DASH=x
URL=https://x.io/a?b=c=d
"""
        self.assertEqual(envfile.parse(text), {"ARTIFICIAL_ANALYSIS_API_KEY": "abc123", "OTHER": "with quotes", "SIMPLE": "single quotes",
                                               "WITH_COMMENT": "value", "SPACES": "trimmed", "URL": "https://x.io/a?b=c=d"})

    def test_empty_and_garbage(self):
        self.assertEqual(envfile.parse(""), {})
        self.assertEqual(envfile.parse("# comments only\n\n"), {})


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = dict(os.environ)
        for k in ("T_ENV_A", "T_ENV_B", "T_ENV_C"):
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        self.tmp.cleanup()

    def write(self, name, text):
        p = Path(self.tmp.name) / name
        p.write_text(text)
        return p

    def test_loads_into_the_environment_without_overriding(self):
        os.environ["T_ENV_A"] = "from the environment"
        added = envfile.load([self.write(".env", "T_ENV_A=from the file\nT_ENV_B=new\n")])
        self.assertEqual(os.environ["T_ENV_A"], "from the environment")           # what is already defined wins
        self.assertEqual(os.environ["T_ENV_B"], "new")
        self.assertEqual(added, {"T_ENV_B": "new"})

    def test_first_file_wins_and_missing_files_are_ignored(self):
        a, b = self.write("a.env", "T_ENV_C=primero\n"), self.write("b.env", "T_ENV_C=segundo\n")
        envfile.load([Path(self.tmp.name) / "no-existe.env", a, b])
        self.assertEqual(os.environ["T_ENV_C"], "primero")

    def test_default_locations_are_the_repo_env_and_the_state_folder(self):
        from unittest import mock
        repo, home = self.write("repo.env", "T_ENV_A=from the repo\n"), Path(self.tmp.name) / "home"
        home.mkdir()
        (home / ".env").write_text("T_ENV_B=from the state\nT_ENV_A=ignored\n")
        os.environ["ROUTER_HOME"] = str(home)
        with mock.patch.object(envfile, "REPO_ENV", repo):
            envfile.load()
        self.assertEqual((os.environ["T_ENV_A"], os.environ["T_ENV_B"]), ("from the repo", "from the state"))

    def test_the_cli_loads_the_env_file_before_anything_else(self):
        from unittest import mock
        import cli
        from ia_router import metrics
        repo = self.write("repo.env", "ARTIFICIAL_ANALYSIS_API_KEY=CLAVE_DE_PRUEBA\n")
        os.environ.pop("ARTIFICIAL_ANALYSIS_API_KEY", None)
        os.environ["ROUTER_HOME"] = self.tmp.name
        with mock.patch.object(envfile, "REPO_ENV", repo), mock.patch.object(sys, "argv", ["cli.py", "stats"]):
            cli.main()
        self.assertEqual(metrics.aa_key(), "CLAVE_DE_PRUEBA")


class VersionTests(unittest.TestCase):
    def test_version_flag_matches_the_package(self):
        from ia_router import __version__
        out = subprocess.run([sys.executable, str(ROOT / "cli.py"), "--version"], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), f"ia-router {__version__}")
        out = subprocess.run([sys.executable, "-m", "ia_router", "--version"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.stdout.strip(), f"ia-router {__version__}")

    def test_the_version_is_in_sync_with_citation_cff(self):
        from ia_router import __version__
        self.assertIn(f'version: "{__version__}"', (ROOT / "CITATION.cff").read_text())


class RepoHygieneTests(unittest.TestCase):
    def test_env_is_ignored_by_git_and_the_example_has_no_real_key(self):
        self.assertIn(".env", (ROOT / ".gitignore").read_text().splitlines())
        example = (ROOT / ".env.example").read_text()
        self.assertIn("ARTIFICIAL_ANALYSIS_API_KEY=", example)
        self.assertEqual(envfile.parse(example), {})                      # the example carries no key: all variables empty or commented out
        self.assertIn("artificialanalysis.ai", example)

    def test_the_real_env_file_is_not_tracked(self):
        out = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True)
        if out.returncode == 0:
            self.assertEqual(out.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
