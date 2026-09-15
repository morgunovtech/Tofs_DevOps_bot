"""Which hosting a site lives on, guessed from response headers — so an
alert can say «открыть панель Cloudflare» with a real link instead of
«open your hosting panel». Pure function; the result is remembered per site."""

from collections.abc import Mapping

# (key, human label, panel URL)
PANELS: dict[str, tuple[str, str]] = {
    "cloudflare": ("Cloudflare", "https://dash.cloudflare.com/"),
    "vercel": ("Vercel", "https://vercel.com/dashboard"),
    "netlify": ("Netlify", "https://app.netlify.com/"),
    "github": ("GitHub Pages", "https://github.com/"),
    "railway": ("Railway", "https://railway.com/dashboard"),
    "render": ("Render", "https://dashboard.render.com/"),
    "fly": ("Fly.io", "https://fly.io/dashboard"),
    "heroku": ("Heroku", "https://dashboard.heroku.com/"),
    "timeweb": ("Timeweb", "https://timeweb.cloud/my"),
    "beget": ("Beget", "https://cp.beget.com/"),
}


def detect_hosting(headers: Mapping[str, str] | None) -> str | None:
    """Hosting key from HTTP response headers, None when unknown."""
    if not headers:
        return None
    h = {k.lower(): str(v).lower() for k, v in headers.items()}
    server = h.get("server", "")
    if "x-vercel-id" in h or "vercel" in server:
        return "vercel"
    if "x-nf-request-id" in h or "netlify" in server:
        return "netlify"
    if "x-github-request-id" in h or "github.com" in server:
        return "github"
    if any(k.startswith("x-railway") for k in h) or "railway" in server:
        return "railway"
    if "x-render-origin-server" in h or "rndr-id" in h or "render" in server:
        return "render"
    if "fly-request-id" in h:
        return "fly"
    if "vegur" in h.get("via", "") or "heroku" in server:
        return "heroku"
    if "timeweb" in server or "x-timeweb" in " ".join(h):
        return "timeweb"
    if "beget" in server or "x-beget" in " ".join(h):
        return "beget"
    if "cf-ray" in h or "cloudflare" in server:
        return "cloudflare"
    return None


def label(key: str | None) -> str | None:
    return PANELS[key][0] if key in PANELS else None


def panel_url(key: str | None) -> str | None:
    return PANELS[key][1] if key in PANELS else None
