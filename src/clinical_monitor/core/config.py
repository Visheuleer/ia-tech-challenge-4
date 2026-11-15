from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from clinical_monitor.domain.vital_series import TemporalRule
from clinical_monitor.domain.vitals import ReferenceRange, VitalSign


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLINICAL_MONITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Clinical Monitor"
    data_dir: Path = Path("data")
    redis_url: str = "redis://127.0.0.1:6379/0"
    api_token: SecretStr | None = None
    azure_speech_endpoint: str = ""
    azure_speech_key: SecretStr | None = None
    azure_language_endpoint: str = ""
    azure_language_key: SecretStr | None = None
    speech_language: str = "pt-BR"
    text_language: str = "pt"
    max_upload_mb: int = Field(default=50, ge=1, le=500)
    max_media_seconds: float = Field(default=300.0, gt=0, le=3600)
    audio_pause_seconds: float = Field(default=2.0, gt=0)
    video_sample_fps: float = Field(default=2.0, gt=0, le=10)
    video_tilt_degrees: float = Field(default=25.0, gt=0, lt=90)
    video_asymmetry_degrees: float = Field(default=20.0, gt=0, lt=180)
    video_stillness_speed: float = Field(default=0.05, gt=0)
    video_stillness_seconds: float = Field(default=10.0, gt=0)
    pose_model_path: str = "models/yolov8n-pose.pt"
    prescription_relative_change: float = Field(default=0.5, gt=0)
    fusion_window_seconds: int = Field(default=60, ge=5, le=3600)
    task_lease_seconds: int = Field(default=1800, ge=60)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    vital_ranges: dict[VitalSign, ReferenceRange] = Field(default_factory=dict)
    vital_temporal_rules: dict[VitalSign, TemporalRule] = Field(default_factory=dict)
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://clinical_monitor:clinical_monitor_local@localhost:5432/clinical_monitor"
    )

    @field_validator("vital_ranges")
    @classmethod
    def validate_vital_ranges(
        cls, ranges: dict[VitalSign, ReferenceRange]
    ) -> dict[VitalSign, ReferenceRange]:
        oxygen_range = ranges.get(VitalSign.OXYGEN_SATURATION)
        if oxygen_range is not None and oxygen_range.upper > 100:
            raise ValueError("Oxygen saturation range must not exceed 100 percent")
        return ranges
