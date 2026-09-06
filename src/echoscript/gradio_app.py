"""Compatibility import for callers of the former web application wrapper."""

from echoscript.web import LocalJobController, create_web_app


class TranscriptionApp:
    def __init__(self, controller: LocalJobController | None = None):
        self.controller = controller or LocalJobController()

    def build_interface(self):
        return create_web_app(self.controller)

    def launch(self, server_port: int = 7860, server_name: str = "0.0.0.0", share2pub: bool = False, debug: bool = False):
        import uvicorn

        if share2pub:
            raise ValueError("Public sharing tunnels are not supported by this application")
        return uvicorn.run(
            self.build_interface(), host=server_name, port=server_port,
            log_level="debug" if debug else "info",
        )
