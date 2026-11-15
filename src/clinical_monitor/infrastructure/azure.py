import io
import wave

import httpx
import numpy as np


class IntegrationUnavailable(Exception):
    pass


class AzureClient:
    def __init__(self, settings, client: httpx.Client | None = None):
        self._settings = settings
        self._client = client

    def _post(self, url: str, key: str, **kwargs) -> dict:
        headers = kwargs.pop("headers", {})
        headers["Ocp-Apim-Subscription-Key"] = key
        try:
            if self._client is not None:
                response = self._client.post(url, headers=headers, **kwargs)
            else:
                with httpx.Client(timeout=60.0, follow_redirects=False) as client:
                    response = client.post(url, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            code = getattr(getattr(exc, "response", None), "status_code", "network")
            raise IntegrationUnavailable(f"Azure request failed ({code})") from exc

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> str:
        settings = self._settings
        if not settings.azure_speech_endpoint or not settings.azure_speech_key:
            raise IntegrationUnavailable("Azure Speech is not configured")
        url = (
            settings.azure_speech_endpoint.rstrip("/")
            + "/stt/speech/recognition/conversation/cognitiveservices/v1"
        )
        texts = []
        size = sample_rate * 50
        for offset in range(0, len(samples), size):
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(
                    (np.clip(samples[offset : offset + size], -1, 1) * 32767)
                    .astype("<i2")
                    .tobytes()
                )
            result = self._post(
                url,
                settings.azure_speech_key.get_secret_value(),
                params={"language": settings.speech_language, "format": "simple"},
                headers={"Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000"},
                content=buffer.getvalue(),
            )
            if result.get("RecognitionStatus") == "Success":
                texts.append(result.get("DisplayText", ""))
            elif result.get("RecognitionStatus") not in {"NoMatch", "InitialSilenceTimeout"}:
                raise IntegrationUnavailable("Azure Speech returned an unsuccessful recognition")
        transcript = " ".join(texts).strip()
        if not transcript:
            raise IntegrationUnavailable("Azure Speech returned no recognized speech")
        return transcript

    def analyze_text(self, text: str) -> dict:
        settings = self._settings
        if not settings.azure_language_endpoint or not settings.azure_language_key:
            raise IntegrationUnavailable("Azure Language is not configured")
        documents = [{"id": "1", "language": settings.text_language, "text": text}]
        output = {}
        for kind, name in [("SentimentAnalysis", "sentiment"), ("KeyPhraseExtraction", "phrases")]:
            result = self._post(
                settings.azure_language_endpoint.rstrip("/") + "/language/:analyze-text",
                settings.azure_language_key.get_secret_value(),
                params={"api-version": "2023-04-01"},
                json={
                    "kind": kind,
                    "analysisInput": {"documents": documents},
                    "parameters": {"modelVersion": "latest"},
                },
            )
            results = result.get("results", {})
            if results.get("errors") or not results.get("documents"):
                raise IntegrationUnavailable(f"Azure Language failed {name} analysis")
            output[name] = results["documents"][0]
        return output
