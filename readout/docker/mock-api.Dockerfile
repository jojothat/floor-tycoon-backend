# Reference mock of the game + read API. For tests only; in-memory, no real data.
FROM node:20-alpine
WORKDIR /app
COPY mock-api/ ./
ENV PORT=8000 READ_TOKEN=dev-viewer
EXPOSE 8000
CMD ["node", "server.mjs"]
