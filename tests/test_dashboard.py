from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from clinical_monitor.dashboard.client import ApiClient

APP = Path(__file__).parents[1] / "src/clinical_monitor/dashboard/app.py"


def test_mixed_timestamps_and_new_encounter_selection():
    encounters = [{"patient_id": "patient-original", "encounter_id": "encounter-original"}]
    evidence = [
        {
            "event_id": str(index),
            "observed_at": observed,
            "modality": "vital_signs",
            "finding_type": "heart_rate.above_range",
            "quality_status": "valid",
            "measured_value": 30 + index,
            "supporting_measurements": [],
        }
        for index, observed in enumerate(
            [
                "2026-11-07T02:17:00Z",
                "2026-11-07T02:17:17.226594Z",
            ]
        )
    ]

    def get(self, path, **kwargs):
        if path == "/configuration":
            return {"azure_speech_configured": False, "azure_language_configured": False}
        if path == "/encounters":
            return encounters.copy()
        if path.endswith("/evidence"):
            return evidence if "encounter-original" in path else []
        return []

    def post(self, path, **kwargs):
        if path == "/encounters":
            encounters.append(kwargs["json"])
        return kwargs["json"]

    with patch.object(ApiClient, "get", get), patch.object(ApiClient, "post", post):
        app = AppTest.from_file(str(APP)).run(timeout=20)
        assert not app.exception
        assert app.toggle[0].value is False
        app.sidebar.button[0].click().run(timeout=20)
        assert not app.exception
        assert len(encounters) == 2
        assert app.session_state["active_encounter"] == encounters[-1]["encounter_id"]
        assert app.sidebar.selectbox[0].value == encounters[-1]["encounter_id"]
        app.text_area[0].input("Texto ainda não enviado").run(timeout=20)
        app.sidebar.button[1].click().run(timeout=20)
        assert app.text_area[0].value == "Texto ainda não enviado"
        assert not app.exception
        app.toggle[0].set_value(True).run(timeout=20)
        assert not app.exception
        assert app.session_state["active_encounter"] == encounters[-1]["encounter_id"]
