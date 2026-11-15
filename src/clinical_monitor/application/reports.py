import html
import json
from datetime import UTC, datetime
from uuid import UUID

from clinical_monitor.application.fusion import FusionService
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.application.jobs import JobService


def build_report(
    encounter_id: UUID, ingestion: IngestionService, fusion: FusionService, jobs: JobService
) -> dict:
    encounter = ingestion.get_encounter(encounter_id)
    fusion.refresh(encounter_id)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "encounter": encounter.model_dump(mode="json"),
        "evidence": [
            item.model_dump(mode="json") for item in ingestion.list_evidence(encounter_id)
        ],
        "alerts": [item.model_dump(mode="json") for item in fusion.list(encounter_id)],
        "jobs": [item.model_dump(mode="json") for item in jobs.list(encounter_id)],
        "limitations": [
            "Protótipo educacional de apoio à revisão; sem validação clínica.",
            "Regras configuráveis e sinais acústicos/posturais não constituem diagnóstico.",
            "Ausência de evidência ou falha de processamento não significa ausência de risco.",
        ],
    }


def report_html(report: dict) -> str:
    encounter = report["encounter"]
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(str(item.get(field, '')))}</td>"
            for field in ["observed_at", "modality", "finding_type", "quality_status"]
        )
        + "</tr>"
        for item in report["evidence"]
    )
    alerts = "".join(
        f"<li><strong>{html.escape(item['severity'])} · {html.escape(item['status'])}</strong>"
        f" — {html.escape(item['reason'])}</li>"
        for item in report["alerts"]
    )
    details = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    return f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<title>Relatório de monitoramento</title>
<style>body{{font:16px system-ui;margin:40px auto;max-width:1100px;color:#172b4d}}
table{{width:100%;border-collapse:collapse}}
td,th{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7fb;padding:20px}}
h1,h2{{color:#135b72}}@media print{{details{{display:block}}}}</style>
<h1>Relatório de monitoramento</h1>
<p>Atendimento: {encounter["encounter_id"]}<br>Paciente: {encounter["patient_id"]}</p>
<p>Gerado em {report["generated_at"]}</p><h2>Alertas</h2>
<ul>{alerts or "<li>Nenhum alerta gerado.</li>"}</ul>
<h2>Evidências</h2><table><thead><tr><th>Horário</th><th>Modalidade</th><th>Resultado</th><th>Qualidade</th></tr></thead>
<tbody>{rows}</tbody></table><h2>Limitações</h2><ul>
{"".join("<li>" + html.escape(item) + "</li>" for item in report["limitations"])}</ul>
<details><summary>Detalhes e rastreabilidade</summary><pre>{details}</pre></details></html>"""
