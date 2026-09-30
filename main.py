"""
Medanta Patna report API - the door between the Medanta robot and 5C.

The robot (on a Medanta PC) only ever holds MEDANTA_API_KEY. This service holds
the read-replica login and the 5C key, and only ever returns client 4161's data.

  GET  /api/medanta/health
  GET  /api/medanta/reports?days=2              completed reports to paste
  GET  /api/medanta/reports/{report_id}/pdf?study_id=...
  POST /api/medanta/reports/{report_id}/status  {"study_id", "status", "message"}

Run: python main.py   (listens on 127.0.0.1:8003; nginx maps /api/medanta to it)
"""

import hmac
from concurrent.futures import ThreadPoolExecutor
import logging
import logging.handlers
import sys

import uvicorn
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

import config
import db
import fivec

log = logging.getLogger("medanta")


def _setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(name)s %(message)s")
    fh = logging.handlers.TimedRotatingFileHandler(config.LOG_DIR / "api.log", when="midnight",
                                                   backupCount=30, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [fh, sh]


def _caller_ip(request: Request):
    """Real client IP. Trust nginx's X-Real-IP only when the request came from nginx itself."""
    peer = request.client.host if request.client else ""
    if peer in ("127.0.0.1", "::1"):
        return request.headers.get("x-real-ip") or peer
    return peer


def _auth(request: Request, authorization: str):
    ip = _caller_ip(request)
    if not authorization or not hmac.compare_digest(authorization, config.API_KEY):
        log.warning("rejected %s %s from %s: bad key", request.method, request.url.path, ip)
        raise HTTPException(status_code=401, detail="Unauthorized")
    if config.ALLOWED_IPS and ip not in config.ALLOWED_IPS:
        log.warning("rejected %s %s from %s: IP not allowed", request.method, request.url.path, ip)
        raise HTTPException(status_code=403, detail="Forbidden")
    return ip


router = APIRouter(prefix="/api/medanta")


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/reports")
def reports(request: Request, days: int = config.DEFAULT_DAYS, include_done: bool = False,
            authorization: str = Header(default="")):
    """One entry per 5C report (a multi-report order gives several), with everything
    the robot needs to find the eHIS row: Patient ID, study description, scan time."""
    ip = _auth(request, authorization)
    days = max(1, min(int(days), config.MAX_DAYS))
    done = {} if include_done else db.statuses()
    out, errors = [], 0
    studies = db.completed_studies(days)

    def one(s):
        try:
            return s, fivec.completed_reports(s["study_fk"], str(s["updated_at"])), None
        except Exception as e:
            return s, None, e
    # one 5C call per study; in parallel, or a cold cache over all modalities takes minutes
    with ThreadPoolExecutor(max_workers=config.FIVEC_WORKERS) as pool:
        results = list(pool.map(one, studies))
    for s, reps, err in results:
        if err is not None:
            errors += 1
            log.warning("reports for study %s failed: %s", s["study_fk"], err)
            continue
        scan = db.scan_time(s["study_date"], s["study_time"])
        for n, r in enumerate(reps, 1):
            if done.get(r["id"]) == "SUCCESS":
                continue
            out.append({
                "report_id": r["id"],
                "report_no": n,
                "report_count": len(reps),
                "report_name": r["name"],
                "study_id": s["study_fk"],
                "order_id": s["order_id"],
                "patient_id": s["patient_id"],
                "patient_name": s["patient_name"],
                "modality": list(s["modality"] or []),
                "scan": scan.isoformat() if scan else None,
                "accession": s["accession"],
                "last_status": done.get(r["id"]),
            })
    log.info("reports days=%s -> %s report(s) from %s studies for %s (%s study error(s))",
             days, len(out), len(studies), ip, errors)
    return out


@router.get("/reports/{report_id}/pdf")
def report_pdf(report_id: int, study_id: int, request: Request,
               authorization: str = Header(default="")):
    """The report PDF - only if the study is Medanta's and the report belongs to it."""
    ip = _auth(request, authorization)
    s = db.medanta_study(study_id)
    if not s:
        log.warning("pdf %s/%s refused for %s: not a completed Medanta study", study_id, report_id, ip)
        raise HTTPException(status_code=404, detail="Not found")
    reps = fivec.completed_reports(s["study_fk"], str(s["updated_at"]))
    if not any(r["id"] == report_id for r in reps):
        log.warning("pdf %s/%s refused for %s: report not in study", study_id, report_id, ip)
        raise HTTPException(status_code=404, detail="Not found")
    pdf = fivec.report_pdf(study_id, report_id)
    if not pdf:
        raise HTTPException(status_code=409, detail="PDF not generated yet")
    log.info("pdf study=%s report=%s (%s KB) -> %s", study_id, report_id, len(pdf) // 1024, ip)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{study_id}_{report_id}.pdf"'})


class StatusIn(BaseModel):
    study_id: int
    status: str                 # SUCCESS | FAILURE | SKIPPED
    message: str = ""
    order_id: str = ""
    patient_id: str = ""


@router.post("/reports/{report_id}/status")
def report_status(report_id: int, body: StatusIn, request: Request,
                  authorization: str = Header(default="")):
    ip = _auth(request, authorization)
    status = body.status.strip().upper()
    if status not in ("SUCCESS", "FAILURE", "SKIPPED"):
        raise HTTPException(status_code=422, detail="status must be SUCCESS, FAILURE or SKIPPED")
    if not db.medanta_study(body.study_id):
        raise HTTPException(status_code=404, detail="Not found")
    db.set_status(report_id, body.study_id, body.order_id, status, body.message, ip)
    log.info("status report=%s study=%s %s %s", report_id, body.study_id, status, body.message[:200])
    if status != "SUCCESS":
        fivec.gchat(f"Medanta Patna paste {status}: order {body.order_id or '?'} "
                    f"patient {body.patient_id or '?'} report {report_id} - {body.message[:300]}")
    return {"ok": True}


app = FastAPI(title="Medanta report API", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)


if __name__ == "__main__":
    config.check()
    _setup_logging()
    log.info("starting on %s:%s for client %s", config.HOST, config.PORT, config.CLIENT_ID)
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")
