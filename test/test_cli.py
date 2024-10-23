
import pytest
from click.testing import CliRunner
from echoscript.cli import cli

@pytest.fixture
def runner():
    return CliRunner()


def test_cli_without_command(runner):
    result = runner.invoke(cli, [])
    assert result.exit_code == 1
    assert '--help' in result.output


def test_list_models(runner, mocker):
    mocker.patch('echoscript.Audio2Text.available_models', ['tiny', 'turbo', 'base'])
    result = runner.invoke(cli, ['list', '--models'])
    assert result.exit_code == 0
    assert 'tiny' in result.output
    assert 'turbo' in result.output


def test_list_languages(runner, mocker):
    mocker.patch('echoscript.Audio2Text.available_languages', {'en': 'English', 'fr': 'French'})
    result = runner.invoke(cli, ['list', '--languages'])
    assert result.exit_code == 0
    assert 'en: English' in result.output
    assert 'fr: French' in result.output
