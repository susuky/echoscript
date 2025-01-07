
import gradio as gr

from echoscript import audio2text, Audio2Text
from echoscript.utils import get_yt_audio


class TranscriptionApp:
    def __init__(self):
        self.model_sizes = Audio2Text.available_models
        self.langs = [None] + list(Audio2Text.available_languages.values())
        self.formats = Audio2Text.available_formats

    def create_input_component(self):
        return gr.Textbox(placeholder='Youtube video URL', label='URL')

    def create_options_components(self):
        model_size = gr.Dropdown(choices=self.model_sizes, value='turbo', label='Model')
        lang = gr.Dropdown(choices=self.langs, value='Taiwan', label='Language (Optional)')
        format = gr.Dropdown(choices=self.formats, value=None, label='Format (Optional)')
        return model_size, lang, format

    def create_output_component(self):
        with gr.Column():
            outputs = gr.Textbox(placeholder='Transcription of the video', 
                                 label='Transcription',
                                 show_label=True,
                                 show_copy_button=True,
                                 interactive=True)
        return outputs

    def build_interface(self):
        with gr.Blocks() as demo:
            with gr.Row():
                with gr.Column():
                    with gr.Row():
                        url = self.create_input_component()
                    with gr.Row():
                        model_size, lang, format = self.create_options_components()

                    with gr.Row():
                        mdtext = '''
                        Larger models are more accurate, but slower and require more GPU memory. 
                        |  Size  | Required VRAM | Relative speed |
                        |:------:|:-------------:|:--------------:|
                        |  tiny  | ~1 GB         |      ~10x      |
                        |  base  | ~1 GB         |      ~7x       |
                        | small  | ~2 GB         |      ~4x       |
                        | medium | ~5 GB         |      ~2x       |
                        | large  | ~10 GB        |       1x       |
                        | turbo  | ~6 GB         |      ~8x       |
                        '''
                        gr.Markdown(mdtext)
                        transcribe_btn = gr.Button('Transcribe')

                with gr.Column():
                    outputs = self.create_output_component()

            transcribe_btn.click(self.get_transcript, inputs=[url, model_size, lang, format], outputs=outputs)

        return demo

    def get_transcript(self, url, model_size, lang, format):
        return audio2text(get_yt_audio(url), model_name=model_size, fmt=format, language=lang)

    def launch(self, 
               server_port: int = 7860, 
               server_name: str = '0.0.0.0',
               share2pub: bool = False,
               debug: bool = False):
        demo = self.build_interface()
        demo.launch(debug=debug, 
                    server_port=server_port, 
                    share=share2pub, 
                    server_name=server_name)

if __name__ == "__main__":
    app = TranscriptionApp()
    app.launch(debug=True)
