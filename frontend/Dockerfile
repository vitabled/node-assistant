# ⚠️ NGINX_VERSION объявлен ПЕРЕД первым FROM: только так его видно в
# последующих строках FROM. При обновлении nginx менять одну эту строку —
# модуль brotli собирается из исходников ровно этой версии.
ARG NGINX_VERSION=1.31.6

FROM node:20-alpine AS builder

ARG VITE_API_BASE=/api
ARG VITE_WS_BASE=/ws
ENV VITE_API_BASE=$VITE_API_BASE
ENV VITE_WS_BASE=$VITE_WS_BASE

WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# ─── brotli как динамический модуль к ТОЙ ЖЕ версии nginx, что в финальном
# образе ────────────────────────────────────────────────────────────────────
# nginx:alpine не содержит ngx_brotli, поэтому модуль собирается из исходников
# nginx ровно той же версии с --with-compat (официальные сборки nginx собраны
# с --with-compat, поэтому такой модуль к ним подходит). Версия объявлена выше,
# до первого FROM — иначе ${NGINX_VERSION} в FROM пуст и сборка падает.
FROM nginx:${NGINX_VERSION}-alpine AS nginx-mod
ARG NGINX_VERSION
RUN apk add --no-cache --virtual .build-deps build-base cmake git linux-headers pcre2-dev zlib-dev openssl-dev \
 && cd /tmp \
 && curl -fsSL https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz -o nginx.tar.gz \
 && tar xzf nginx.tar.gz \
 && git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/google/ngx_brotli.git \
 && cd ngx_brotli/deps/brotli && mkdir -p out && cd out \
 && cmake -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DCMAKE_C_FLAGS="-Ofast -fPIC" .. > /dev/null \
 && make -j$(nproc) > /dev/null \
 && cd /tmp/nginx-${NGINX_VERSION} \
 && ./configure --with-compat --add-dynamic-module=../ngx_brotli > /dev/null \
 && make -j$(nproc) modules \
 && mkdir -p /out && cp objs/ngx_http_brotli_*.so /out/ && ls -la /out/

FROM nginx:${NGINX_VERSION}-alpine
ARG NGINX_VERSION

COPY --from=nginx-mod /out/*.so /usr/lib/nginx/modules/
# load_module допустим только в main-контексте nginx.conf — вставляем первыми
# строками (иначе модуль не виден, а конфиг с brotli-директивами не проходит).
RUN sed -i "1i load_module modules/ngx_http_brotli_filter_module.so;\nload_module modules/ngx_http_brotli_static_module.so;" /etc/nginx/nginx.conf

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-compress.conf /etc/nginx/conf.d/10-compress.conf

# Пред-сжатие белых ассетов на этапе сборки: brotli_static/gzip_static отдают
# готовые файлы (q11/gzip -9) вместо сжатия на каждый запрос — меньше байт на
# проводе и ноль CPU в рантайме. Хешированные имена делают .br/.gz вечно
# валидными вместе с исходником.
RUN apk add --no-cache --virtual .brotli-build brotli \
 && find /usr/share/nginx/html -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' \) -exec brotli -q 11 -f {} \; \
 && find /usr/share/nginx/html -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' \) -exec gzip -9 -k -n -f {} \; \
 && apk del .brotli-build \
 && du -sh /usr/share/nginx/html

# Проверка конфига И загрузки модуля проходит на СБОРКЕ: битый модуль/конфиг
# роняет build, а не контейнер на проде.
RUN nginx -t

EXPOSE 80
