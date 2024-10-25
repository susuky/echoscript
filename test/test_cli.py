
import pytest

from click.testing import CliRunner
from unittest.mock import patch, MagicMock

from echoscript.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mocker():
    with patch('echoscript.cli.audio2text') as mock:
        yield mock


def test_cli_without_command(runner):
    result = runner.invoke(cli, [])
    assert result.exit_code == 1
    assert '--help' in result.output


def test_cli_with_command(tmp_path, runner, mocker):
    temp_audio = tmp_path / 'test.wav'
    temp_audio.touch()
    mocker.return_value = 'Transcribed text'
    result = runner.invoke(cli, ['-a', str(temp_audio)])
    assert result.exit_code == 0
    assert 'Transcribed text' in result.output


def test_list_no_options(runner):
    result = runner.invoke(cli, ['list'])
    assert result.exit_code == 1
    assert 'Please specify either --models or --languages' in result.output


def test_list_models(runner):
    result = runner.invoke(cli, ['list', '--models'])
    assert result.exit_code == 0
    assert 'tiny' in result.output
    assert 'turbo' in result.output


def test_list_languages(runner):
    result = runner.invoke(cli, ['list', '--languages'])
    assert result.exit_code == 0
    assert 'en: English' in result.output
    assert 'fr: French' in result.output
    assert 'zh: Chinese' in result.output

