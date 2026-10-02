import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


ISSUER = "https://issuer.test/"
AUDIENCE = "datacopilot-api"


def create_rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def private_pem(key) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def public_jwk(key, kid: str) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _base64url(numbers.n),
        "e": _base64url(numbers.e),
    }


def signed_token(
    key,
    *,
    kid: str = "key-1",
    subject: str = "user-a",
    claims: dict[str, Any] | None = None,
    algorithm: str = "RS256",
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": subject,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
    }
    payload.update(claims or {})
    signing_key = private_pem(key) if algorithm == "RS256" else "x" * 32
    return jwt.encode(payload, signing_key, algorithm=algorithm, headers={"kid": kid})


class LocalJwksServer:
    def __init__(self, keys: list[dict[str, str]]) -> None:
        self._keys = keys
        self.request_count = 0
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/.well-known/jwks.json":
                    self.send_error(404)
                    return
                state.request_count += 1
                body = json.dumps({"keys": state._keys}).encode("ascii")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_port}/.well-known/jwks.json"

    def replace_keys(self, keys: list[dict[str, str]]) -> None:
        self._keys = keys

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _base64url(value: int) -> str:
    width = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(width, "big")).rstrip(b"=").decode("ascii")
