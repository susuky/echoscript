
import whisper
import warnings

from echoscript.utils import segments2srt
from echoscript.utils import classproperty


class Audio2Text:
    '''
    Class for audio transcription using the Whisper model.
    '''

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

    @staticmethod
    def load_whisper_model(model_name='base'):
        '''
        Load the Whisper model.

        Args:
            model_name (str, optional): The name of the Whisper model to load. Defaults to 'base'.

        Returns:
            whisper.Model: The loaded Whisper model.
        '''
        if model_name not in Audio2Text.available_models:
            raise ValueError(f'Whisper model `{model_name}` is not available.')
        
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', FutureWarning)
            model = whisper.load_model(model_name)
        return model
    
    def transcribe(self,
                   audio,
                   model_name: str = 'base',
                   fmt: str = None,
                   language: str = None,
                   **kwargs):
        '''
        Transcribe an audio file using the loaded model.

        Args:
            audio (str | ndarray | Tensor): The audio to transcribe. Can be a file path, bytes of audio data, or a URL.
            fmt (str, optional): The format of the audio, supported formats {`json`, `srt`, `None`}. Defaults to None.
            language (str, optional): The language of the audio, use `None` for multilingual. Defaults to None.
            **kwargs: Additional keyword arguments to pass to the model's transcribe method.

        Returns:
            str: The transcribed text
        '''
        if language is not None and not self.is_language_available(language):
            raise ValueError(f'Language `{language}` is not available.')

        if fmt is not None and fmt not in ['json', 'srt', None]:
            raise ValueError(f'Format `{fmt}` is not supported.')
        
        self.model_name = model_name
        self.model = self.load_whisper_model(model_name)
        result = self.model.transcribe(audio, language=language)
        if fmt == 'srt': return segments2srt(result['segments'])
        if fmt == 'json': return result
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
    return Audio2Text().transcribe(audio, model_name, fmt, language, **kwargs)
