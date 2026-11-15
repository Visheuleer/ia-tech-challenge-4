import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pandas as pd
import streamlit as st

from clinical_monitor.dashboard.client import ApiClient

st.set_page_config(page_title="Clinical Monitor", page_icon="🩺", layout="wide")


@st.cache_resource
def api_client():
    return ApiClient()


api = api_client()

SIGNALS = {
    "Frequência cardíaca": ("heart_rate", "bpm"),
    "Oxigenação": ("oxygen_saturation", "%"),
    "Pressão sistólica": ("systolic_pressure", "mmHg"),
    "Pressão diastólica": ("diastolic_pressure", "mmHg"),
}
STATES = {
    "open": "Aberto",
    "acknowledged": "Reconhecido",
    "closed": "Encerrado",
    "queued": "Na fila",
    "running": "Processando",
    "completed": "Concluído",
    "partial": "Parcial",
    "failed": "Falhou",
}
MODALITIES = {
    "audio": "Áudio",
    "video": "Vídeo",
    "text": "Texto",
    "vital_signs": "Sinais vitais",
    "prescription": "Prescrições",
}

CONCEPTS = {
    "breathing": "dificuldade respiratória",
    "chest_pain": "dor no peito",
    "fatigue": "cansaço",
    "weakness": "fraqueza",
    "dizziness": "tontura",
    "fainting": "desmaio",
}


FINDINGS = {
    "within_range": "Dentro da faixa configurada",
    "above_range": "Acima da faixa configurada",
    "below_range": "Abaixo da faixa configurada",
    "sustained_deviation": "Desvio persistente",
    "critical_terms": "Termos críticos identificados",
    "language_context": "Contexto textual analisado",
    "dose_change": "Alteração de dose",
    "prolonged_pauses": "Pausas prolongadas",
    "posture_deviation": "Desvio postural",
    "prolonged_stillness": "Imobilidade prolongada",
    "voice_change": "Alteração vocal — triagem experimental",
    "exercise_deviation": "Execução do exercício fora do padrão aprendido",
    "new_symptoms": "Novos sintomas mencionados na evolução",
    "documentation_change": "Comparação entre evoluções concluída",
    "cloud_critical_terms": "Termos críticos confirmados nas expressões extraídas pelo Azure",
    "conversation_pauses_context": "Pausas de conversação — apenas contexto",
    "no_detected_deviation": "Nenhum desvio identificado pelo detector",
}


def finding_label(value):
    return FINDINGS.get(value.split(".")[-1], value)


def show_error(exc):
    st.error(f"Não foi possível concluir: {exc}")


def context(encounter):
    return {"patient_id": encounter["patient_id"], "encounter_id": encounter["encounter_id"]}


@st.fragment(run_every="10s" if st.session_state.get("auto_refresh", False) else None)
def monitor(encounter):
    try:
        evidence = api.get(f"/encounters/{encounter['encounter_id']}/evidence")
        alerts = api.get("/alerts", params={"encounter_id": encounter["encounter_id"]})
        jobs = api.get("/jobs", params={"encounter_id": encounter["encounter_id"]})
    except Exception as exc:
        show_error(exc)
        return
    active = [alert for alert in alerts if alert["status"] != "closed"]
    columns = st.columns(4)
    columns[0].metric("Alertas em acompanhamento", len(active))
    columns[1].metric("Evidências analisadas", len(evidence))
    columns[2].metric("Modalidades presentes", len({item["modality"] for item in evidence}))
    columns[3].metric(
        "Processamentos parciais", sum(job["status"] in {"partial", "failed"} for job in jobs)
    )
    st.subheader("Alertas da equipe")
    if not alerts:
        st.info(
            "Ainda não há alertas neste atendimento. "
            "Ausência de alerta não confirma ausência de risco."
        )
    for alert in alerts:
        label = "Alta" if alert["severity"] == "high" else "Moderada"
        with st.container(border=True):
            st.markdown(f"**Prioridade {label} · {STATES[alert['status']]}**")
            st.write("Revisar as alterações identificadas neste intervalo.")
            st.write(
                " · ".join(
                    dict.fromkeys(
                        finding_label(item["finding_type"])
                        for item in evidence
                        if item["event_id"] in alert["evidence_ids"]
                    )
                )
            )
            st.caption("Fontes: " + ", ".join(MODALITIES[item] for item in alert["modalities"]))
            if alert["missing_modalities"]:
                st.caption(
                    "Sem evidência nesta janela: "
                    + ", ".join(MODALITIES[item] for item in alert["missing_modalities"])
                )
            with st.expander("Ver evidências e histórico"):
                st.json({"evidence_ids": alert["evidence_ids"], "history": alert["history"]})
            if alert["status"] != "closed":
                action = "acknowledged" if alert["status"] == "open" else "closed"
                with st.form(f"action-{alert['alert_id']}"):
                    actor = st.text_input("Responsável", key=f"actor-{alert['alert_id']}")
                    note = st.text_input("Observação da revisão", key=f"note-{alert['alert_id']}")
                    if st.form_submit_button(
                        "Reconhecer" if action == "acknowledged" else "Encerrar"
                    ):
                        try:
                            api.post(
                                f"/alerts/{alert['alert_id']}/actions",
                                json={"status": action, "actor": actor, "note": note},
                            )
                            st.rerun(scope="fragment")
                        except Exception as exc:
                            show_error(exc)
    st.subheader("Linha do tempo")
    if evidence:
        table = pd.DataFrame(
            [
                {
                    "Horário": item["observed_at"],
                    "Modalidade": MODALITIES[item["modality"]],
                    "Resultado": finding_label(item["finding_type"]),
                    "Qualidade": {
                        "valid": "Válida",
                        "degraded": "Limitada",
                        "unusable": "Não utilizável",
                    }[item["quality_status"]],
                }
                for item in evidence
            ]
        )
        st.dataframe(table, hide_index=True, width="stretch")
        measurements = []
        for item in evidence:
            if item["modality"] == "vital_signs":
                samples = item.get("supporting_measurements") or (
                    [{"observed_at": item["observed_at"], "measured_value": item["measured_value"]}]
                    if item["measured_value"] is not None
                    else []
                )
                measurements += [
                    {
                        "Horário": sample["observed_at"],
                        "Valor": sample["measured_value"],
                        "Sinal": item["finding_type"].split(".")[0],
                    }
                    for sample in samples
                ]
        if measurements:
            data = pd.DataFrame(measurements).drop_duplicates()
            data["Horário"] = pd.to_datetime(data["Horário"], format="ISO8601", utc=True)
            for signal, group in data.groupby("Sinal"):
                st.caption(signal)
                st.line_chart(group.set_index("Horário")["Valor"])
        with st.expander("Detalhes das análises"):
            st.json(evidence)


def clinical_data(encounter):
    st.subheader("Registrar uma medição")
    with st.form("measurement"):
        label = st.selectbox("Sinal", list(SIGNALS))
        value = st.number_input("Valor medido", min_value=0.0, value=75.0)
        quality = st.selectbox(
            "Qualidade",
            ["valid", "degraded", "unusable"],
            format_func=lambda item: {
                "valid": "Válida",
                "degraded": "Limitada",
                "unusable": "Não utilizável",
            }[item],
        )
        if st.form_submit_button("Analisar medição"):
            signal, unit = SIGNALS[label]
            try:
                result = api.post(
                    "/vital-signs/analyze",
                    json={
                        **context(encounter),
                        "source_id": str(uuid4()),
                        "signal": signal,
                        "value": value,
                        "unit": unit,
                        "quality_status": quality,
                        "observed_at": datetime.now(UTC).isoformat(),
                    },
                )
                st.success(finding_label(result["finding_type"]))
            except Exception as exc:
                show_error(exc)
    st.subheader("Analisar uma série temporal")
    uploaded = st.file_uploader("Série de medições em JSON", type=["json"], key="series")
    if uploaded is not None and st.button("Analisar série"):
        try:
            payload = json.loads(uploaded.getvalue())
            payload["series_id"] = str(uuid4())
            for sample in payload["measurements"]:
                sample.update(context(encounter))
            result = api.post("/vital-signs/analyze-series", json=payload)
            st.success(finding_label(result["finding_type"]))
        except Exception as exc:
            show_error(exc)
    st.subheader("Comparar prescrições")
    with st.form("prescription"):
        medication = st.text_input("Medicamento", value="Medicamento demonstrativo")
        unit = st.selectbox("Unidade", ["mg", "g", "mcg", "mL", "IU"])
        before, after = st.columns(2)
        old_dose = before.number_input("Dose anterior", min_value=0.001, value=10.0)
        new_dose = after.number_input("Dose atual", min_value=0.001, value=20.0)
        old_frequency = before.number_input(
            "Administrações/dia anteriores", min_value=0.1, max_value=24.0, value=1.0
        )
        new_frequency = after.number_input(
            "Administrações/dia atuais", min_value=0.1, max_value=24.0, value=1.0
        )
        if st.form_submit_button("Comparar alteração"):
            now = datetime.now(UTC)
            base = {**context(encounter), "medication": medication, "unit": unit, "route": "oral"}
            try:
                result = api.post(
                    "/prescriptions/analyze",
                    json={
                        "previous": {
                            **base,
                            "source_id": str(uuid4()),
                            "dose": old_dose,
                            "administrations_per_day": old_frequency,
                            "observed_at": (now - timedelta(seconds=1)).isoformat(),
                        },
                        "current": {
                            **base,
                            "source_id": str(uuid4()),
                            "dose": new_dose,
                            "administrations_per_day": new_frequency,
                            "observed_at": now.isoformat(),
                        },
                    },
                )
                st.success(finding_label(result["finding_type"]))
            except Exception as exc:
                show_error(exc)


@st.fragment(run_every="10s" if st.session_state.get("auto_refresh", False) else None)
def processing_list(encounter):
    try:
        jobs = api.get("/jobs", params={"encounter_id": encounter["encounter_id"]})
    except Exception as exc:
        show_error(exc)
        return
    for job in jobs:
        with st.expander(
            f"{MODALITIES[job['kind']]} · {STATES[job['status']]} · {job['job_id'][:8]}"
        ):
            for error in job["errors"]:
                st.warning(error)
            result = job["result"]
            if result.get("screening"):
                screening = result["screening"]
                st.write(
                    "Triagem experimental: "
                    + (
                        "alteração identificada para revisão"
                        if screening["flagged"]
                        else "nenhuma alteração identificada"
                    )
                )
                st.caption("Resultado limitado ao perfil selecionado; não é diagnóstico.")
            if result.get("evolution"):
                evolution = result["evolution"]
                st.write(
                    "Novas menções de sintomas: "
                    + (
                        ", ".join(
                            CONCEPTS.get(term, term) for term in evolution["new_symptom_mentions"]
                        )
                        or "nenhuma"
                    )
                )
            if result.get("text"):
                st.write(result["text"])
            if result.get("language"):
                sentiment = result["language"]["sentiment"]["sentiment"]
                st.caption(
                    "Sentimento: "
                    + {
                        "negative": "negativo",
                        "positive": "positivo",
                        "neutral": "neutro",
                        "mixed": "misto",
                    }.get(sentiment, sentiment)
                )
                st.caption("O sentimento é contextual; não define a prioridade do alerta.")
            if result.get("acoustic"):
                pauses = result["acoustic"]["long_pauses"]
                st.write(f"Pausas prolongadas: {len(pauses)}")
            if result.get("video"):
                st.write(f"Duração analisada: {result['video']['duration_seconds']:.1f} segundos")
                try:
                    snapshot = api.request(
                        "GET", f"/jobs/{job['job_id']}/artifacts/video-evidence.jpg"
                    )
                    st.image(snapshot.content, caption="Quadro com estimativa de pose", width=500)
                except RuntimeError:
                    pass
            with st.expander("Detalhes técnicos da análise"):
                st.json(result)
            if job["status"] in {"partial", "failed"} and st.button(
                "Tentar novamente", key=job["job_id"]
            ):
                try:
                    api.post(f"/jobs/{job['job_id']}/retry")
                    st.rerun(scope="fragment")
                except Exception as exc:
                    show_error(exc)
            if job["status"] in {"completed", "partial", "failed"}:
                try:
                    artifact = api.request("GET", f"/jobs/{job['job_id']}/artifacts/report.json")
                    st.download_button(
                        "Baixar relatório",
                        artifact.content,
                        file_name=f"analise-{job['job_id']}.json",
                        key=f"dl-{job['job_id']}",
                    )
                except RuntimeError:
                    pass


def media_input(encounter):
    st.subheader("Enviar uma gravação")
    st.caption(
        "Vídeo: um paciente em cena, corpo visível. "
        "Áudio: WAV PCM de 16 bits. Máximo padrão: 5 minutos."
    )
    kind = st.radio(
        "Tipo de gravação",
        ["audio", "video"],
        format_func=lambda item: MODALITIES[item],
        horizontal=True,
    )
    profiles = {
        "consultation": "Consulta — transcrição e contexto",
        "sustained_vowel": "Vogal sustentada — triagem vocal experimental",
        "squat": "Agachamento — uma repetição, corpo inteiro, meio perfil",
        "general": "Movimento e pausas — regras gerais",
    }
    profile = st.selectbox(
        "Perfil de análise",
        ["consultation", "sustained_vowel", "general"] if kind == "audio" else ["squat", "general"],
        format_func=profiles.get,
    )
    language = st.selectbox("Idioma da gravação e do texto", ["pt-BR", "en-GB", "en-US"])
    if profile in {"squat", "sustained_vowel"}:
        st.caption("Modelo experimental avaliado em uma amostra pequena. Não produz diagnóstico.")
    upload = st.file_uploader("Arquivo", type=["wav"] if kind == "audio" else ["mp4", "avi", "mov"])
    started = st.text_input(
        "Início da gravação (com fuso horário)",
        value=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
    )
    if st.button("Enviar para análise", type="primary", disabled=upload is None):
        try:
            metadata = {
                **context(encounter),
                "job_id": str(uuid4()),
                "kind": kind,
                "analysis_profile": profile,
                "language": language,
                "observed_at": started,
            }
            api.post(
                "/jobs/media",
                data={"metadata": json.dumps(metadata)},
                files={"file": (upload.name, upload.getvalue(), upload.type)},
            )
            st.success("Gravação recebida. O processamento aparecerá abaixo.")
        except Exception as exc:
            show_error(exc)
    with st.container(border=True):
        text = st.text_area(
            "Evolução clínica ou relato do paciente",
            key=f"clinical-text-{encounter['encounter_id']}",
        )
        if st.button("Analisar texto"):
            try:
                api.post(
                    "/jobs/text",
                    json={
                        **context(encounter),
                        "source_id": str(uuid4()),
                        "observed_at": datetime.now(UTC).isoformat(),
                        "text": text,
                        "language": language,
                    },
                )
                st.success("Texto recebido.")
            except Exception as exc:
                show_error(exc)
    processing_list(encounter)


@st.fragment(run_every="5s")
def live_alerts(encounter):
    try:
        alerts = api.get(
            "/alerts", params={"encounter_id": encounter["encounter_id"], "refresh": False}
        )
        active = [alert for alert in alerts if alert["status"] == "open"]
        if active:
            st.warning(f"{len(active)} alerta(s) aguardando revisão. Consulte a visão geral.")
        else:
            st.caption("Sem novos alertas · acompanhamento a cada 5 segundos")
    except RuntimeError:
        st.caption("Acompanhamento temporariamente indisponível")


def main():
    st.title("Clinical Monitor")
    st.caption("Monitoramento multimodal · apoio à revisão da equipe")
    try:
        configuration = api.get("/configuration")
        encounters = api.get("/encounters")
    except Exception as exc:
        show_error(exc)
        st.info("Verifique se a API está em execução e se o acesso está configurado.")
        return
    with st.sidebar:
        st.header("Atendimentos")
        if st.button("Novo paciente e atendimento", width="stretch"):
            try:
                patient_id, encounter_id = str(uuid4()), str(uuid4())
                api.post("/patients", json={"patient_id": patient_id})
                created_encounter = api.post(
                    "/encounters",
                    json={
                        "patient_id": patient_id,
                        "encounter_id": encounter_id,
                        "started_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                    },
                )
                encounters.append(created_encounter)
                st.session_state["active_encounter"] = encounter_id
                st.success("Paciente e atendimento criados.")
            except Exception as exc:
                show_error(exc)
        if not encounters:
            st.info("Crie um atendimento para começar.")
            return
        encounters_by_id = {item["encounter_id"]: item for item in encounters}
        if st.session_state.get("active_encounter") not in encounters_by_id:
            st.session_state["active_encounter"] = encounters[0]["encounter_id"]
        selected_id = st.selectbox(
            "Atendimento ativo",
            list(encounters_by_id),
            key="active_encounter",
            format_func=lambda identifier: (
                f"Paciente {encounters_by_id[identifier]['patient_id'][:8]} · {identifier[:8]}"
            ),
        )
        encounter = encounters_by_id[selected_id]
        st.caption("Identificadores pseudonimizados")
        st.toggle("Atualização automática", key="auto_refresh", value=False)
        st.caption("Atualiza os resultados a cada 10 segundos quando ativada.")
        st.button("Atualizar resultados", width="stretch")
        st.divider()
        st.write("Integrações")
        st.caption(
            "Speech: "
            + ("configurado" if configuration["azure_speech_configured"] else "não configurado")
        )
        st.caption(
            "Language: "
            + ("configurado" if configuration["azure_language_configured"] else "não configurado")
        )
        st.caption("Configuração não equivale a uma conexão validada.")
    live_alerts(encounter)
    overview, clinical, media, report = st.tabs(
        ["Visão geral", "Dados clínicos", "Mídias e texto", "Relatório"]
    )
    with overview:
        monitor(encounter)
    with clinical:
        clinical_data(encounter)
    with media:
        media_input(encounter)
    with report:
        st.subheader("Relatório do atendimento")
        if st.button("Gerar relatório atualizado"):
            try:
                content = api.request(
                    "GET",
                    f"/encounters/{encounter['encounter_id']}/report",
                    params={"format": "html"},
                )
                st.download_button(
                    "Baixar relatório HTML",
                    content.content,
                    file_name="relatorio-clinico.html",
                    mime="text/html",
                )
            except Exception as exc:
                show_error(exc)
    st.caption("Protótipo educacional. Resultados exigem revisão; não há validação clínica.")


main()
