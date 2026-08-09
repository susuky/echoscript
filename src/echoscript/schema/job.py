from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class JobOptions:
    asr_backend: str = "qwen"
    asr_model: str = "Qwen/Qwen3-ASR-1.7B"
    language: str | None = None
    timestamps: bool = True
    diarize: bool = False
    min_speakers: int | None = None
    max_speakers: int | None = None
    zh_script: str | None = "tw"
    context: str = ""
    output_formats: list[str] = field(default_factory=lambda: ["json", "txt", "srt", "vtt"])
    compute_type: str = "float16"
    device: str = "cuda"

    def __post_init__(self) -> None:
        if self.asr_backend not in {"qwen", "faster-whisper", "faster_whisper", "whisper"}:
            raise ValueError(f"Unsupported ASR backend: {self.asr_backend}")
        invalid = set(self.output_formats) - {"json", "txt", "srt", "vtt"}
        if invalid:
            raise ValueError(f"Unsupported output formats: {sorted(invalid)}")
        if self.diarize and not self.timestamps:
            raise ValueError("Speaker diarization requires timestamps for speaker attribution")
        if self.min_speakers is not None and self.min_speakers < 1:
            raise ValueError("min_speakers must be >= 1")
        if self.max_speakers is not None and self.max_speakers < 1:
            raise ValueError("max_speakers must be >= 1")
        if self.min_speakers and self.max_speakers and self.min_speakers > self.max_speakers:
            raise ValueError("min_speakers cannot exceed max_speakers")
        if self.zh_script not in {None, "tw", "twp"}:
            raise ValueError("zh_script must be one of: tw, twp, None")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JobOptions":
        if not data:
            return cls()
        known = {field.name for field in cls.__dataclass_fields__.values()}
        values = {k: v for k, v in data.items() if k in known}
        backend = values.get("asr_backend", "qwen")
        if "asr_model" not in values:
            values["asr_model"] = (
                "large-v3-turbo"
                if backend in {"faster-whisper", "faster_whisper", "whisper"}
                else "Qwen/Qwen3-ASR-1.7B"
            )
        return cls(**values)
