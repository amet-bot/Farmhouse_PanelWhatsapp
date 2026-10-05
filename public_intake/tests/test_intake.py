"""
Buzón público del formulario del colaborador. Lo que se prueba es lo que lo hace seguro: solo se puede
enviar con una invitación vigente de un solo uso; los enlaces inválidos, vencidos o usados responden
igual; el servicio solo guarda sobres cifrados que no puede abrir; la administración (solo Farmhouse
Link) exige una clave larga y se bloquea ante intentos; y la página y las respuestas llevan una política
de seguridad estricta.
"""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

import main as intake_main


def _envelope(**over):
    env = {"v": 1, "epk": "A" * 87, "salt": "B" * 22, "iv": "C" * 16, "ct": "D" * 120}
    env.update(over)
    return env


def _new_invite(client, admin, hours=24, label="Ana Pérez"):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    exp = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    res = client.post("/admin/invites", json={"token_hash": token_hash, "label": label, "expires_at": exp}, headers=admin)
    assert res.status_code == 201, res.text
    return token, token_hash


def _submit(client, token, **over):
    return client.post("/api/submit", json={"token": token, "envelope": _envelope(), **over})


# ---------------- público ----------------
def test_check_and_submit_with_a_valid_invitation(client, admin):
    token, h = _new_invite(client, admin)
    check = client.post("/api/check", json={"token": token})
    assert check.status_code == 200 and check.json() == {"ok": True, "label": "Ana Pérez"}
    res = _submit(client, token)
    assert res.status_code == 201 and res.json() == {"ok": True}
    subs = client.get("/admin/submissions", headers=admin).json()
    assert len(subs) == 1 and subs[0]["invite_hash"] == h and subs[0]["envelope"]["ct"] == "D" * 120


def test_the_service_stores_the_envelope_and_nothing_readable(client, admin):
    token, _ = _new_invite(client, admin)
    _submit(client, token)
    db = intake_main.SessionLocal()
    try:
        row = db.query(intake_main.Submission).one()
        assert json.loads(row.envelope) == _envelope()
        assert set(json.loads(row.envelope)) == {"v", "epk", "salt", "iv", "ct"}
    finally:
        db.close()


def test_invitation_is_single_use(client, admin):
    token, _ = _new_invite(client, admin)
    assert _submit(client, token).status_code == 201
    assert _submit(client, token).status_code == 404
    assert client.post("/api/check", json={"token": token}).status_code == 404
    assert len(client.get("/admin/submissions", headers=admin).json()) == 1


def test_expired_used_and_unknown_links_answer_identically(client, admin):
    expired, h = _new_invite(client, admin)
    db = intake_main.SessionLocal()
    inv = db.query(intake_main.Invite).filter(intake_main.Invite.token_hash == h).one()
    inv.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    db.commit()
    db.close()
    used, _ = _new_invite(client, admin)
    _submit(client, used)
    responses = [client.post("/api/check", json={"token": t}) for t in (expired, used, "x" * 43)]
    assert {r.status_code for r in responses} == {404}
    assert len({r.text for r in responses}) == 1
    assert _submit(client, expired).text == _submit(client, "x" * 43).text


def test_bad_envelope_shape_is_rejected_without_burning_the_invitation(client, admin):
    token, _ = _new_invite(client, admin)
    for bad in (_envelope(v=2), _envelope(ct="no es base64!!"), _envelope(epk="corto"), _envelope(ct="D" * 40_000)):
        assert _submit(client, token, envelope=bad).status_code in (413, 422)
    assert client.post("/api/submit", json={"token": token}).status_code == 422             # sin sobre
    assert client.post("/api/submit", json={"token": token, "envelope": {"first_name": "Ana"}}).status_code == 422   # datos en claro: no se aceptan
    assert _submit(client, token).status_code == 201


def test_honeypot_silently_discards_bots_and_keeps_the_invitation(client, admin):
    token, _ = _new_invite(client, admin)
    bot = _submit(client, token, website="http://spam.example")
    assert bot.status_code == 201 and bot.json() == {"ok": True}
    assert client.get("/admin/submissions", headers=admin).json() == []
    assert _submit(client, token).status_code == 201


def test_oversized_bodies_are_rejected(client):
    big = client.post("/api/submit", content=b"{" + b" " * 60_000 + b"}", headers={"Content-Type": "application/json"})
    assert big.status_code == 413


def test_guessing_tokens_gets_rate_limited(client):
    for _ in range(intake_main.PUBLIC_MAX_BAD_TOKENS):
        assert client.post("/api/check", json={"token": "a" * 43}).status_code == 404
    assert client.post("/api/check", json={"token": "a" * 43}).status_code == 429


def test_request_flood_gets_rate_limited(client):
    codes = [client.get("/api/public-key").status_code for _ in range(intake_main.PUBLIC_MAX_REQUESTS + 3)]
    assert codes[0] == 200 and codes[-1] == 429


def test_public_key_is_served_and_missing_key_fails_closed(client, monkeypatch):
    assert client.get("/api/public-key").json()["key"].startswith("BPublicKey")
    monkeypatch.setenv("PUBLIC_KEY", "")
    assert client.get("/api/public-key").status_code == 503


# ---------------- administración (solo Farmhouse Link) ----------------
def test_admin_requires_the_key(client, admin):
    for call in (lambda h: client.get("/admin/submissions", headers=h),
                 lambda h: client.delete("/admin/submissions/1", headers=h),
                 lambda h: client.delete("/admin/invites/" + "0" * 64, headers=h),
                 lambda h: client.post("/admin/invites", json={}, headers=h)):
        intake_main._admin_bad.clear()      # el bloqueo por intentos fallidos se prueba aparte
        assert call({}).status_code == 401
        assert call({"Authorization": "Bearer otra-clave"}).status_code == 401
        assert call({"Authorization": "Basic " + "a" * 48}).status_code == 401


def test_admin_is_off_without_a_long_key(client, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "corta")
    assert client.get("/admin/submissions", headers={"Authorization": "Bearer corta"}).status_code == 503
    monkeypatch.setenv("ADMIN_KEY", "")
    assert client.get("/admin/submissions", headers={"Authorization": "Bearer "}).status_code == 503


def test_admin_key_guessing_gets_rate_limited(client, admin):
    for _ in range(intake_main.ADMIN_MAX_FAILURES):
        assert client.get("/admin/submissions", headers={"Authorization": "Bearer mala"}).status_code == 401
    assert client.get("/admin/submissions", headers={"Authorization": "Bearer mala"}).status_code == 429
    assert client.get("/admin/submissions", headers=admin).status_code == 429      # desde esa IP, ni la correcta pasa mientras dure el bloqueo


def test_ack_deletes_the_submission(client, admin):
    token, _ = _new_invite(client, admin)
    _submit(client, token)
    sid = client.get("/admin/submissions", headers=admin).json()[0]["id"]
    assert client.delete(f"/admin/submissions/{sid}", headers=admin).status_code == 204
    assert client.get("/admin/submissions", headers=admin).json() == []


def test_revoking_an_invite_kills_the_link(client, admin):
    token, h = _new_invite(client, admin)
    assert client.delete(f"/admin/invites/{h}", headers=admin).status_code == 204
    assert client.post("/api/check", json={"token": token}).status_code == 404
    assert client.delete("/admin/invites/no-es-un-hash", headers=admin).status_code == 422


def test_invite_registration_is_validated(client, admin):
    ok_exp = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    assert client.post("/admin/invites", json={"token_hash": "no-hash", "expires_at": ok_exp}, headers=admin).status_code == 422
    h = "a" * 64
    assert client.post("/admin/invites", json={"token_hash": h, "expires_at": ok_exp}, headers=admin).status_code == 201
    assert client.post("/admin/invites", json={"token_hash": h, "expires_at": ok_exp}, headers=admin).status_code == 409
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    far = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
    for exp in (past, far):
        assert client.post("/admin/invites", json={"token_hash": "b" * 64, "expires_at": exp}, headers=admin).status_code == 422


def test_unclaimed_submissions_are_purged(client, admin):
    token, _ = _new_invite(client, admin)
    _submit(client, token)
    db = intake_main.SessionLocal()
    row = db.query(intake_main.Submission).one()
    row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=intake_main.SUBMISSION_RETENTION_DAYS + 1)
    db.commit()
    db.close()
    assert client.get("/admin/submissions", headers=admin).json() == []


# ---------------- página y cabeceras ----------------
def test_page_has_a_strict_security_policy(client):
    res = client.get("/colaborador")
    assert res.status_code == 200
    csp = res.headers["content-security-policy"]
    for directive in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "frame-ancestors 'none'", "base-uri 'none'", "form-action 'none'"):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert res.headers["x-frame-options"] == "DENY" and res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["x-content-type-options"] == "nosniff" and "no-store" in res.headers["cache-control"]
    assert "noindex" in res.headers["x-robots-tag"]
    html = res.text
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "scripts en línea"
    assert not re.search(r"\sstyle\s*=", html), "estilos en línea"
    assert not re.search(r"\son\w+\s*=", html), "manejadores de eventos en línea"
    assert not re.search(r"(src|href)=[\"']https?://", html), "recursos de terceros"
    assert 'autocomplete="off"' in html


def test_assets_are_served_only_from_the_allowlist(client):
    for name in ("colaborador.css", "colaborador.js", "crypto.js", "logo.png"):
        assert client.get(f"/assets/{name}").status_code == 200
    for name in ("main.py", "intake.db", "../main.py", "colaborador.html", "%2e%2e/main.py"):
        assert client.get(f"/assets/{name}").status_code == 404


def test_api_responses_are_not_cacheable_and_have_no_cors(client, admin):
    res = client.get("/api/public-key", headers={"Origin": "https://otro-sitio.example"})
    assert "no-store" in res.headers["cache-control"]
    assert "access-control-allow-origin" not in {k.lower() for k in res.headers}


def test_docs_and_schema_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_the_service_has_no_link_to_the_central_system():
    """Aislamiento: este servicio no importa nada de Farmhouse Link ni guarda llaves privadas."""
    source = open(intake_main.__file__, encoding="utf-8").read()
    assert not re.search(r"^\s*(from|import)\s+(backend|models|routers|services|security|config)\b", source, re.M)
    assert "PRIVATE" not in source.upper()


# ---------------- formulario ABIERTO (el enlace que se publica, p. ej. en Instagram) ----------------
import hashlib as _hashlib
import shutil
import subprocess
import time
from pathlib import Path


def _solve(challenge, bits=None):
    """Lo que hace el navegador: busca el número que da el hash con los ceros pedidos."""
    bits = bits or intake_main.POW_BITS
    n = 0
    while intake_main._leading_zero_bits(_hashlib.sha256(f"{challenge}:{n}".encode()).digest()) < bits:
        n += 1
    return str(n)


def _open(client, admin, enabled=True):
    res = client.put("/admin/open-form", json={"enabled": enabled}, headers=admin)
    assert res.status_code == 200, res.text
    return res.json()


def _old_challenge(age=40):
    return intake_main.new_challenge(now=time.time() - age)


def _submit_open(client, challenge=None, nonce=None, **over):
    challenge = challenge or _old_challenge()
    body = {"challenge": challenge, "nonce": nonce or _solve(challenge), "envelope": _envelope(), **over}
    return client.post("/api/submit-open", json=body)


def test_open_form_is_closed_by_default(client):
    assert client.get("/api/mode").json() == {"open": False}
    assert client.get("/api/challenge").status_code == 404
    assert _submit_open(client).status_code == 404


def test_admin_can_open_and_close_the_form(client, admin):
    assert _open(client, admin)["enabled"] is True
    assert client.get("/api/mode").json() == {"open": True}
    ch = client.get("/api/challenge").json()
    assert ch["bits"] == intake_main.POW_BITS and ch["challenge"].count(".") == 2
    assert _open(client, admin, False)["enabled"] is False
    assert client.get("/api/mode").json() == {"open": False}
    assert client.get("/admin/open-form").status_code == 401          # el interruptor es solo de Farmhouse Link


def test_open_form_accepts_a_solved_challenge_and_marks_the_envelope(client, admin):
    _open(client, admin)
    res = _submit_open(client)
    assert res.status_code == 201 and res.json() == {"ok": True}
    subs = client.get("/admin/submissions", headers=admin).json()
    assert len(subs) == 1 and subs[0]["invite_hash"] == intake_main.OPEN_MARK


def test_each_challenge_works_only_once(client, admin):
    _open(client, admin)
    ch = _old_challenge()
    nonce = _solve(ch)
    assert _submit_open(client, ch, nonce).status_code == 201
    assert _submit_open(client, ch, nonce).status_code == 400


def test_challenges_that_are_forged_unsolved_too_fast_or_expired_are_rejected(client, admin):
    _open(client, admin)
    good = _old_challenge()
    parts = good.split(".")
    forged = f"{parts[0]}.{parts[1]}.{'A' * 22}"
    assert _submit_open(client, forged, "1").status_code == 400                                   # firma falsa
    other = f"{int(time.time()) - 40}.{parts[1]}.{parts[2]}"
    assert _submit_open(client, other, "1").status_code == 400                                    # fecha cambiada
    unsolved = next(str(n) for n in range(10_000) if intake_main._leading_zero_bits(_hashlib.sha256(f"{good}:{n}".encode()).digest()) < intake_main.POW_BITS)
    assert _submit_open(client, good, unsolved).status_code == 400                                # sin resolver
    young = intake_main.new_challenge()
    assert _submit_open(client, young, _solve(young)).status_code == 400                          # demasiado rápido
    old = _old_challenge(age=intake_main.POW_TTL_SECONDS + 60)
    assert _submit_open(client, old, _solve(old)).status_code == 400                              # vencido
    assert client.get("/admin/submissions", headers=admin).json() == []


def test_open_form_honeypot_discards_bots_silently(client, admin):
    _open(client, admin)
    res = _submit_open(client, website="http://spam.example")
    assert res.status_code == 201 and client.get("/admin/submissions", headers=admin).json() == []


def test_open_form_limits_each_ip(client, admin):
    _open(client, admin)
    for _ in range(intake_main.OPEN_IP_MAX):
        assert _submit_open(client).status_code == 201
    assert _submit_open(client).status_code == 429


def test_open_form_has_a_daily_cap(client, admin, monkeypatch):
    _open(client, admin)
    monkeypatch.setenv("OPEN_DAILY_CAP", "2")
    for _ in range(2):
        intake_main._open_hits.clear()
        assert _submit_open(client).status_code == 201
    intake_main._open_hits.clear()
    res = _submit_open(client)
    assert res.status_code == 429 and "mañana" in res.json()["detail"]
    assert client.get("/admin/open-form", headers=admin).json()["today"] == 2


def test_open_form_stops_when_the_pending_buffer_is_full(client, admin, monkeypatch):
    _open(client, admin)
    monkeypatch.setattr(intake_main, "OPEN_BUFFER_CAP", 1)
    assert _submit_open(client).status_code == 201
    intake_main._open_hits.clear()
    assert _submit_open(client).status_code == 503


def test_invitations_keep_working_while_the_form_is_open_or_closed(client, admin):
    token, _ = _new_invite(client, admin)
    assert _submit(client, token).status_code == 201
    _open(client, admin)
    token2, _ = _new_invite(client, admin)
    assert _submit(client, token2).status_code == 201


def test_invitation_endpoint_cannot_be_used_to_skip_the_challenge(client, admin):
    _open(client, admin)
    assert client.post("/api/submit", json={"token": "x" * 43, "envelope": _envelope()}).status_code == 404


def test_browser_proof_of_work_is_accepted_by_the_server(client, admin):
    """Interoperabilidad real: el crypto.js del navegador resuelve el reto y el servidor lo acepta."""
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node no está instalado")
    _open(client, admin)
    ch = _old_challenge()
    crypto_js = Path(intake_main.__file__).parent / "static" / "crypto.js"
    script = f"require({str(crypto_js)!r}); globalThis.IntakeCrypto.solvePow({ch!r}, {intake_main.POW_BITS}).then(n => console.log(n));"
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert _submit_open(client, ch, out.stdout.strip()).status_code == 201


def test_the_form_is_always_light(client):
    """El formulario no cambia con el modo oscuro del equipo."""
    css = client.get("/assets/colaborador.css").text
    assert "prefers-color-scheme" not in css and "color-scheme: light" in css
    assert 'name="color-scheme" content="light"' in client.get("/colaborador").text


def test_root_opens_the_same_form(client):
    """La dirección sola (sin /colaborador) también abre el formulario, con la misma política de seguridad."""
    root, page = client.get("/"), client.get("/colaborador")
    assert root.status_code == 200 and root.text == page.text
    assert root.headers["content-security-policy"] == page.headers["content-security-policy"]
