#!/usr/bin/env python3
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockTxHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/mcp":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}

        method_name = payload.get("params", {}).get("name")
        rpc_id = payload.get("id", "mock")

        if method_name == "getTransactionsForPeriod":
            body = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "fact_by_category": {
                                        "5411": 1800,
                                        "5812": 1200,
                                        "5912": 400,
                                    }
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                },
            }
        else:
            body = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"content": [{"type": "text", "text": "{}"}]},
            }

        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


def main():
    server = HTTPServer(("127.0.0.1", 9200), MockTxHandler)
    print("mock tx-agent listening on http://127.0.0.1:9200/mcp", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
