# Static hosting

## Scope

Publish only the contents of `site/` as the document root. Do not publish the
repository root, exports, raw evidence, scripts, or SQLite. These examples do
not install or reload a server. The host, TLS certificate and delivery method
must be configured by the hosting owner.

The intended public origin is `https://munchkin-wiki.ru`, configured in
`site_src/site_config.json`. Change it before building for another domain.
The logo uses `/` so local previews remain local. Canonicals and the sitemap
use the configured public origin. Force HTTP to HTTPS at the host.

## Nginx

Inside the existing HTTPS server block, after setting its document root to
the uploaded site directory:

```nginx
index index.html;
error_page 404 /404.html;
gzip on;
gzip_vary on;
gzip_min_length 1024;
gzip_types text/css application/javascript application/json application/xml image/svg+xml;

location / {
    try_files $uri $uri/ =404;
    add_header Cache-Control "no-cache";
}
```

`no-cache` permits storage but requires revalidation. This is deliberately
conservative for filenames reused at each build. CSS, JS and the search index
also include content-hash query parameters. Do not configure an immutable cache
that ignores those parameters. When publishing, upload assets/data before HTML,
or switch the whole document root atomically to a complete release.

## Apache shared hosting

If the hosting supports `AllowOverride`, copy `apache.htaccess` from this
directory to `.htaccess` in the site's document root. Enable HTTPS and HTTP
redirection through the hosting panel. Confirm that `mod_deflate` and
`mod_headers` are available; the conditional sections have no effect otherwise.

## Acceptance checks

- `/`, `/sets/`, `/cards/page/2/` and `/faq/epic-munchkin/` open over HTTPS.
- A nonexistent URL returns HTTP 404 and displays the custom error page.
- `sitemap.xml` names the intended HTTPS domain.
- The browser receives gzip for the search JSON and HTML when requesting gzip.
- Search for `Смывка` puts that FAQ first; `+5 к Сексозности` finds its card.
- After a second release, both content and search results reflect the new build.
- Directory listing is disabled and paths outside `site/` are unavailable.

References: [Nginx gzip](https://nginx.org/en/docs/http/ngx_http_gzip_module.html),
[Apache mod_deflate](https://httpd.apache.org/docs/2.4/mod/mod_deflate.html).
