# Multi-stage build
# Stage 1: Dependencies
FROM node:20-alpine AS deps
WORKDIR /app

# Copy package files
COPY package*.json ./
# Install all dependencies (including dev dependencies for building)
RUN npm ci --cache /tmp/empty-cache && \
    npm cache clean --force

# Stage 2: Builder
FROM node:20-alpine AS builder
WORKDIR /app

# Copy dependencies from deps stage
COPY --from=deps /app/node_modules ./node_modules

# Copy all files needed for build
COPY package*.json ./
COPY tsconfig.json ./
COPY next.config.ts ./
COPY components.json ./
COPY sentry.edge.config.ts ./
COPY sentry.server.config.ts ./
COPY postcss.config.mjs ./
COPY public ./public
COPY src ./src

# Set build-time environment variables (needed for Next.js build)
ENV NEXT_PUBLIC_NODE_ENV="oss"
ENV NEXT_PUBLIC_AUTH_PROVIDER="local"
ENV NEXT_PUBLIC_DEPLOYMENT_MODE="oss"
ENV NEXT_PUBLIC_BACKEND_URL="http://localhost:8000"
ENV BACKEND_URL="http://api:8000"
ENV NEXT_TELEMETRY_DISABLED="1"

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
EXPOSE 3000

# Start the production server using the standalone Node.js server
CMD ["node", "server.js"]