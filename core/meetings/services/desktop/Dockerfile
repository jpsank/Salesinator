# syntax=docker/dockerfile:1.7
# ==============================================================================
# @vexa/desktop run as the stack's CAPTURE INGEST (src/stack-main.ts).
#
# Clients (the Mac capture app, the browser extension) stream capture.v1 audio to it with the user's API key; each
# call becomes a meeting in the stack, transcribed by the stack's own STT and fed to the same pipeline a bot feeds.
# No browser: node + the carved @vexa/* bricks only, so this is far lighter than the bot image.
#
# Build context = the repo root (pnpm-workspace.yaml is there):
#   docker build -t vexaai/v012-capture:dev -f core/meetings/services/desktop/Dockerfile .
# ==============================================================================
FROM node:22-bookworm-slim AS builder

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=1 \
    # The workspace's browser tooling is not part of this image; never fetch it during `pnpm install`.
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# pnpm pinned to the workspace's packageManager via corepack.
RUN corepack enable && corepack prepare pnpm@11.7.0 --activate && pnpm --version

WORKDIR /build
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json tsconfig.base.json turbo.json ./
COPY core ./core

# onnxruntime-node (via @vexa/mixed-pipeline) runs its native postinstall through the workspace's allowBuilds.
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile=false --filter "@vexa/desktop..."

# The selector builds @vexa/desktop and every workspace brick it depends on, in dependency order.
RUN pnpm --filter "@vexa/desktop..." run build \
    && test -f core/meetings/services/desktop/dist/stack-main.js

# Bake the Zoom/Teams lane's diarization model into an image-resident cache so a call never pays a download stall
# (the same step, and the same model, as the bot image). It degrades loudly if HuggingFace is unreachable.
ENV VEXA_HF_CACHE=/opt/hf-cache
RUN node core/meetings/services/bot/warm-hf-cache.mjs

# ------------------------------------------------------------------------------
FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production VEXA_HF_CACHE=/opt/hf-cache
WORKDIR /build
COPY --from=builder /build /build
COPY --from=builder /opt/hf-cache /opt/hf-cache
WORKDIR /build/core/meetings/services/desktop
USER node
EXPOSE 9099
CMD ["node", "dist/stack-main.js"]
