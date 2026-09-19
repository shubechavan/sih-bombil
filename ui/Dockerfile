# Dockerfile — the Next.js attribution console.
#
# Multi-stage so the runtime image carries the build output and production
# dependencies, not the toolchain.
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
ENV NODE_ENV=production \
    PORT=3000
# BACKEND_URL is read at runtime by app/api/_backend.ts, so the server-side
# route handlers can reach the API over the compose network even though the
# browser-facing NEXT_PUBLIC_API_URL was baked in above.
ENV BACKEND_URL=http://api:8000

COPY --from=deps /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
COPY package.json next.config.mjs ./

RUN useradd --create-home --uid 10001 sentinel && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 3000
CMD ["npm", "run", "start", "--", "--port", "3000", "--hostname", "0.0.0.0"]
