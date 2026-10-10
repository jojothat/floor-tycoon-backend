# Plays synthetic games through the game API (tools/seed.mjs). Dev and staging only, never production.
FROM node:20-alpine
WORKDIR /seed
COPY tools/seed.mjs tools/items.json tools/scenarios.json ./
ENV API=http://api:8000 N=300
CMD ["node", "seed.mjs"]
