# Medanta Patna report API

The door between the Medanta Patna eHIS robot (runs on a Medanta PC) and 5C.

- The robot only holds **`MEDANTA_API_KEY`**. It can read Medanta's completed reports and
  their PDFs, and report back what it pasted. Nothing else.
- This service holds the **read-replica login** and the **5C API key**. Neither ever
  leaves the server.
- **Client 4161 only.** The client id is fixed in code; no parameter can make the service
  return another client's data. PDFs are refused unless the study is a completed Medanta
  study *and* the report belongs to that study.

Runs on the aae VM (164.52.198.98) as `medanta-report-api.service` on `127.0.0.1:8003`,
published by nginx at `https://aae-auto-agent.5cn.co.in/api/medanta/`.

## Endpoints

Every call except `/health` needs the header `Authorization: <MEDANTA_API_KEY>`.

| Method | Path | Returns |
|---|---|---|
| GET | `/api/medanta/health` | `{"ok": true}` |
| GET | `/api/medanta/reports?days=2` | Completed reports (all modalities unless MEDANTA_MODALITIES limits them) of the last `days` (1–7), one entry per report, minus those already reported SUCCESS (`include_done=true` shows all) |
| GET | `/api/medanta/reports/{report_id}/pdf?study_id=…` | The report PDF (plain, no letterhead); 404 if not Medanta's, 409 if the PDF isn't generated yet |
| POST | `/api/medanta/reports/{report_id}/status` | Body `{"study_id", "status": "SUCCESS\|FAILURE\|SKIPPED", "message", "order_id", "patient_id"}`; non-SUCCESS posts a Google Chat alert |

A report entry:

```json
{
  "report_id": 12430786, "report_no": 1, "report_count": 1,
  "report_name": "CT Brain Plain",
  "study_id": 9569051, "order_id": "7GZVF6LF86",
  "patient_id": "BP00532332", "patient_name": "Lal Bikau",
  "modality": ["CT"], "scan": "2026-09-28T03:08:59",
  "accession": "313517853", "last_status": null
}
```

`scan` is the DICOM study date/time (the same "Scan Date & Time" the 5C portal shows).
A multi-report order (e.g. MRI Brain + MRI Cervical) gives one entry per report.

## Files

| File | Purpose |
|---|---|
| `main.py` | FastAPI app: auth, IP allowlist, the four endpoints, logging |
| `db.py` | Read-only read-replica queries; local SQLite status store |
| `fivec.py` | 5C API: report list per study (cached), signed PDF, Google Chat |
| `config.py` | Settings from `.env`; refuses to start if a secret is missing |
| `deploy/medanta-report-api.service` | systemd unit |
| `deploy/nginx-location.conf` | The nginx `location` block for `/api/medanta/` |

Runtime folders `data/` (status.sqlite3) and `logs/` (api.log, 30 days) are gitignored.

## Deploy on the aae VM

```bash
# 1. code
cd /root && git clone https://github.com/dileepchowdary-git/medanta-api.git medanta_report_api
cd /root/medanta_report_api
python3 -m venv venv && venv/bin/pip install -r requirements.txt

# 2. secrets
cp .env.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(40))"   # -> MEDANTA_API_KEY
#   PG_HOST_PROD / PG_USER_PROD / PG_PASSWORD_PROD : same as /root/yashoda/.env
#   FIVEC_AUTH                                    : same as /root/Health-map-Report-sync/.env
nano .env

# 3. service
cp deploy/medanta-report-api.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now medanta-report-api
systemctl status medanta-report-api --no-pager
curl -s http://127.0.0.1:8003/api/medanta/health            # {"ok":true}

# 4. nginx (only shared file touched - back it up first)
cp /etc/nginx/sites-available/aae-auto-agent.5cn.co.in /root/aae-auto-agent.nginx.bak-$(date +%F)
nano /etc/nginx/sites-available/aae-auto-agent.5cn.co.in
#   paste deploy/nginx-location.conf inside the "listen 443 ssl" server { } block
nginx -t && systemctl reload nginx

# 5. check from outside
curl -s https://aae-auto-agent.5cn.co.in/api/medanta/health
curl -s -H "Authorization: <key>" "https://aae-auto-agent.5cn.co.in/api/medanta/reports?days=1" | head -c 600
```

Update later: `cd /root/medanta_report_api && git pull && systemctl restart medanta-report-api`.

### Rollback

```bash
systemctl disable --now medanta-report-api
cp /root/aae-auto-agent.nginx.bak-<date> /etc/nginx/sites-available/aae-auto-agent.5cn.co.in
nginx -t && systemctl reload nginx
```

Nothing else on the VM (Yashoda :8002, GenX :8001, study correlation :8000) is touched.

## Security notes

- Rotate the key by changing `MEDANTA_API_KEY` in `.env` and restarting; update the robot's `.env`.
- `MEDANTA_ALLOWED_IPS` locks the key to Medanta's outgoing IP(s). The real caller IP is
  taken from nginx's `X-Real-IP`, trusted only when the request comes from nginx itself.
- The service listens on 127.0.0.1 only; it is reachable from outside only through nginx/HTTPS.
- Logs record caller IP, endpoint and counts - never keys or PDF content.
