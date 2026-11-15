# Clinical Monitor

Monitoramento multimodal para o Tech Challenge Fase 4: vídeo, áudio, texto, sinais vitais e prescrições. O projeto reúne evidências por atendimento, gera alertas revisáveis e exporta relatórios rastreáveis.

![Arquitetura](docs/architecture.png)

## Executar localmente

Pré-requisitos: Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker com Compose e acesso à internet para baixar dependências/modelo. Execute na raiz:

```powershell
uv sync --locked --all-extras
docker compose up -d --wait postgres redis
```

Na primeira instalação, copie `.env.example` para `.env`, sem substituir uma configuração existente. Preencha as quatro variáveis `CLINICAL_MONITOR_AZURE_SPEECH_ENDPOINT`, `CLINICAL_MONITOR_AZURE_SPEECH_KEY`, `CLINICAL_MONITOR_AZURE_LANGUAGE_ENDPOINT` e `CLINICAL_MONITOR_AZURE_LANGUAGE_KEY`. Use as URLs-base dos recursos. Chaves ficam somente no `.env`, ignorado pelo Git.

```powershell
uv run --all-extras python -m clinical_monitor.tools.prepare_model
uv run --all-extras python -m clinical_monitor.tools.run_local --demo-rules
```

O iniciador aplica migrações e executa API, dois trabalhadores, agendador e painel. Ctrl+C encerra os processos iniciados. PostgreSQL e Redis continuam no Docker. Logs locais: `output/run/`.

- Painel: http://127.0.0.1:8501
- API interativa: http://127.0.0.1:8000/docs
- Saúde: http://127.0.0.1:8000/health

`--demo-rules` usa uma faixa **artificial** de 10 a 20 para frequência cardíaca e uma janela de persistência de 10 segundos. Serve para testar o fluxo e não é referência médica. Para usar parâmetros próprios, remova essa opção e configure `CLINICAL_MONITOR_VITAL_RANGES` e `CLINICAL_MONITOR_VITAL_TEMPORAL_RULES` no `.env`. Os padrões vazios não inventam faixas clínicas. `--api-port` e `--dashboard-port` permitem alterar as portas.

## Dados reais e validação reproduzível

```powershell
uv run --all-extras python -m clinical_monitor.tools.prepare_real_data
uv run python -m clinical_monitor.tools.prepare_vitals
uv run --all-extras python -m clinical_monitor.tools.evaluate_real_data
uv run python -m clinical_monitor.tools.validate_real
```

Os dois primeiros comandos preparam gravações humanas e sinais hospitalares públicos, com procedência e verificações de integridade em `data/real/`. O catálogo de fontes, licenças e restrições está em `datasets/catalog.json`. A preparação de vídeos baixa somente os membros necessários do arquivo remoto. Os arquivos de mídia não entram no Git.

`evaluate_real_data` reproduz o treinamento dos dois classificadores experimentais e a avaliação por participante. Os coeficientes pequenos acompanham o código em `src/clinical_monitor/analysis/models/`; os resultados completos ficam em `data/real/evaluation/results.json`. O extrator de pose YOLO é pré-treinado. Normalização e ajuste dos classificadores usam somente a divisão de desenvolvimento; o limiar é fixo em 0,5.

`validate_real` exige a aplicação em execução, Azure configurado e faixa de frequência cardíaca ou oxigenação configurada. Reproduz 60 segundos de sinais BIDMC, envia 18 mídias e registra os resultados em `data/real/evaluation/integration.json`. Um atendimento sintético separado verifica duas evoluções clínicas, alteração de prescrição, revisão de alertas e geração do relatório clínico da aplicação. Cada gravação independente recebe seu próprio atendimento: os participantes de bases diferentes não são tratados como uma única pessoa. `--interval 1` reproduz os sinais na cadência original; o padrão acelera o replay. `--url` permite escolher outra API.

No painel, use os seguintes perfis:

| Entrada | Perfil | Observação |
|---|---|---|
| `rehab24/*-rep*.mp4` | Agachamento | Uma repetição, corpo inteiro, meio perfil |
| `voiced/*.wav` | Vogal sustentada | Triagem experimental de alteração vocal; não transcreve vogais |
| `primock57/*-first120s.wav` | Consulta | Selecione `en-GB`; vozes humanas de consultas encenadas |
| Outros vídeos | Movimento e pausas | Regras geométricas; não avalia cirurgia nem qualquer exercício |

O perfil de consulta trata pausas como contexto de conversação. Azure Speech transcreve; Azure Language extrai frases-chave e sentimento. Vocabulário crítico em português/inglês respeita negações locais e cruza as expressões extraídas pelo Azure. Evoluções são comparadas à anotação anterior disponível do mesmo atendimento; uma menção ausente não comprova recuperação. A análise temporal de sinais acontece automaticamente a cada medição quando há regra configurada.

O indicador de novos alertas acompanha o atendimento a cada cinco segundos sem recarregar os formulários. A atualização completa das listas continua manual ou opcional a cada dez segundos.

[Arquitetura implementada](docs/architecture.md)

## Organização

| Diretório | Responsabilidade |
|---|---|
| `domain` | Contratos, validações e estados |
| `analysis` | Detectores de áudio, vídeo, texto, sinais e prescrição |
| `application` | Ingestão, tarefas, fusão e relatórios |
| `infrastructure` | Persistência e integração Azure |
| `api` | Rotas HTTP e autenticação |
| `dashboard` | Interface Streamlit |
| `tools` | Inicialização, preparação e validação |
| `migrations`, `tests` | Evolução do banco e validação |

Os módulos estão em `src/clinical_monitor/`. `uv.lock` fixa as dependências. O extra `video` inclui Ultralytics/PyTorch; a suíte de regras não baixa pesos de modelos.

## Configuração e operação

O prefixo das variáveis é `CLINICAL_MONITOR_`; o contrato completo está em `core/config.py`. Além das credenciais: `DATABASE_URL`, `REDIS_URL`, `DATA_DIR`, `POSE_MODEL_PATH`, `API_TOKEN`, `FUSION_WINDOW_SECONDS`, `MAX_UPLOAD_MB` e `MAX_MEDIA_SECONDS`. Variáveis de ambiente têm precedência sobre `.env`.

Áudio: WAV PCM16, mono/estéreo, 8–96 kHz; conversão interna para 16 kHz. Vídeo: MP4/AVI/MOV decodificáveis por OpenCV, uma pessoa com corpo visível. Limites padrão: 50 MB e 300 segundos. O início da gravação deve ter fuso e pertencer ao atendimento. Horários retornados são UTC.

Para habilitar o token compartilhado, defina `CLINICAL_MONITOR_API_TOKEN`; clientes da API enviam `Authorization: Bearer ...`. O painel lê o mesmo valor. Isso não implementa usuários, perfis nem autorização por atendimento. O ambiente validado é local, com controles sintéticos e amostras públicas autorizadas para pesquisa.

Alternativa de empacotamento: `docker compose --profile app up --build -d`, depois de preparar o modelo. O Compose inclui API, painel, trabalhadores e agendador. A execução integral dessa imagem não foi validada nesta entrega; a execução local com PostgreSQL/Redis em contêineres foi validada. O extra PyTorch pode produzir imagem grande.

Não execute simultaneamente dois agendadores para o mesmo ambiente. As tarefas têm tentativas limitadas e recuperação de lease expirado; o painel permite reenviar tarefas parciais ou falhas. Sem Azure, não é necessário descartar as evidências já produzidas.

## Verificação

```powershell
uv run --all-extras ruff check .
uv run --all-extras ruff format --check .
$env:TEST_DATABASE_URL = 'postgresql+psycopg://clinical_monitor:clinical_monitor_local@127.0.0.1:5432/clinical_monitor'
uv run --all-extras pytest
```

A URL deve apontar para banco de desenvolvimento; os testes PostgreSQL criam e removem schemas isolados. Sem `TEST_DATABASE_URL`, esses casos são ignorados. O workflow de CI executa os testes e verificações de código; sua execução remota não foi verificada.

## Escopo da entrega

Protótipo educacional funcional, sem validação clínica. Na amostra reservada, o classificador de agachamento acertou 4/4 vídeos de um participante; a triagem vocal acertou 6/12 gravações. A amostra de vídeo é muito pequena, e o resultado vocal é insuficiente para uso clínico. Esses resultados e as abstenções ficam registrados sem descartar erros. VOICED não valida cansaço, dispneia ou disartria; PriMock57 contém consultas encenadas por pessoas, sem diagnóstico acústico real dos atores.

Sentimento negativo não gera alerta sozinho. O replay hospitalar verifica o funcionamento das regras, não sua acurácia clínica: BIDMC não oferece rótulos de anomalia nem pressão arterial nessa seleção. A medição de latência cobre ingestão e persistência de alertas na API; não equivale a teste de carga nem garante prazo de processamento de mídia ou exibição no navegador.

Pesos YOLO, mídia, dados, `.env` e saídas locais não são versionados automaticamente. Os coeficientes experimentais e o resumo numérico de avaliação são versionáveis. Preserve as atribuições e restrições das fontes, incluindo o uso acadêmico não comercial de REHAB24-6 e a licença do Ultralytics.
