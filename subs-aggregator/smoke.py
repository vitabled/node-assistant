"""End-to-end smoke of the subs-aggregator server (stdlib only, no docker needed
— app.py is `python app.py`, so host-python behaviour == container behaviour).

Starts a mock upstream subscription + a mock agg-source, runs the aggregator as a
subprocess, and asserts: /sub returns a tagged combined subscription; breaking the
upstream surfaces an error in /status WITHOUT re-hammering; /refresh re-fetches.

Run: python subs-aggregator/smoke.py
"""
import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM_PORT = 8791
SOURCE_PORT = 8792
AGG_PORT = 8793

# The mock upstream serves a base64 subscription of 2 vless configs.
_UPSTREAM = {"body": base64.b64encode(b"vless://a@h:443#N1\nvless://b@h:443#N2").decode(),
             "hits": 0, "fail": False}


class UpstreamH(BaseHTTPRequestHandler):
    def do_GET(self):
        _UPSTREAM["hits"] += 1
        if _UPSTREAM["fail"]:
            self.send_response(500); self.end_headers(); self.wfile.write(b"down"); return
        b = _UPSTREAM["body"].encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a): pass


# The source list is mutable so the smoke can test cache eviction (drop a sub).
_SOURCE = {"items": [{"account_id": "acc1", "sub_id": "sub1",
                      "url": f"http://127.0.0.1:{UPSTREAM_PORT}/s"}]}


class SourceH(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(_SOURCE["items"]).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass


def _serve(handler, port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _get(path):
    return urllib.request.urlopen(f"http://127.0.0.1:{AGG_PORT}{path}", timeout=5).read().decode()


def _decoded_sub():
    """/sub base64 → the URL-decoded config text (fragments un-percent-encoded)."""
    text = base64.b64decode(_get("/sub") + "==").decode()
    return urllib.parse.unquote(text)


def _post(path, body=b""):
    req = urllib.request.Request(f"http://127.0.0.1:{AGG_PORT}{path}", data=body, method="POST")
    return urllib.request.urlopen(req, timeout=5).read().decode()


def main():
    _serve(UpstreamH, UPSTREAM_PORT)
    _serve(SourceH, SOURCE_PORT)

    env = dict(os.environ,
               AGG_SOURCE_URL=f"http://127.0.0.1:{SOURCE_PORT}/agg",
               PORT=str(AGG_PORT),
               ALLOW_PRIVATE_HOSTS="1")  # mock upstream is on 127.0.0.1
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "app.py")], env=env)
    try:
        # wait for aggregator to come up
        for _ in range(30):
            try:
                if _get("/health") == "ok":
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise SystemExit("aggregator did not start")

        # 1. /sub returns a tagged combined subscription
        decoded = _decoded_sub()
        assert "acc1:sub1|N1" in decoded, decoded
        assert "acc1:sub1|N2" in decoded, decoded
        print("  ok  /sub returns tagged aggregate")

        # 2. break upstream; the cached configs are still served, /status shows error,
        #    and the aggregator does NOT re-hit the (now failing) upstream on /sub.
        _UPSTREAM["fail"] = True
        hits_before = _UPSTREAM["hits"]
        assert "acc1:sub1|N1" in _decoded_sub()  # cached → no upstream call
        assert _UPSTREAM["hits"] == hits_before, "aggregator hammered a cached upstream"
        print("  ok  broken upstream not re-hit (cache serves last-good)")

        # 3. /refresh clears the cache → next /sub re-fetches (hits the upstream, now failing)
        _post("/refresh", b"{}")
        status = json.loads(_get("/status"))
        errs = [s for s in status["subscriptions"] if s["error"]]
        assert errs and "acc1:sub1" in errs[0]["key"], status
        print("  ok  /refresh re-fetches; failing upstream surfaces error in /status")

        # 4. upstream recovers → refresh → /sub healthy again
        _UPSTREAM["fail"] = False
        _post("/refresh", b"{}")
        assert "acc1:sub1|N1" in _decoded_sub()
        print("  ok  recovery after refresh")

        # 5. sub removed from the source → drops out of /sub AND its cache entry
        #    is evicted (import the module in-process to inspect _CACHE is not
        #    possible across the subprocess; assert via the served output).
        _SOURCE["items"] = []
        assert _decoded_sub().strip() == ""  # nothing aggregated
        print("  ok  removed sub drops from /sub (source-driven)")

        # 6. bring it back → reappears
        _SOURCE["items"] = [{"account_id": "acc1", "sub_id": "sub1",
                             "url": f"http://127.0.0.1:{UPSTREAM_PORT}/s"}]
        assert "acc1:sub1|N1" in _decoded_sub()
        print("  ok  re-added sub reappears")

        print("SMOKE OK")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
