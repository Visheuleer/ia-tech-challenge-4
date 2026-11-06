from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, StringConstraints


def normalize_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(normalize_utc)]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
