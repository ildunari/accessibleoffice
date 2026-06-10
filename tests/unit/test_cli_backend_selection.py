"""--vlm choices come from the backend registry; --vlm-model overrides the model."""

from click.testing import CliRunner

from a11yfix.cli import main


def test_vlm_choices_come_from_registry():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert "claude-api" in result.output
    assert "anthropic" in result.output
    assert "--vlm-model" in result.output


def test_unknown_vlm_rejected():
    runner = CliRunner()
    result = runner.invoke(main, ["nofile.pptx", "--vlm", "bogus"])
    assert result.exit_code == 2  # click.Choice rejection (fail gate: unknown backend)
