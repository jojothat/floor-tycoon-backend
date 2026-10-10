# Reference mock of the backend contract. For frontend tests only; in-memory, no real data.
FROM node:20-alpine
WORKDIR /app
COPY mock-api/ ./
ENV PORT=8000
EXPOSE 8000
CMD ["node", "server.mjs"]
