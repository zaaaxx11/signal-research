"""``/robots.txt`` and ``/sitemap.xml`` at the root, built from the request.

Two documents a crawler asks for at the origin root, and both need **absolute**
URLs — a sitemap's ``<loc>`` must be a full URL or the crawler ignores the file,
and ``robots.txt`` names its sitemap the same way. That is why they used to be
static files carrying the deployment host: a host committed to the repository,
and a document that had to be edited to serve the box under another name.

They are built here instead, from the host the request arrived with:

* nothing in the repository names the deployment host, so a preview box, a second
  domain or a renamed tunnel all answer correctly without an edit;
* :data:`PAGES` is the **single** source both documents read, so a page cannot
  appear in the sitemap and go missing from the crawl policy;
* the shell is mounted at ``/app`` from the built bundle, and stays free to move.

Served at the root, not under ``/app``: files under ``web/public`` are copied
into the shell's own directory and therefore answered at ``/app/robots.txt``,
which no crawler asks for.

What this cannot fix: an edge that answers ``/robots.txt`` itself shadows this
one — Cloudflare's managed content-signals policy does, so the origin is correct
and unreachable. That is an operator setting, not a code path.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

router = APIRouter(include_in_schema=False)

#: Every page a crawler may index, with the priority the sitemap states. The order
#: is the document's own: the flagship pages first. One list, two readers.
PAGES: tuple[tuple[str, str], ...] = (
    ("/app/", "1.0"),
    ("/app/screener", "0.9"),
    ("/app/screener/etf", "0.8"),
    ("/app/map", "0.9"),
    ("/app/charts", "0.8"),
    ("/app/news", "0.8"),
    ("/app/groups", "0.7"),
    ("/app/insider", "0.7"),
    ("/app/futures", "0.6"),
    ("/app/forex", "0.6"),
    ("/app/crypto", "0.7"),
    ("/app/calendar", "0.6"),
    ("/app/watchlist", "0.5"),
    ("/app/portfolio", "0.5"),
    ("/app/compare", "0.5"),
)

#: The sitemap protocol version the namespace identifies. A spec identifier, not
#: an origin: it is never rewritten from the request.
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

#: Short and Host-keyed: both documents are cheap, and a CDN keys its cache by
#: host, so one host's answer is never handed to another.
CACHE_CONTROL = "public, max-age=300"


def origin_of(request: Request) -> str:
    """The origin this request arrived on, without a trailing slash.

    ``base_url`` is Starlette's reading of the request's own scheme and host —
    which is what makes these documents follow the domain instead of declaring
    one.
    """

    return str(request.base_url).rstrip("/")


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots(request: Request) -> Response:
    """Allow every crawler, and name the sitemap on the host that asked."""

    body = (
        "# Every page is public; the API is not for crawlers.\n"
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {origin_of(request)}/sitemap.xml\n"
    )
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"cache-control": CACHE_CONTROL},
    )


@router.get("/sitemap.xml")
async def sitemap(request: Request) -> Response:
    """The page list, with every ``<loc>`` on the host that asked."""

    origin = origin_of(request)
    rows = "".join(
        f"  <url><loc>{origin}{path}</loc><priority>{priority}</priority></url>\n"
        for path, priority in PAGES
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{SITEMAP_NS}">\n{rows}</urlset>\n'
    )
    return Response(
        body,
        media_type="application/xml",
        headers={"cache-control": CACHE_CONTROL},
    )


__all__ = ["CACHE_CONTROL", "PAGES", "SITEMAP_NS", "origin_of", "robots", "router", "sitemap"]
