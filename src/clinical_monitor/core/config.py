from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from clinical_monitor.domain.vitals import ReferenceRange, VitalSign


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLINICAL_MONITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Clinical Monitor"
    vital_ranges: dict[VitalSign, ReferenceRange] = Field(default_factory=dict)
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
