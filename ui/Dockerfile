# Dockerfile — the Next.js attribution console.
#
# Multi-stage, and the runtime stage carries Next's standalone output rather
# than the whole dependency tree: server.js plus only the modules the build
# actually traced. Copying node_modules wholesale is what made this image 2 GB,
# most of it devDependencies that never run in production.
#
# The browser never calls the API directly: every attribution page goes through
# this app's own /api/attribution route handlers, which run server-side and read
# BACKEND_URL at runtime. So the compose service name works, and nothing has to
# be baked in for the browser.

FROM node:20-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

FROM node:20-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app
# server.js reads PORT and HOSTNAME from the environment; it does not accept the
# --port/--hostname flags `next start` took, so they move here.
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# BACKEND_URL is read at runtime by app/api/_backend.ts, so the server-side
# route handlers can reach the API over the compose network even though the
# browser-facing NEXT_PUBLIC_API_URL was baked in above.
ENV BACKEND_URL=http://api:8000

# The standalone bundle: server.js, the traced node_modules subset, and
# package.json. Nothing else from `deps` is needed.
COPY --from=build /app/.next/standalone ./
# Static assets are deliberately not part of the standalone trace — Next expects
# them served from .next/static next to the server. Omit this and the app boots
# and renders unstyled with no client JS, which is a confusing way to fail.
COPY --from=build /app/.next/static ./.next/static

RUN useradd --create-home --uid 10001 sentinel && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 3000
CMD ["node", "server.js"]
