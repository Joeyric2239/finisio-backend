"""
FINISIO CLEANS — Passenger WSGI Entry Point
Bridges cPanel's Passenger system with the pure-Python HTTP server.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import json
import re
from urllib.parse import urlparse, parse_qs
import db
import logic

db.init_db()

from server import ROUTES, ok, err, not_found, forbidden, created

def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path   = environ.get("PATH_INFO", "/").rstrip("/") or "/"
    qs     = environ.get("QUERY_STRING", "")
    params = parse_qs(qs)

    if path.startswith("/api"):
        path = path[4:] or "/"

    cors_headers = [
        ("Access-Control-Allow-Origin",  "*"),
        ("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
    ]

    if method == "OPTIONS":
        start_response("204 No Content", cors_headers)
        return [b""]

    body = {}
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        if length > 0:
            raw = environ["wsgi.input"].read(length).decode("utf-8")
            body = json.loads(raw)
    except Exception:
        pass

    for route_method, pattern, handler in ROUTES:
        if route_method != method:
            continue
        m = pattern.match(path)
        if m:
            kwargs = m.groupdict()
            try:
                status_code, payload = handler(body=body, params=params, **kwargs)
            except Exception as e:
                status_code, payload = 500, {"ok": False, "error": str(e)}
            break
    else:
        status_code, payload = 404, {"ok": False, "error": f"Route not found: {method} {path}"}

    response_body = json.dumps(payload, default=str).encode("utf-8")
    status_map = {
        200: "200 OK", 201: "201 Created", 204: "204 No Content",
        400: "400 Bad Request", 403: "403 Forbidden",
        404: "404 Not Found", 500: "500 Internal Server Error",
    }
    status_str = status_map.get(status_code, f"{status_code} Unknown")

    headers = cors_headers + [
        ("Content-Type",   "application/json"),
        ("Content-Length", str(len(response_body))),
    ]
    start_response(status_str, headers)
    return [response_body]
