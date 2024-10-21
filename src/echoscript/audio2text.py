
import whisper

from echoscript.utils import segments2srt
from echoscript.utils import classproperty


class Audio2Text:
    def __init__(self, model_name='base'):
        self.model_name = model_name
        self.model = whisper.load_model(model_name)

    @classproperty
    def available_models(self):
        '''
        A list of all available Whisper models.

        Returns:
            list[str]: A list of all available Whisper models.
        '''
        return whisper.available_models()

    @classproperty
    def available_languages(self):
        '''
        A dictionary of all available languages and their corresponding ISO 639-1 code.

        Returns:
            dict[str, str]: A dictionary of all available languages
                - key: Corresponding ISO 639-1 code
                - value: Language name
        '''
        return whisper.tokenizer.LANGUAGES

    def is_language_available(self, language):
        '''
        Check if a language is available in the Whisper model.

        Args:
            language (str): The language to check.

        Returns:
            bool: True if the language is available, False otherwise.
        '''
        available_languages = self.available_languages
        if language in available_languages:
            return True

        if language in available_languages.values():
            return True

        return False

    def transcribe(self,
                   audio,
                   fmt: str = None,
                   language: str = None,
                   **kwargs):
        '''
        Transcribe an audio file using the loaded model.

        Args:
            audio (str | ndarray | Tensor): The audio to transcribe. Can be a file path, bytes of audio data, or a URL.
            fmt (str, optional): The format of the audio, supported formats {`srt`, `None`}. Defaults to None.
            language (str, optional): The language of the audio, use `None` for multilingual. Defaults to None.
            **kwargs: Additional keyword arguments to pass to the model's transcribe method.

        Returns:
            str: The transcribed text
        '''
        if language is not None and not self.is_language_available(language):
            raise ValueError(f'Language `{language}` is not available.')

        result = self.model.transcribe(audio, language=language)
        if fmt == 'srt':
            return segments2srt(result['segments'])
        return result['text']


def audio2text(audio, model_name='base', fmt=None, language=None, **kwargs):
    '''
    Transcribe an audio file using the Whisper model.

    Args:
        audio (str | ndarray | Tensor): The audio to transcribe. Can be a file path, bytes of audio data, or a URL.
        model_name (str, optional): The name of the Whisper model to use. Defaults to 'base'.
        fmt (str, optional): The format of the audio, supported formats {`srt`, `None`}. Defaults to None.
        language (str, optional): The language of the audio, use `None` for multilingual. Defaults to None.
        **kwargs: Additional keyword arguments to pass to the model's transcribe method.

    Returns:
        str: The transcribed text
    '''
    return Audio2Text(model_name=model_name).transcribe(audio, fmt, language, **kwargs)
