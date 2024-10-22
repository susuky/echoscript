
import pytest

from echoscript.audio2text import Audio2Text, audio2text

class TestAudio2Text:
    def test_init(self):
        audio2text = Audio2Text(model_name='tiny')
        assert audio2text.model_name == 'tiny'

    def test_available_models(self):
        available_models = Audio2Text.available_models
        assert isinstance(available_models, list)
        assert len(available_models) > 0

    def test_available_languages(self):
        available_languages = Audio2Text.available_languages
        assert isinstance(available_languages, dict)
        assert len(available_languages) > 0
        assert 'en' in available_languages

    def test_is_language_available(self):
        audio2text = Audio2Text(model_name='tiny')
        assert audio2text.is_language_available('english') is True
        assert audio2text.is_language_available('en') is True
        assert audio2text.is_language_available('invalid_language') is False

    def test_transcribe(self):
        filename = 'This_is_an_example.mp3'
        transcribed_text = audio2text(filename, fmt='srt', language='en', model_name='tiny')
        assert isinstance(transcribed_text, str)

        transcribed_text = audio2text(filename, fmt=None, language='en', model_name='tiny')
        assert isinstance(transcribed_text, str)
        assert 'This is an example' in transcribed_text

        transcribed_text = audio2text(filename, fmt='json', language='en', model_name='tiny')
        assert isinstance(transcribed_text, dict)

        with pytest.raises(ValueError) as excinfo:
            audio2text(filename, fmt='srt', language='invalid_language', model_name='tiny')
        assert 'Language `invalid_language` is not available.' in str(excinfo.value)

