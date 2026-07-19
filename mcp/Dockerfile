# node-installer MCP server — builds the TS bundle, then runs it.
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY tsconfig.json tsup.config.ts ./
COPY src/ ./src/
RUN npm run build

FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --omit=dev
COPY --from=build /app/dist/ ./dist/
# HTTP transport port (matches MCP_HTTP_PORT); stdio mode ignores it.
EXPOSE 3100
CMD ["node", "dist/index.js"]
