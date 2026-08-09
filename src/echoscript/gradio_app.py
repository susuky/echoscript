"""Compatibility wrapper around the local Gradio application."""

from echoscript.web import LocalJobController, create_web_app


class TranscriptionApp:
    def __init__(self, controller: LocalJobController | None = None):
        self.controller = controller or LocalJobController()

    def build_interface(self):
        return create_web_app(self.controller)

    def launch(self, server_port: int = 7860, server_name: str = "0.0.0.0", share2pub: bool = False, debug: bool = False):
        self.controller.start()
        try:
            app = self.build_interface()
            return app.launch(
                server_port=server_port,
                server_name=server_name,
                share=share2pub,
                debug=debug,
                footer_links=[],
            )
        finally:
            self.controller.stop()
