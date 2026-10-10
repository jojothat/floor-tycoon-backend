# Runs e2e/readout.mjs against the readout. The Playwright image ships Chromium.
FROM mcr.microsoft.com/playwright:v1.48.2-jammy
WORKDIR /e2e
RUN npm init -y >/dev/null && npm i playwright@1.48.2 >/dev/null
COPY e2e/readout.mjs ./readout.mjs
ENV READOUT_URL=http://readout
CMD ["node", "readout.mjs"]
