import io
import wave
import zipfile

import httpx
import numpy as np
import pytest

from clinical_monitor.tools.dataset_downloads import RangeReader
from clinical_monitor.tools.prepare_real_data import save_pcm


def test_remote_zip_reads_only_selected_member():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("selected.txt", "selected content")
        output.writestr("other.bin", np.random.default_rng(42).bytes(100000))
    content = archive.getvalue()
    received = []

    def respond(request):
        interval = request.headers["Range"].removeprefix("bytes=")
        first, last = map(int, interval.split("-"))
        received.append(last - first + 1)
        return httpx.Response(
            206,
            content=content[first : last + 1],
            headers={"Content-Range": f"bytes {first}-{last}/{len(content)}"},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        reader = RangeReader(client, "https://example.org/videos.zip", len(content))
        with zipfile.ZipFile(reader) as remote:
            assert remote.read("selected.txt") == b"selected content"
    assert sum(received) < 1000


def test_range_reader_rejects_full_download_response():
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        reader = RangeReader(client, "https://example.org/videos.zip", 1000000)
        with pytest.raises(ValueError, match="byte range"):
            reader.read(100)


def test_pcm_conversion_preserves_amplitude_and_rejects_clipping(tmp_path):
    path = tmp_path / "sample.wav"
    samples = np.array([0, -0.5, 0.5, 0.1], dtype=np.float64)
    save_pcm(path, samples, 8000)
    with wave.open(str(path)) as audio:
        assert audio.getframerate() == 8000
        assert audio.getsampwidth() == 2
        actual = np.frombuffer(audio.readframes(4), dtype="<i2") / 32767
    np.testing.assert_allclose(actual, samples, atol=1 / 32767)
    with pytest.raises(ValueError, match="PCM range"):
        save_pcm(path, np.array([1.1]), 8000)
