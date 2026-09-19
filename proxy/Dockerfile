# Public entry point for the whole stack: TLS termination + reverse proxy.
#
# ⚠️ Версия закреплена: (а) TLS-шаблон использует `http2 on;`, который есть
# только с nginx 1.25.1 — старый базовый образ не стартует с невнятной ошибкой;
# (б) ngx_brotli собирается динамическим модулем ровно под эту версию (стадия
# nginx-mod ниже). При обновлении менять ОБЕ строки — базовый образ и ARG.
ARG NGINX_VERSION=1.27.5

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
# строками, иначе модуль не виден и brotli-директивы в conf.d роняют конфиг.
RUN sed -i "1i load_module modules/ngx_http_brotli_filter_module.so;\nload_module modules/ngx_http_brotli_static_module.so;" /etc/nginx/nginx.conf

# `envsubst` (gettext) ships in the official nginx images and is what the
# entrypoint uses to inject the domain into a template.
COPY snippets/  /etc/nginx/snippets/
COPY templates/ /templates/
# http-level config (the WebSocket upgrade map + сжатие) — conf.d is included in
# http{}. The entrypoint only ever writes conf.d/default.conf, so these survive.
COPY http.d/    /etc/nginx/conf.d/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Проверка «модуль грузится, brotli-директивы валидны» на СБОРКЕ: битый модуль
# роняет build, а не прод-контейнер на старте.
RUN nginx -t

EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]
