"""安全响应头：主站收紧，预览页隔离。

两套 CSP 的分工：
- 主站（`/`、静态资源）：只允许同源脚本与样式，禁止内联脚本、禁止被嵌套；
- 预览（`/api/tasks/**`）：生成代码需要内联 CSS/JS 与 data URI，CSP 由 `api.PREVIEW_CSP` 单独下发，
  配合 iframe `sandbox="allow-scripts"`（无 allow-same-origin）把生成代码关在独立源里。

`/api/docs`（Swagger UI）依赖 CDN 资源，必须排除在严格 CSP 之外，否则文档页会白屏。
"""

from __future__ import annotations

from fastapi import FastAPI, Request, Response

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

SITE_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "object-src 'none'"
)

# 预览与接口文档不套主站 CSP：前者有专门的宽松策略，后者需要 CDN
CSP_EXEMPT_PREFIXES = ("/api/tasks/", "/api/docs")


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)

        for name, value in BASE_HEADERS.items():
            response.headers.setdefault(name, value)

        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html") and not path.startswith(CSP_EXEMPT_PREFIXES):
            response.headers.setdefault("Content-Security-Policy", SITE_CSP)
            response.headers.setdefault("X-Frame-Options", "DENY")
            # 主站 HTML 不缓存：部署后刷新即可拿到新版本，避免看到旧页面以为发布失败
            response.headers.setdefault("Cache-Control", "no-store")

        return response
