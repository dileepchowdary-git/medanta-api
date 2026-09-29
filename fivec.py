"""Calls to the 5C API. The 5C key never leaves this server."""

import logging
import threading
import time

import requests

import config

log = logging.getLogger("medanta.fivec")
_cache = {}                 # study_fk -> (study updated_at, fetched_at, reports)
_lock = threading.Lock()


def _get(path, **params):
    r = requests.get(f"{config.FIVEC_BASE}{path}", params=params,
                     headers={"Authorization": config.FIVEC_AUTH}, timeout=config.HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def completed_reports(study_fk, study_updated_at=None):
    """[{id, name, status}] for one study, in 5C's order (= portal's Reports (k / N)).
    Cached until the study changes or REPORT_CACHE_SECS pass."""
    now = time.time()
    with _lock:
        hit = _cache.get(study_fk)
        if hit and hit[0] == study_updated_at and now - hit[1] < config.REPORT_CACHE_SECS:
            return hit[2]
    data = _get(f"/report/client/completed/{int(study_fk)}")
    rows = data if isinstance(data, list) else (data or {}).get("data") or []
    reports = [{"id": r.get("id"), "name": r.get("name"), "status": r.get("status")}
               for r in rows if isinstance(r, dict) and r.get("id")]
    with _lock:
        _cache[study_fk] = (study_updated_at, now, reports)
    return reports


def report_pdf(study_fk, report_id):
    """Signed report PDF bytes (plain, no letterhead), or None if not generated yet."""
    params = {"report_ids": int(report_id), "file_type[]": "pdf", "languages[]": "en_US",
              "letterHead": "false", "report_type": "pdf", "study_id": int(study_fk)}
    rows = _get("/report/reports-signed-url", **params)
    url = rows[0].get("pdf_url") if isinstance(rows, list) and rows else None
    if not url:
        return None
    r = requests.get(url, timeout=config.HTTP_TIMEOUT)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return None
    return r.content


def gchat(text):
    if not config.GCHAT_WEBHOOK:
        return
    try:
        requests.post(config.GCHAT_WEBHOOK, json={"text": text}, timeout=15)
    except Exception as e:
        log.warning("gchat failed: %s", e)
