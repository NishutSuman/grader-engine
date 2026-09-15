"""S3 storage for student submissions + report cards, with shareable URLs.

WHY: storing artifacts in the org Google Drive was a friction point — external
students don't get direct view access, and replacing a regraded card is awkward.
An S3 bucket fixes both: a stable object key per (eval, student) gives a durable
link students can open, and re-uploading the same key transparently replaces the
card after a regrade.

Config (env / .env — no secrets in code):
  GRADEPILOT_S3_BUCKET   bucket name              (required to enable S3)
  GRADEPILOT_S3_REGION   e.g. ap-south-1          (default us-east-1)
  GRADEPILOT_S3_PREFIX   key prefix               (default 'gradepilot')
  GRADEPILOT_S3_PUBLIC   '1' → return public URLs; else presigned URLs
  AWS creds: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (env, .env, or ~/.aws)

Object layout:
  <prefix>/<eval-slug>/cards/<student-code>.pdf
  <prefix>/<eval-slug>/submissions/<student-code>/<filename>
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    envp = REPO / ".env"
    if not envp.exists():
        return
    for line in envp.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _cfg() -> dict:
    _load_env()
    return {
        "bucket": os.environ.get("GRADEPILOT_S3_BUCKET", "").strip(),
        "region": os.environ.get("GRADEPILOT_S3_REGION", "us-east-1").strip(),
        "prefix": os.environ.get("GRADEPILOT_S3_PREFIX", "gradepilot").strip().strip("/"),
        "public": os.environ.get("GRADEPILOT_S3_PUBLIC", "").strip() in ("1", "true", "yes"),
    }


def enabled() -> bool:
    return bool(_cfg()["bucket"])


def _client():
    import boto3
    return boto3.client("s3", region_name=_cfg()["region"])


def _url(key: str) -> str:
    c = _cfg()
    if c["public"]:
        return f"https://{c['bucket']}.s3.{c['region']}.amazonaws.com/{key}"
    # presigned (default 7 days — students keep the link; regenerate on regrade)
    return _client().generate_presigned_url(
        "get_object", Params={"Bucket": c["bucket"], "Key": key}, ExpiresIn=7 * 24 * 3600)


def card_key(eval_slug: str, code: str, token: str = "") -> str:
    # token = unguessable suffix so public URLs can't be enumerated by roll number
    stem = f"{code}-{token}" if token else code
    return f"{_cfg()['prefix']}/{eval_slug}/cards/{stem}.pdf"


def submission_key(eval_slug: str, code: str, filename: str) -> str:
    return f"{_cfg()['prefix']}/{eval_slug}/submissions/{code}/{filename}"


def upload(local_path: str | Path, key: str, content_type: str = "application/pdf") -> str:
    """Upload (overwrites the key → regrade-safe) and return a shareable URL.

    Public read is granted by a BUCKET POLICY on the prefix (works on modern
    ACL-disabled buckets). Set GRADEPILOT_S3_ACL=1 only if the bucket still uses
    per-object ACLs (Object Ownership = 'ACLs enabled')."""
    c = _cfg()
    extra = {"ContentType": content_type}
    if c["public"] and os.environ.get("GRADEPILOT_S3_ACL", "").strip() in ("1", "true", "yes"):
        extra["ACL"] = "public-read"
    _client().upload_file(str(local_path), c["bucket"], key, ExtraArgs=extra)
    return _url(key)


def check() -> dict:
    """Verify creds + bucket write access — run once after configuring."""
    import io
    c = _cfg()
    if not c["bucket"]:
        return {"ok": False, "error": "GRADEPILOT_S3_BUCKET not set"}
    cl = _client()
    probe = f"{c['prefix']}/.gradepilot-probe"
    try:
        cl.put_object(Bucket=c["bucket"], Key=probe, Body=io.BytesIO(b"ok"))
        cl.delete_object(Bucket=c["bucket"], Key=probe)
        return {"ok": True, "bucket": c["bucket"], "region": c["region"],
                "url_mode": "public" if c["public"] else "presigned", "write": True}
    except Exception as e:
        return {"ok": False, "bucket": c["bucket"], "error": f"{type(e).__name__}: {e}"}


# ── backfill: push already-rendered local cards to S3 ────────────────────────
def backfill_cards(eval_id: int) -> dict:
    """Upload every student's local report card to S3, store the URL on the
    student. Idempotent (re-uploads overwrite). No-op if S3 not configured."""
    import secrets
    from app.db import repo
    from app.db.session import get_session
    if not enabled():
        raise RuntimeError("S3 not configured — set GRADEPILOT_S3_BUCKET")
    s = get_session()
    ev = repo.get_eval_by_id(s, eval_id)
    up = 0
    for st in repo.students(s, ev.id):
        meta = dict(st.download_meta_json or {})
        rep = dict(meta.get("report", {}))
        local = rep.get("card_file")
        if not (local and os.path.exists(local)):
            continue
        key = rep.get("s3_key") or card_key(ev.slug, st.student_code, secrets.token_hex(4))
        rep["url"] = upload(local, key)          # reuses key on regrade → same URL
        rep["s3_key"] = key
        meta["report"] = rep
        st.download_meta_json = meta
        up += 1
    s.commit()
    return {"eval": ev.slug, "cards_uploaded": up}
