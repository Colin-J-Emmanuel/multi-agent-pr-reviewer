import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request

SECRET = os.environ["GITHUB_WEBHOOK_SECRET"].encode()
URL = "http://localhost:8000/webhook"


def send(label: str, body: bytes, headers: dict) -> None:
    req = urllib.request.Request(URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"{label:16} -> {resp.status} {resp.read().decode()}")
    except urllib.error.HTTPError as e:            # 4xx/5xx land here instead of raising
        print(f"{label:16} -> {e.code} {e.read().decode()}")


payload = {
    "action": "opened",
    "pull_request": {"number": 2, "head": {"sha": "abc123"}},
    "repository": {"full_name": "Colin-J-Emmanuel/multi-agent-pr-reviewer"},
}
body = json.dumps(payload).encode()
sig = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
headers = {
    "Content-Type": "application/json",
    "X-GitHub-Event": "pull_request",
    "X-GitHub-Delivery": "test-delivery-002",
    "X-Hub-Signature-256": sig,
}

send("valid",        body, headers)                                    # expect 202
send("tampered",     body + b'{"x":1}', headers)                       # expect 403
send("no-signature", body, {k: v for k, v in headers.items()
                            if k != "X-Hub-Signature-256"})            # expect 403