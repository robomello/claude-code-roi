#!/usr/bin/env python3
"""Tiny HTTP server for ROI dashboard. Serves static files + /api/refresh endpoint."""

import http.server
import json
import os
import subprocess
import socketserver

PORT = 8040
DIR = os.path.dirname(os.path.abspath(__file__))
ROI_SCRIPT = os.environ.get("ROI_SCRIPT", os.path.join(os.path.dirname(DIR), "roi.py"))
DATA_FILE = os.path.join(DIR, "data.json")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_POST(self):
        if self.path == "/api/refresh":
            try:
                # Read optional JSON body for date range
                content_len = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(content_len)) if content_len > 0 else {}

                cmd = ["python3", ROI_SCRIPT, "--json", "--full"]
                if body.get("since"):
                    cmd.extend(["--since", body["since"]])
                if body.get("until"):
                    cmd.extend(["--until", body["until"]])
                if body.get("days"):
                    cmd.extend(["--days", str(body["days"])])

                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    # Only save to data.json if unfiltered (all-time)
                    if not body.get("since") and not body.get("days"):
                        with open(DATA_FILE, "w") as f:
                            f.write(result.stdout)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(result.stdout.encode())
                else:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": result.stderr}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def end_headers(self):
        if self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        print("Generating initial data.json...")
        result = subprocess.run(
            ["python3", ROI_SCRIPT, "--json", "--full"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            with open(DATA_FILE, "w") as f:
                f.write(result.stdout)
            print("data.json created")

    class ReuseTCPServer(socketserver.TCPServer):
        allow_reuse_address = True
        allow_reuse_port = True

    with ReuseTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"ROI Dashboard serving on port {PORT}")
        httpd.serve_forever()
