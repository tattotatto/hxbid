FROM node:20-alpine AS builder
WORKDIR /app

# Build args — 内网部署时可指定 npm 镜像
#   docker build --build-arg NPM_REGISTRY=https://registry.npmmirror.com ...
ARG NPM_REGISTRY=""

COPY package.json package-lock.json ./

# Configure npm registry if specified
RUN if [ -n "${NPM_REGISTRY}" ]; then \
        npm config set registry "${NPM_REGISTRY}" ; \
    fi && \
    npm ci

COPY . .
RUN npm run build

# --- Production stage ---
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
