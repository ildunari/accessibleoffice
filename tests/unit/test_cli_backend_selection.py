"""--vlm choices come from the backend registry; --vlm-model overrides the model."""

from click.testing import CliRunner

from a11yfix.ai.registry import backend_names
from a11yfix.cli import main


def test_vlm_choices_come_from_registry():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "--vlm" in result.output
    # Every registered backend name must appear in the rendered choice list
    # (help text wraps, so normalize whitespace before matching).
    rendered = "".join(result.output.split())
    for name in backend_names():
        assert name in rendered
    assert "--vlm-model" in result.output


def test_unknown_vlm_rejected():
    runner = CliRunner()
    result = runner.invoke(main, ["nofile.pptx", "--vlm", "bogus"])
    assert result.exit_code == 2  # click.Choice rejection (fail gate: unknown backend)
    assert "Invalid value for '--vlm'" in result.output


def test_agent_option_in_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    out = " ".join(result.output.split())
    assert "--agent [claude|codex]" in out
