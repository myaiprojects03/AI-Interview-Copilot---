# Production image for the web app (Next.js standalone server).
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# The public API address is compiled into the browser code and the security policy.
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT=standalone
RUN test -n "$NEXT_PUBLIC_API_URL" || (echo "Build argument NEXT_PUBLIC_API_URL is required" && exit 1)
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S app && adduser -S app -G app
COPY --from=build /app/public ./public
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
USER app
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/login" > /dev/null || exit 1
# Defaults for hosts that run one container per service (Render): the sign-in address is the
# service's own public URL, and the server reaches the API at its public address.
# (Only filled in when empty and the host provides a value: an empty NEXTAUTH_URL breaks sign-in.)
CMD ["sh", "-c", "[ -n \"$NEXTAUTH_URL\" ] || [ -z \"$RENDER_EXTERNAL_URL\" ] || export NEXTAUTH_URL=\"$RENDER_EXTERNAL_URL\"; [ -n \"$BACKEND_URL\" ] || [ -z \"$NEXT_PUBLIC_API_URL\" ] || export BACKEND_URL=\"$NEXT_PUBLIC_API_URL\"; exec node server.js"]
