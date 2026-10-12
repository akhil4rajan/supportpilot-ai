import time

import jwt
from conftest import SECRET, auth, chat, make_client, open_session, user_token  # noqa: F401


def test_valid_session_includes_branding_and_features(make_client):
    r = open_session(make_client(), user="alice")
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["assistant_name"] == "Penny" and cfg["features"] == {"orders": True, "tickets": True}
    assert r.json()["conversation"]["user"]["authenticated"] is True


def test_unknown_tenant_key(make_client):
    assert open_session(make_client(), key="pk_nope").status_code == 404


def test_forged_and_invalid_user_tokens_rejected(make_client):
    c = make_client()
    for bad in (
        user_token("alice", secret="attacker-secret-attacker-secret-123"),   # wrong signing key
        user_token("alice", aud="something-else"),                            # wrong audience
        user_token("alice", ttl=-10),                                         # expired
        user_token("alice", ttl=7200),                                        # lifetime > 1h
        jwt.encode({"sub": "alice", "aud": "supportpilot", "exp": int(time.time()) + 60}, "", algorithm="none"),
        "not-a-jwt",
    ):
        assert open_session(c, token=bad).status_code == 401, bad


def test_token_from_another_tenant_is_not_accepted(make_client):
    c = make_client()
    globex_secret = "globex-secret-with-enough-length-1234"
    tok = user_token("alice", secret=globex_secret)
    assert open_session(c, token=tok, key="pk_acme_demo").status_code == 401
    assert open_session(c, token=tok, key="pk_globex", origin="https://globex.example").status_code == 200


def test_origin_allow_list_enforced_in_prod(make_client):
    c = make_client(env="prod")
    assert open_session(c, origin="https://evil.example").status_code == 403
    assert open_session(c).status_code == 403  # no Origin header in prod
    assert open_session(c, origin="http://localhost:5173").status_code == 200


def test_prod_has_no_dev_token_endpoint(make_client):
    c = make_client(env="prod")
    assert c.post("/v1/dev/user-token", json={"tenant_id": "acme", "user_id": "x"}).status_code == 404


def test_endpoints_require_a_valid_session_token(make_client):
    c = make_client()
    assert c.post("/v1/widget/messages", json={"content": "hi"}).status_code == 401
    assert c.post("/v1/widget/messages", json={"content": "hi"}, headers=auth("garbage")).status_code == 401


def test_signed_in_user_resumes_same_conversation(make_client):
    c = make_client()
    first = open_session(c, user="alice").json()
    chat(c, first["token"], "how long is shipping?")
    second = open_session(c, user="alice").json()
    assert second["conversation"]["id"] == first["conversation"]["id"]
    assert len(second["messages"]) == 2
    other = open_session(c, user="bob").json()
    assert other["conversation"]["id"] != first["conversation"]["id"]


def test_anonymous_resume_needs_the_previous_token(make_client):
    c = make_client()
    a = open_session(c, user=None).json()
    again = c.post("/v1/widget/sessions", json={"public_key": "pk_acme_demo", "resume_token": a["token"]}).json()
    assert again["conversation"]["id"] == a["conversation"]["id"]
    fresh = open_session(c, user=None).json()
    assert fresh["conversation"]["id"] != a["conversation"]["id"]


def test_message_limits(make_client):
    c = make_client()
    tok = open_session(c).json()["token"]
    assert c.post("/v1/widget/messages", json={"content": "x" * 5000}, headers=auth(tok)).status_code == 413
    assert c.post("/v1/widget/messages", json={"content": "   "}, headers=auth(tok)).status_code == 422


def test_public_config_endpoint(make_client):
    c = make_client(env="prod")
    ok = c.get("/v1/widget/config", params={"public_key": "pk_acme_demo"}, headers={"origin": "http://localhost:5173"})
    assert ok.status_code == 200 and ok.json()["branding"]["title"] == "Acme Support"
    assert c.get("/v1/widget/config", params={"public_key": "pk_acme_demo"}, headers={"origin": "https://evil.example"}).status_code == 404
    assert c.get("/v1/widget/config", params={"public_key": "nope"}).status_code == 404
