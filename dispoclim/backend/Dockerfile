# DispoClim backend — image multi-étapes (API + collecteur partagent l'image)
FROM node:20-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:20-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY package*.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
COPY db ./db
COPY scripts ./scripts
EXPOSE 8080
# API par défaut ; le collecteur se lance avec :  node dist/collector/run.js [hot|warm|cold]
CMD ["node", "dist/server.js"]
