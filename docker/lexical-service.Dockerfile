FROM oven/bun:1.3.5 AS builder
WORKDIR /app

# Keep the frozen workspace graph and patches, without copying frontend source.
COPY package.json bun.lock bunfig.toml ./
COPY apps/web/package.json apps/web/package.json
COPY apps/web/patches apps/web/patches
COPY apps/marketing/package.json apps/marketing/package.json
COPY packages packages
COPY services/ai-editing-worker/package.json services/ai-editing-worker/package.json
COPY services/lexical-service services/lexical-service
COPY services/bots/anthropic-status-bot/package.json services/bots/anthropic-status-bot/package.json
COPY services/bots/goalert-bot/package.json services/bots/goalert-bot/package.json
COPY services/bots/stripe-payment-bot/package.json services/bots/stripe-payment-bot/package.json
COPY vendor/ironcalc/pkg vendor/ironcalc/pkg
RUN bun install --frozen-lockfile --filter lexical-service \
    && rm -rf /root/.bun/install/cache

WORKDIR /app/services/lexical-service
# Bundling orders the circular Lexical declarations correctly. Keep loro-crdt
# external and copy its complete package: its runtime loads WASM beside the JS.
RUN bun build src/server.ts --target=bun --external loro-crdt --outfile=/app/server.bundle.js

FROM oven/bun:1.3.5 AS runtime
WORKDIR /app
COPY --from=builder /app/server.bundle.js ./server.bundle.js
COPY --from=builder /app/node_modules/loro-crdt ./node_modules/loro-crdt
EXPOSE 8096
CMD ["bun", "/app/server.bundle.js"]
