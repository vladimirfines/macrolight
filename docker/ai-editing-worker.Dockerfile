FROM oven/bun:1.3.5 AS bun

FROM node:22-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git \
	&& rm -rf /var/lib/apt/lists/*
COPY --from=bun /usr/local/bin/bun /usr/local/bin/bun

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app
# Resolve workspace links and native workerd/esbuild packages inside Linux.
# Host node_modules never enter this context or shadow the installed tree.
COPY package.json bun.lock bunfig.toml ./
COPY apps/web/package.json apps/web/package.json
COPY apps/web/patches apps/web/patches
COPY apps/marketing/package.json apps/marketing/package.json
COPY packages packages
COPY services/ai-editing-worker services/ai-editing-worker
COPY services/lexical-service/package.json services/lexical-service/package.json
COPY services/bots/anthropic-status-bot/package.json services/bots/anthropic-status-bot/package.json
COPY services/bots/goalert-bot/package.json services/bots/goalert-bot/package.json
COPY services/bots/stripe-payment-bot/package.json services/bots/stripe-payment-bot/package.json
COPY vendor/ironcalc/pkg vendor/ironcalc/pkg
RUN bun install --frozen-lockfile --filter ai-editing-worker \
    && rm -rf /root/.bun/install/cache

WORKDIR /app/services/ai-editing-worker
RUN bun scripts/generate-sandbox.ts
EXPOSE 8933

CMD ["sh", "-c", "\
  printf 'OPENAI_API_KEY=%s\\nANTHROPIC_API_KEY=%s\\nCEREBRAS_API_KEY=%s\\n' \
    \"${OPENAI_API_KEY}\" \
    \"${ANTHROPIC_API_KEY}\" \
    \"${CEREBRAS_API_KEY}\" \
    > .dev.vars && \
  npx --no-install wrangler dev \
    --env local \
    --ip 0.0.0.0 \
    --var SYNC_WS_BASE:ws://sync-service:8787\
"]
