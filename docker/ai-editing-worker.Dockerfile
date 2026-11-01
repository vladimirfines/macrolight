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
COPY apps/web apps/web
COPY apps/marketing apps/marketing
COPY packages packages
COPY services/ai-editing-worker services/ai-editing-worker
COPY services/lexical-service services/lexical-service
COPY services/bots services/bots
COPY vendor/ironcalc/pkg vendor/ironcalc/pkg
RUN bun install --frozen-lockfile

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
