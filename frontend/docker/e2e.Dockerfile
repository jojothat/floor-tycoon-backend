# Runs e2e/play.mjs against the stack. The Playwright image ships Chromium.
FROM mcr.microsoft.com/playwright:v1.48.2-jammy
WORKDIR /e2e
RUN npm init -y >/dev/null && npm i playwright@1.48.2 >/dev/null
COPY e2e/play.mjs ./play.mjs
ENV BASE_URL=http://web
CMD ["node", "play.mjs"]
