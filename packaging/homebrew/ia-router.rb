# Homebrew formula for ia-router. It goes in the tap repo: github.com/Mgobeaalcoba/homebrew-tap  ->  Formula/ia-router.rb
# User installation:   brew install Mgobeaalcoba/tap/ia-router
#
# It is completed AFTER publishing the version on PyPI: replace `url` and `sha256` with those of the source archive (sdist).
#   sha256:  curl -sL <url> | shasum -a 256        (or the one shown at https://pypi.org/project/ia-router/#files)
class IaRouter < Formula
  include Language::Python::Virtualenv

  desc "Routes your tasks across the official AI CLIs using objective metrics from Arena and Artificial Analysis"
  homepage "https://www.mgatc.com/recursos/ia-router/"
  url "https://files.pythonhosted.org/packages/source/i/ia-router/ia_router-0.2.0.tar.gz"
  sha256 "REPLACE_WITH_THE_SDIST_SHA256"
  license "Apache-2.0"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources # no Python dependencies: standard library only
  end

  def caveats
    <<~EOS
      ia-router routes to the official CLIs you already have installed and logged in (claude, codex, agy).
      To add speed and cost, create your free Artificial Analysis key and save it in:
        ~/.ia-router/.env      (ARTIFICIAL_ANALYSIS_API_KEY=your_key)
    EOS
  end

  test do
    ENV["ROUTER_HOME"] = testpath.to_s
    assert_match version.to_s, shell_output("#{bin}/ia-router --version")
    assert_match "usage: ia-router", shell_output("#{bin}/ia-router --help")
  end
end
