from conftest import chat, make_client, open_session  # noqa: F401

H = {"X-Admin-Key": "admin-key"}


def test_admin_requires_key(make_client):
    c = make_client()
    assert c.get("/v1/admin/tenants").status_code == 401
    assert c.get("/v1/admin/tenants", headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert {t["id"] for t in c.get("/v1/admin/tenants", headers=H).json()["tenants"]} == {"acme", "globex"}


def test_new_document_becomes_answerable_immediately(make_client):
    c = make_client()
    tok = open_session(c, user=None).json()["token"]
    q = "what are the store hours for the flagship boutique in Zurich"
    assert "couldn't find" in chat(c, tok, q)["messages"][0]["content"]
    doc = {"filename": "boutique.md", "content": "# Flagship boutique\n\nThe Zurich flagship boutique opens daily from 10am to 8pm."}
    assert c.post("/v1/admin/tenants/acme/knowledge", json=doc, headers=H).status_code == 200
    assert "10am to 8pm" in chat(c, tok, q)["messages"][0]["content"]
    assert c.delete("/v1/admin/tenants/acme/knowledge/boutique.md", headers=H).status_code == 200


def test_upload_rejects_path_traversal_and_bad_types(make_client):
    c = make_client()
    for name in ("../evil.md", "a/b.md", "..\\x.md", "notes.exe", ".hidden.md"):
        r = c.post("/v1/admin/tenants/acme/knowledge", json={"filename": name, "content": "x"}, headers=H)
        assert r.status_code == 422, name
    assert c.delete("/v1/admin/tenants/acme/knowledge/..%2Ftenant.yaml", headers=H).status_code == 404


def test_knowledge_is_isolated_per_tenant(make_client):
    c = make_client()
    tok = open_session(c, user=None, key="pk_globex", origin="https://globex.example").json()["token"]
    # globex has no KB: acme's refund policy must not leak
    r = chat(c, tok, "how long do refunds take?")
    assert "5-7" not in r["messages"][0]["content"]
