import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services.gsc import make_assertion


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_make_assertion_is_a_signed_jwt():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    jwt = make_assertion({"client_email": "svc@example.iam", "private_key": pem.decode()}, now=1000)
    header, claims, sig = jwt.split(".")
    assert json.loads(_b64d(header)) == {"alg": "RS256", "typ": "JWT"}
    c = json.loads(_b64d(claims))
    assert c["iss"] == "svc@example.iam" and c["exp"] == 4600 and "webmasters" in c["scope"]
    assert len(_b64d(sig)) == 256
