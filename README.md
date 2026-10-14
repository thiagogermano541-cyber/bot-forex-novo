# JARVIS AI

Primeira versão de uma plataforma local de análise contextual de **BTC/USD** com a API oficial da Anthropic. Claude interpreta o mercado; o backend fornece dados, verifica integridade, valida JSON, registra contexto e distribui notificações. Não existe motor de indicadores tomando decisões direcionais nem execução de ordens.

## Escopo atual

Somente BTC/USD está habilitado no worker, na API e no dashboard. A análise de XAU/USD foi desativada; registros legados permanecem no banco. A coleta REST roda separadamente das chamadas Claude, a cada 30 segundos, com reconexão WebSocket e validação de candles fechados.

## Estado desta entrega

Código do backend, dashboard, conectores reais, Compose e testes escritos. A sintaxe Python foi verificada neste ambiente. **Os testes de dependências, o build Next.js e a execução completa do Compose não foram executados**, pois a instalação ficou bloqueada na etapa de permissão de rede. Nenhuma integração autenticada ou qualidade analítica foi validada com credenciais reais. Não há métricas de desempenho pré-preenchidas.

## Estrutura

```text
jarvis-ai/
├── backend/
│   ├── app/
│   │   ├── main.py          # API, autenticação e permissões
│   │   ├── worker.py        # coleta, streaming, entregas e avaliação
│   │   ├── schemas.py       # contratos de dados e saída do Claude
│   │   ├── service.py       # fluxo de análise, memória, orçamento, auditoria
│   │   ├── claude.py        # SDK oficial, prompt e tool de saída JSON
│   │   ├── providers/      # Kraken, OANDA e FRED
│   │   ├── telegram.py     # outbox, deduplicação e tentativas
│   │   ├── evaluation.py   # resultado direcional e paper trading
│   │   ├── auth.py         # Argon2 + JWT em cookie HttpOnly
│   │   └── db.py           # PostgreSQL / SQLAlchemy assíncrono
│   └── tests/              # mocks exclusivamente em testes
├── frontend/               # Next.js, TypeScript, Tailwind, shadcn/ui
│   ├── app/                # dashboard responsivo e login
│   └── components/         # Lightweight Charts e componentes UI
├── docs/ARCHITECTURE.md
├── .env.example
└── compose.yaml
```

## Executar com Docker

Para hospedar, consulte [deploy no Railway](docs/RAILWAY.md). O repositório inclui configs separados para API, worker e frontend, porta dinâmica, suporte IPv4/IPv6 e proxy da API em runtime. A publicação requer conexão autorizada à conta Railway.

Requisitos: Docker Engine com Compose v2, acesso aos registries e APIs dos provedores. O dashboard e a documentação da API só são expostos em loopback.

```bash
cd /workspace/jarvis-ai
cp .env.example .env
python -c 'import secrets; print(secrets.token_hex(32))'
```

Edite `.env`: coloque a chave gerada em `JWT_SECRET`, uma senha de pelo menos 12 caracteres em `ADMIN_PASSWORD` e altere `POSTGRES_PASSWORD` e a senha em `DATABASE_URL` para os mesmos valores. A senha do banco deve ser codificada para URL se contiver caracteres especiais. Não deixe as senhas de exemplo.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f backend worker
```

- Dashboard: **http://localhost:3000**
- API OpenAPI: **http://localhost:8000/docs**
- Readiness: **http://localhost:8000/health/ready**
- Login: `ADMIN_EMAIL` e `ADMIN_PASSWORD` configurados no servidor.

Sem chaves externas, a aplicação inicia com autenticação válida, coleta os dados públicos Kraken quando habilitados e mostra **NÃO CONFIGURADO** para Claude, OANDA, FRED e Telegram. Não usa dados de demonstração. As tabelas são criadas no primeiro start; esta versão ainda usa `create_all`, sem migrations incrementais.

```bash
docker compose down
# Preserva os volumes. Não use -v se quiser manter histórico e auditoria.
```

## Configurar integrações

**Claude:** preencha `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` com um ID disponível em sua conta e as tarifas oficiais do modelo em `CLAUDE_INPUT_USD_PER_MILLION` e `CLAUDE_OUTPUT_USD_PER_MILLION`. Sem tarifas positivas, o estado permanece NÃO CONFIGURADO. Ajuste orçamento, limite de tokens e intervalo. O SDK retorna JSON através de uma ferramenta de saída forçada; essa ferramenta não executa ações.

`CLAUDE_ENABLED=false` pausa as análises sem interromper a coleta ou o login. `CLAUDE_PAUSE_REASON` explica o motivo no painel. Depois de resolver uma restrição da conta Anthropic, use `CLAUDE_ENABLED=true` no backend e no worker para reativar. A chave continua exclusivamente nas variáveis do servidor.

**BTC/USD:** Kraken spot público: candles OHLCV, trades e profundidade local, REST para histórico e recuperação, WebSocket ticker para bid/ask. Não requer chave. Derivativos e liquidações não são fornecidos por esse conector e ficam explicitamente indisponíveis. Não confunda livro da Kraken com mercado global.

**XAU/USD:** configure `OANDA_TOKEN`, `OANDA_ACCOUNT_ID` e `OANDA_ENVIRONMENT=practice` ou `live`. A conta precisa permitir consultas a `XAU_USD`; disponibilidade depende da região. O sistema só consulta dados, inclusive em ambiente live. Candles midpoint, bid/ask e tick volume; sem livro centralizado, volume real ou ordens institucionais presumidas. Streaming HTTP é a opção nativa da OANDA, não WebSocket.

**Macro:** `FRED_API_KEY` habilita DFF, DGS10 e DTWEXBGS com data da observação e vintage da coleta. São séries diárias com atrasos e possíveis revisões, **não calendário de notícias em tempo real**. Replays usam o vintage salvo, sem consultar uma versão futura do FRED.

**Telegram:** crie seu bot na Bot API, configure token e IDs autorizados em `TELEGRAM_CHAT_IDS` separados por vírgula. `TELEGRAM_ENABLED=true` autoriza os envios reais do worker. Há alertas, invalidações por preço observado, atualizações de AGUARDAR e resumos manuais. A outbox registra sucesso/falha e limita tentativas. Mensagens contêm apenas conteúdo analítico e identificadores públicos da análise; URLs com token nunca entram nos logs da aplicação.

Após editar `.env`:

```bash
docker compose up -d --force-recreate backend worker
```

## Desenvolvimento sem containers da aplicação

Disponibilize PostgreSQL e Redis localmente e ajuste `DATABASE_URL`, `REDIS_URL` para `localhost`. Se usar somente os serviços do Compose, publique suas portas por um override local. Os comandos abaixo usam `.env` na raiz via `--env-file`.

```bash
python -m venv .venv
.venv/bin/pip install -e './backend[test]'
.venv/bin/uvicorn app.main:app --env-file .env --reload --port 8000
```

Em outro terminal, exporte as variáveis de `.env` com sua ferramenta de ambiente e execute `.venv/bin/python -m app.worker`, ou execute o worker a partir da raiz para o Pydantic ler `.env` automaticamente.

```bash
cd frontend
npm install
npm run dev
```

O proxy server-side `/api/*` do Next.js aponta para `http://localhost:8000` em desenvolvimento e `http://backend:8000` no Docker. `BACKEND_URL` pode ser definido em runtime para a rede privada Railway. Nenhuma chave é `NEXT_PUBLIC_*`. Após a primeira instalação, preserve `package-lock.json` e use `npm ci` em builds reprodutíveis; esta entrega ainda não inclui lockfile gerado.

## Testes e verificação

```bash
cd backend
../.venv/bin/pytest -q
../.venv/bin/ruff check app tests
cd ../frontend
npm run typecheck
npm run build
```

Os testes cobrem atualidade, lacunas, candles futuros, OHLC inválido, NaN, evidências inventadas, resposta truncada, retries REST, tick volume, horizonte fixo, macro futura, autenticação, CSRF, permissões, persistência e entregas Telegram. Integrações HTTP são mockadas **somente nos testes**. A integração de API utiliza SQLite real de teste; valide também o fluxo com PostgreSQL/Redis no Compose antes de produção.

Chamadas POST fora do navegador precisam do header `Origin: http://localhost:3000`, ou da origem definida em `APP_ORIGIN`, e do cookie da sessão. Crie observadores ou administradores via `POST /users`; não há cadastro público.

## Avaliação e limites da primeira versão

- Replay: selecione um snapshot salvo no Histórico; o contexto e a memória só contêm informação disponível antes do cutoff. Registros ficam marcados BACKTEST e não geram Telegram ou alertas ao vivo. Cada replay consome API.
- Avaliação: compara a cotação inicial com o fechamento M1 no horizonte fixo configurado, sem selecionar retrospectivamente o melhor preço. Só registra resultado quando o candle correspondente está armazenado. A precisão é observacional, com contagem de amostras e separação por ativo e modo.
- Esta primeira versão interpreta todos os seis timeframes, mas **avalia resultados somente no M1**. Avaliação independente por M5/M15/M30/H1/H4, replay de um intervalo completo, spreads históricos, slippage e uma estratégia validada ficam para a próxima iteração. Não existe selo de validação automática.
- Paper trading: registro manual de análise direcional atual, entrada por ask/bid, taxa declarada e saída por fechamento M1 no horizonte. É simulação; não modela stop executável, slippage ou funding. Não é resultado real.
- Histórico Kraken REST é limitado pelo provedor; snapshots passam a ser acumulados a partir da execução. Lacunas, mercado fechado ou erros de provedor bloqueiam direção.
- Referências a evidências são verificadas; a veracidade semântica de toda frase do modelo ainda exige revisão. O prompt e o JSON reduzem risco de alucinação, mas não comprovam uma hipótese financeira.
- Orçamento: cada chamada reserva persistentemente um teto conservador para até três tentativas. Reservas não são gastos reais: o custo exibido usa tokens informados pelo SDK, inclusive saídas rejeitadas. Timeouts podem deixar custo real desconhecido; sua reserva permanece bloqueando a quota diária. Tarifas são configuradas pelo operador.
- Deduplicação por cenário exato e chat; Telegram não oferece garantia de exatamente uma entrega após timeout ambíguo. A primeira versão requer um worker; escalar exige ampliar leases e revisar concorrência.
- JWT é válido por oito horas; logout remove o cookie, mas não revoga tokens copiados. Antes de exposição pública: HTTPS, `COOKIE_SECURE=true`, gestão de segredos, migrations, backups, revogação de sessões e revisão operacional. O Compose é para desenvolvimento local.

## Fluxo

`provedores → snapshot fechado + UTC → gate de integridade → contexto + memória → Claude → schema + grounding → auditoria → alertas/outbox → dashboard → avaliação posterior`

O gate do backend só pode interromper com DADOS INSUFICIENTES. As decisões CENÁRIO DE ALTA, CENÁRIO DE BAIXA e AGUARDAR são do Claude. Sem Claude configurado, o sistema não simula análises de IA.
