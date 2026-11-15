import os

import httpx

from clinical_monitor.core.config import Settings


class ApiClient:
    def __init__(self):
        self.base_url = os.environ.get("CLINICAL_MONITOR_API_URL", "http://127.0.0.1:8000").rstrip(
            "/"
        )
        token = Settings().api_token
        self.headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
        self._client = httpx.Client(timeout=30.0)

    def request(self, method: str, path: str, **kwargs):
        response = self._client.request(
            method, self.base_url + "/v1" + path, headers=self.headers, **kwargs
        )
        if not response.is_success:
            try:
                detail = response.json().get("detail", "Falha na solicitação")
            except ValueError:
                detail = "Serviço indisponível"
            raise RuntimeError(f"{response.status_code}: {detail}")
        return response

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs).json()

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs).json()
