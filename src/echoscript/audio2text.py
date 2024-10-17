
import whisper


class Audio2Text:
    def __init__(self, model_name='base'):
        self.model_name = model_name
        self.model = whisper.load_model(model_name)

    @property
    def available_models(self):
        '''
        A list of all available Whisper models.

        Returns:
            list[str]: A list of all available Whisper models.
        '''
        return whisper.list_models()
    
    @property
    def available_languages(self):
        '''
        A dictionary of all available languages and their corresponding ISO 639-1 code.

        Returns:
            dict[str, str]: A dictionary of all available languages
                - key: Corresponding ISO 639-1 code
                - value: Language name
        '''
        return whisper.tokenizer.LANGUAGES

    def transcribe(self, audio):
        '''
        Transcribe an audio file using the loaded model.

        Args:
            audio (str | ndarray | Tensor): The audio to transcribe. Can be a file path, bytes of audio data, or a URL.

        Returns:
            dict: The result containing the transcription.
        '''
        result = self.model.transcribe(audio)
        return result
    
    