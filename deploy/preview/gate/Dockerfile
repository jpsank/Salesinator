# The preview gate: zero dependencies, Node's own http/net/crypto only.
FROM node:22-slim
WORKDIR /gate
COPY *.mjs ./
USER node
CMD ["node", "gate.mjs"]
