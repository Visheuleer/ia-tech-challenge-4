# Arquitetura proposta - Tech Challenge Fase 4

Status: desenho inicial para implementação. Nenhuma infraestrutura foi provisionada.
Diagrama visual: [SVG ampliável](arquitetura.svg) · [PNG em alta resolução](architecture.png) · [Fonte Mermaid](architecture.mmd).

![Diagrama da arquitetura multimodal: entradas, ingestão, análises, fusão e alertas](arquitetura.svg)

## Escopo

Monitorar pacientes combinando vídeo, áudio, texto, sinais vitais e prescrições; produzir evidências rastreáveis, relatórios e alertas para revisão médica.
Cenário inicial proposto: acompanhamento de reabilitação e internação, usando vídeos de fisioterapia, consultas gravadas e reprodução temporal de sinais vitais e prescrições.
Esse recorte é uma decisão de projeto, não uma exigência do enunciado.

## Organização

Uma base de código Python modular, com API e processos de trabalho separados.
A API recebe arquivos e eventos, registra metadados e agenda o processamento.
Processos especializados analisam as modalidades em segundo plano.
Um módulo de fusão reúne evidências por paciente, atendimento e intervalo temporal.
O painel permite visualizar processamento, evidências, relatórios e acompanhar alertas.

Os blocos do diagrama representam responsabilidades lógicas, não um microsserviço para cada bloco.
Toda consulta ou atualização do painel passa pela API, inclusive o reconhecimento de alertas.

## Componentes propostos

| Componente | Escolha inicial | Responsabilidade |
|---|---|---|
| Painel | Streamlit | Upload, linha do tempo, relatórios e alertas; atualização periódica no MVP |
| API | FastAPI | Validação, autenticação, consultas e ingestão |
| Tarefas | Celery e Redis | Processamento assíncrono com filas distintas para mídia e eventos clínicos |
| Persistência | PostgreSQL | Pacientes pseudonimizados, atendimentos, eventos, análises, alertas e auditoria |
| Arquivos | Diretório local no MVP; adaptador para Azure Blob Storage | Armazenar mídia e relatórios; mensagens levam referências, não arquivos completos |
| Vídeo | OpenCV e modelo de pose a selecionar | Extrair movimento e postura, medir desvios e registrar intervalos do vídeo |
| Áudio | Extração de características acústicas e detector a validar | Pausas, ritmo e alterações vocais; preservar evidências no áudio |
| Transcrição | Azure Speech to Text | Converter áudio em texto |
| Texto | Azure Text Analytics e regras de contexto | Sentimentos, termos relevantes, correspondência com vocabulário crítico e negações |
| Anomalias clínicas | Regras temporais e modelo estatístico a avaliar | Sinais vitais, alterações de prescrição e comparação com histórico |
| Execução | Docker Compose local e chamadas reais ao Azure | Ambiente reproduzível para desenvolvimento e demonstração |

As bibliotecas e modelos são propostas; versões, licença, consumo de recursos e desempenho devem ser verificados na implementação.
OpenPose e YOLOv8 são candidatos do enunciado. Escolheremos o modelo após uma amostra representativa dos vídeos.
Sentimento textual não será tratado como diagnóstico nem como medida direta de gravidade.
Alterações de prescrições serão detectadas comparando medicamento, dose, unidade, frequência e sequência temporal.

## Fluxo de dados

1. Receber arquivo ou evento com paciente, atendimento, origem e horário.
2. Validar formato, unidades, tamanho e vinculação ao atendimento.
3. Persistir o evento ou referência ao arquivo e registrar tarefa pendente.
4. Despachar tarefas para a fila correspondente; reconciliar tarefas persistidas ainda não enfileiradas.
5. Processar modalidades. Áudio segue tanto para análise acústica quanto para transcrição.
6. Normalizar resultados e persistir evidências.
7. Executar fusão temporal e regras de alerta.
8. Persistir alerta, atualizar o painel e gerar relatório com os trechos relevantes.
9. Registrar reconhecimento, revisão e encerramento pelo usuário.

## Contrato de evidência

Campos mínimos: event_id, patient_id, encounter_id, modality, source_id, observed_at,
window_start, window_end, processed_at, detector_name, detector_version,
finding_type, measured_value, unit, quality_status, evidence_reference e confidence quando aplicável.
Não inventar confiança para regras determinísticas; registrar os critérios que dispararam a regra.
Manter horário do evento separado do horário de processamento. Usar UTC na persistência e fuso local na interface.
Áudio e vídeo precisam de horário de início da gravação para converter posições relativas em horários dos eventos.

## Fusão multimodal

Abordagem inicial: combinar decisões e evidências, sem treinar um único modelo multimodal.
Agrupar somente dados do mesmo paciente e atendimento em janelas temporais configuráveis.
Regras versionadas explicam quais evidências geraram cada alerta.
Uma evidência crítica pode gerar alerta sem esperar todas as modalidades.
Evidências complementares podem atualizar um alerta existente.
Modalidade ausente ou serviço indisponível deve aparecer como análise parcial, nunca como resultado normal.
Aceitar eventos atrasados e reavaliar as janelas afetadas, mantendo histórico de revisão.
Os limiares serão parâmetros de demonstração documentados, com avaliação no conjunto de dados escolhido.

## Tempo real e resiliência

Separar a fila de mídia da fila clínica para impedir que um vídeo longo atrase sinais vitais.
No MVP, sinais vitais entram continuamente por reprodução temporal; vídeos e áudios gravados são processados em segundo plano.
Isso demonstra monitoramento por eventos, mas não equivale a captura ao vivo de áudio e vídeo.
Meta inicial de engenharia: alerta de sinais vitais disponível no painel em até 5 segundos após a ingestão, a validar em carga.
Medir separadamente latência de ingestão, fila, análise e atualização do painel; registrar atrasos da origem.
O diagrama prevê atualização por API/SSE. Streamlit pode começar consultando a API periodicamente; SSE fica para cliente compatível.
Usar idempotência por evento e versão de análise, retentativas limitadas e registro de tarefas com falha.
Deduplicar alertas por paciente, tipo e janela; registrar estados aberto, reconhecido e encerrado.
Persistir alertas antes da notificação. O painel consulta alertas não lidos após reconexão.
Falhas do Azure devem permitir reprocessamento e deixar o estado parcial visível.

## Acesso e observabilidade

Usar dados sintéticos ou desidentificados na demonstração.
Manter credenciais fora do código, controlar acesso por atendimento/perfil e não registrar conteúdo clínico bruto nos logs.
Registrar identificadores de correlação, versões dos detectores, tempo por etapa, falhas, atraso das filas e alterações de alertas.
Definir retenção e exclusão de arquivos e evidências antes de utilizar dados reais.

## Validação e evidências para a entrega

- Vídeo: comparar eventos detectados com trechos anotados e emitir relatório de desvios.
- Áudio: avaliar transcrição e alterações acústicas separadamente.
- Texto: verificar termos críticos, negações e sentimento em exemplos revisados.
- Anomalias: incluir casos normais e anormais de sinais vitais, prescrições e movimento.
- Fusão: testar modalidades ausentes, dados atrasados, pacientes distintos e evidências correlacionadas.
- Alertas: medir latência e verificar deduplicação, reconhecimento e recuperação de falhas.
- Evitar divisão aleatória de janelas do mesmo paciente entre treino e teste.
- Na demonstração final, executar integrações Azure reais e mostrar o percurso da entrada ao alerta.

## Fontes e relação com o enunciado

Requisitos extraídos do PDF fornecido pelo usuário: IADT - Fase 4 - Tech challenge (1).pdf.
O documento exige análise de vídeo e áudio, Azure Speech to Text, Azure Text Analytics,
anomalias em sinais vitais/prescrições/movimento, relatórios e alertas.
A organização em módulos, tecnologias auxiliares e meta de latência são decisões propostas neste desenho.

Documentação consultada:
- Azure Speech: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/overview
- Azure Language: https://learn.microsoft.com/en-us/azure/ai-services/language-service/overview

A documentação atual agrupa os recursos de Text Analytics no Azure Language.
Sentiment analysis e key phrase extraction aparecem como capacidades legacy; validar disponibilidade
na região e assinatura escolhidas antes da implementação, mantendo o requisito Azure do enunciado.

