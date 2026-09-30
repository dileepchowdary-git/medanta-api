"""Read-only access to the 5C read replica, plus the local status store (SQLite)."""

import datetime as dt
import logging
import sqlite3
import time
from urllib.parse import quote

from sqlalchemy import create_engine, text

import config

log = logging.getLogger("medanta.db")
_engine = None


def _eng():
    global _engine
    if _engine is None:
        url = (f"postgresql+psycopg2://{config.PG_USER}:{quote(config.PG_PASSWORD)}"
               f"@{config.PG_HOST}:{config.PG_PORT}/{config.PG_DB_STUDY}")
        _engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _engine


def _query(sql, **params):
    """Rows as dicts. Retries the read replica's 'conflict with recovery' cancels."""
    for attempt in range(6):
        try:
            with _eng().connect() as c:
                return [dict(r._mapping) for r in c.execute(text(sql), params)]
        except Exception as e:
            if "conflict with recovery" in str(e) and attempt < 5:
                time.sleep(2)
                continue
            raise


_STUDIES_SQL = """
select distinct on (s.id)
       s.id            as study_fk,
       s.order_id,
       s.patient_id,
       s.patient_name,
       s.modality,
       s.created_at,
       s.updated_at,
       sd.dicom_details->>'study_date'       as study_date,
       sd.dicom_details->>'study_time'       as study_time,
       sd.dicom_details->>'accession_number' as accession
from public."Studies" s
left join public."StudyDetails" sd on sd.study_fk = s.id
where s.client_fk = :client
  and s.status = 'COMPLETED'
  and (:all_mods or s.modality && cast(:mods as varchar[]))
  and s.created_at > now() - make_interval(days => :days)
  {extra}
order by s.id, sd.id desc
"""


def completed_studies(days):
    """Medanta's completed studies (modalities per MEDANTA_MODALITIES) created in the last `days` days."""
    return _query(_STUDIES_SQL.format(extra=""), client=config.CLIENT_ID,
                  mods=config.MODALITIES, all_mods=config.ALL_MODALITIES, days=int(days))


def medanta_study(study_fk):
    """The study if (and only if) it is a completed Medanta study, else None."""
    rows = _query(_STUDIES_SQL.format(extra="and s.id = :fk"), client=config.CLIENT_ID,
                  mods=config.MODALITIES, all_mods=config.ALL_MODALITIES, days=config.MAX_DAYS + 30, fk=int(study_fk))
    return rows[0] if rows else None


def scan_time(study_date, study_time):
    """DICOM '20260928' + '030859.123' -> datetime (local scan time, as the portal shows)."""
    try:
        t = (study_time or "000000").split(".")[0].ljust(6, "0")
        return dt.datetime.strptime(study_date + t[:6], "%Y%m%d%H%M%S")
    except Exception:
        return None


# ---------------------------------------------------------------- status store
def _sq():
    config.DATA_DIR.mkdir(exist_ok=True)
    c = sqlite3.connect(config.STATUS_DB)
    c.execute("""create table if not exists report_status (
                   report_id  integer primary key,
                   study_fk   integer,
                   order_id   text,
                   status     text,
                   message    text,
                   caller_ip  text,
                   updated_at text)""")
    return c


def set_status(report_id, study_fk, order_id, status, message, caller_ip):
    with _sq() as c:
        c.execute("""insert into report_status values (?,?,?,?,?,?,?)
                     on conflict(report_id) do update set status=excluded.status,
                       message=excluded.message, caller_ip=excluded.caller_ip,
                       updated_at=excluded.updated_at""",
                  (int(report_id), int(study_fk), order_id, status, (message or "")[:1000],
                   caller_ip, dt.datetime.now().isoformat(timespec="seconds")))


def statuses():
    """report_id -> status for everything the robot has reported back."""
    with _sq() as c:
        return {r[0]: r[1] for r in c.execute("select report_id, status from report_status")}
