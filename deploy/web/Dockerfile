# Mã Mở — production static web image
# Build context is the repository root:
#   docker build -f deploy/web/Dockerfile -t mamo-web:latest .
FROM nginx:1.27-alpine

# Drop the stock config; install the hardened production config.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY deploy/web/nginx.conf /etc/nginx/nginx.conf

# Copy only the static front-end surfaces (never scripts/secrets/reports/etc.).
WORKDIR /usr/share/nginx/html
COPY index.html styles.css ./
COPY atlas/       ./atlas/
COPY mapper/      ./mapper/
COPY logic-view/  ./logic-view/
COPY lab/         ./lab/
COPY js/          ./js/
COPY data/        ./data/

EXPOSE 80

# Basic container healthcheck against the health endpoint.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
