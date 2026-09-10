FROM node:22-bookworm-slim
WORKDIR /srv
COPY package*.json ./
RUN npm ci --omit=dev
COPY *.mjs ./
CMD ["node", "index.mjs"]
