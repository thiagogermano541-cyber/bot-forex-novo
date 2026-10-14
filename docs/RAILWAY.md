# Deploy JARVIS AI no Railway

Fonte: `thiagogermano541-cyber/bot-forex-novo`, branch `main`.

A importação padrão pela raiz agora usa `Dockerfile` e `railway.toml` na raiz para construir somente o **dashboard**. Renomeie esse serviço para `frontend` ao montar os cinco serviços abaixo e configure `BACKEND_URL`. A página de login servida pelo frontend não comprova que a API, o banco ou as integrações foram configurados. Para os serviços adicionais, use a raiz `/backend` e seus configs dedicados.

## Serviços

Crie um projeto JARVIS AI com PostgreSQL, Redis e três serviços conectados ao mesmo repositório. Use **uma réplica** para API e worker nesta versão. PostgreSQL e Redis devem manter os volumes persistentes dos templates Railway.

| Serviço | Root Directory | Config File (caminho no repositório) | Início | Saúde | Exposição |
| --- | --- | --- | --- | --- | --- |
| `backend` | `/backend` | `/backend/railway.toml` | `python -m app.run` | `/health/ready` | Rede privada |
| `worker` | `/backend` | `/backend/railway.worker.toml` | `python -m app.worker` | Heartbeat Redis | Rede privada, sem domínio |
| `frontend` | `/frontend` | `/frontend/railway.toml` | `node server.js` | `/` | Domínio HTTPS Railway |
| `Postgres` | Template Railway | — | Template | Template | Rede privada |
| `Redis` | Template Railway | — | Template | Template | Rede privada |

No worker, selecione explicitamente `railway.worker.toml` em **Settings → Config File**. O config da API tem healthcheck HTTP; o worker não oferece servidor HTTP. Em **Root Directory**, os nomes `Dockerfile` usados nos configs são relativos à raiz do serviço. Confirme o Dockerfile detectado no log de build. Não importe o `compose.yaml`: ele é usado somente localmente.

## Variáveis

Gere `JWT_SECRET` aleatório com pelo menos 32 caracteres e `ADMIN_PASSWORD` aleatório com pelo menos 12 caracteres. Guarde-os em variáveis compartilhadas do Railway; nunca no GitHub, no frontend ou em logs. Defina `ADMIN_EMAIL` com o e-mail que usará no login.

Backend e worker recebem as mesmas referências:

```dotenv
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
JWT_SECRET=${{shared.JWT_SECRET}}
ADMIN_EMAIL=${{shared.ADMIN_EMAIL}}
ADMIN_PASSWORD=${{shared.ADMIN_PASSWORD}}
APP_ORIGIN=https://${{frontend.RAILWAY_PUBLIC_DOMAIN}}
COOKIE_SECURE=true
KRAKEN_ENABLED=true
ANALYSIS_INTERVAL_SECONDS=300
STALE_AFTER_SECONDS=90
DAILY_AI_BUDGET_USD=5
TELEGRAM_ENABLED=false
```

Os nomes `Postgres`, `Redis`, `frontend` e as variáveis compartilhadas acima precisam corresponder aos nomes reais dos serviços. Se o template de Redis tiver outro nome ou variável, selecione sua URL privada correspondente pelo editor de referências do Railway. O backend normaliza `postgresql://` e `postgres://` para o driver assíncrono `asyncpg`.

No **backend** defina também `PORT=8000`. O entrypoint liga um socket IPv4/IPv6 para suportar a rede privada Railway. Gere o domínio público do frontend antes de validar `APP_ORIGIN`.

No **frontend**:

```dotenv
BACKEND_URL=http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:8000
```

O Next.js lê `BACKEND_URL` em runtime pelo proxy server-side `/api/*`. Cookies HttpOnly são repassados sem expor chaves. Não coloque URL PostgreSQL, Redis, chave Anthropic, senha de administrador ou token Telegram no serviço frontend. O frontend respeita o `PORT` definido pela plataforma.

## Integrações opcionais

Adicione **ao backend e ao worker** as variáveis externas que quiser habilitar, seguindo `.env.example`:

- Claude: `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, tarifas USD por milhão de tokens de entrada e saída. Sem elas, o dashboard mostra NÃO CONFIGURADO.
- Ouro: `OANDA_TOKEN`, `OANDA_ACCOUNT_ID`, `OANDA_ENVIRONMENT`.
- Macro: `FRED_API_KEY`.
- Telegram: token, IDs autorizados e `TELEGRAM_ENABLED=true` para ativar entregas reais.

As credenciais externas não são necessárias para publicar e acessar a aplicação. São necessárias para suas integrações respectivas.

## Ordem e validação

1. Criar PostgreSQL e Redis com volumes persistentes.
2. Criar os três serviços GitHub e ajustar suas raízes/configs.
3. Gerar o domínio HTTPS do frontend, segredos compartilhados e referências de variáveis.
4. Publicar backend e confirmar `/health/ready` via rede privada ou logs de healthcheck Railway.
5. Publicar worker; ele aguarda esquema do banco e Redis por até 180 segundos. A API cria o esquema inicial e o administrador.
6. Publicar frontend e acessar o domínio HTTPS. Fazer login com as credenciais guardadas nas variáveis do Railway.
7. Confirmar worker ONLINE, snapshots com horários recentes, estados explícitos das integrações, e ausência de chaves nas respostas de `/api/config`.

O domínio do frontend só é entregue como publicado após o deployment ficar ativo e o login/saúde serem verificados. Esta preparação de código, sozinha, não cria um projeto Railway.

## Diagnóstico

- Build a partir da raiz errada: conferir Root Directory e config file de cada serviço.
- API sem conexão ao PostgreSQL/Redis: conferir referências privadas, serviços e mesma região/ambiente. A saúde da API falha até ambos responderem.
- Erro 403 no login: `APP_ORIGIN` deve ser a origem HTTPS exata do dashboard, sem barra final.
- Proxy 503: conferir `BACKEND_URL`, `PORT=8000`, saúde da API e domínio privado. IPv6 está habilitado no entrypoint.
- Worker reiniciando: publicar API primeiro, conferir schema e permissões do banco; consultar tipos de erro nos logs estruturados.
- Claude NÃO CONFIGURADO: verificar chave, modelo e ambas as tarifas positivas no backend e no worker.

Trocar o domínio público exige atualizar `APP_ORIGIN` e reiniciar backend/worker. Não altere nem remova os volumes para um redeploy.
