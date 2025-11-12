# Multi-stage build
# Stage 1: Dependencies
FROM node:20-alpine AS deps
WORKDIR /app

# Install Python and build dependencies for native modules
# This helps with ARM64 builds and native module compilation
RUN apk add --no-cache python3 make g++ libc6-compat

# Copy package files
COPY ui/package*.json ./

# Clean install with proper handling of native modules
RUN rm -rf node_modules && \
    npm ci || npm install

# Stage 2: Builder
FROM node:20-alpine AS builder
WORKDIR /app

# Install libc6-compat for native modules in builder stage too
RUN apk add --no-cache libc6-compat

# Copy dependencies from deps stage
COPY --from=deps /app/node_modules ./node_modules

# Copy all files needed for build
COPY ui/package*.json ./
COPY ui/tsconfig.json ./
COPY ui/next.config.ts ./
COPY ui/components.json ./
COPY ui/sentry.edge.config.ts ./
COPY ui/sentry.server.config.ts ./
COPY ui/postcss.config.mjs ./
COPY ui/public ./public
COPY ui/src ./src

# Set build-time environment variables (needed for Next.js build)
ENV NEXT_PUBLIC_NODE_ENV="oss"
ENV NEXT_PUBLIC_AUTH_PROVIDER="local"
ENV NEXT_PUBLIC_DEPLOYMENT_MODE="oss"
ENV NEXT_PUBLIC_BACKEND_URL="http://localhost:8000"
ENV BACKEND_URL="http://api:8000"
ENV NEXT_TELEMETRY_DISABLED="1"
ENV NEXT_PUBLIC_CHATWOOT_URL="https://chat.dograh.com"
ENV NEXT_PUBLIC_CHATWOOT_TOKEN="3fkFx2mCEjNHjM9gaNc4A82X"

# Build the application with standalone mode
RUN npm run build && \
    rm -rf /tmp/* /root/.npm /root/.next/cache

# Stage 3: Runner (production image)
FROM node:20-alpine AS runner
WORKDIR /app

# Environment variables will be provided by docker-compose
ENV NODE_ENV=production

# Create a non-root user
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy standalone build output
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

# Switch to non-root user
USER nextjs

# Expose the port Next.js runs on
EXPOSE 3010

# Start the production server using the standalone Node.js server
CMD sh -c "echo '🚀 Application ready at http://localhost:3010' && PORT=3010 node server.js"