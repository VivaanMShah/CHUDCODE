# One image serves the website and the API.
# Stage 1 builds the React site. Stage 2 runs FastAPI and serves that site.
# Base images come from the AWS public mirror to avoid Docker Hub rate limits.

FROM public.ecr.aws/docker/library/node:22-slim AS web
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
COPY public ./public
RUN npm run build

FROM public.ecr.aws/docker/library/python:3.14-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000 STATIC_DIR=/srv/dist
COPY requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt
COPY app ./app
COPY bundle ./bundle
COPY --from=web /web/dist ./dist
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log"]
