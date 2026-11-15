import io
from pathlib import Path

import httpx


class RangeReader(io.RawIOBase):
    def __init__(self, client: httpx.Client, url: str, size: int):
        self.client, self.url, self.size, self.position = client, url, size, 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset + (0 if whence == 0 else self.position if whence == 1 else self.size)
        if position < 0 or whence not in {0, 1, 2}:
            raise ValueError("Invalid seek")
        self.position = position
        return position

    def read(self, size=-1):
        size = self.size - self.position if size < 0 else min(size, self.size - self.position)
        if size <= 0:
            return b""
        if size > 128 * 1024 * 1024:
            raise ValueError("Requested archive member exceeds 128 MB")
        end = self.position + size - 1
        with self.client.stream(
            "GET", self.url, headers={"Range": f"bytes={self.position}-{end}"}
        ) as response:
            expected = f"bytes {self.position}-{end}/{self.size}"
            if response.status_code != 206 or response.headers.get("content-range") != expected:
                raise ValueError("Server did not honor the requested byte range")
            content = response.read()
        if len(content) != size:
            raise ValueError("Incomplete archive range")
        self.position += size
        return content


def download(client: httpx.Client, url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        return destination
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
