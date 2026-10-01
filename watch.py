"""
Background GChat jobs of the Medanta report API:

  robot down  - the robot asks /reports every cycle (~2 min). No call for MEDANTA_ROBOT_DOWN_MIN
                minutes -> one alert; when it calls again -> "back up". Survives API restarts.
  daily summary at MEDANTA_SUMMARY_AT (local time): what was pasted, what failed, and every
                report that was NOT pasted with the robot's reason - the staff's to-do list.
"""

import datetime as dt
import json
import logging
import threading
import time

import config
import db
import fivec

log = logging.getLogger("medanta.watch")
_STATE = config.DATA_DIR / "watch.json"
_lock = threading.Lock()


def _load():
    try:
        return json.loads(_STATE.read_text())
    except Exception:
        return {}


def _save(st):
    config.DATA_DIR.mkdir(exist_ok=True)
    _STATE.write_text(json.dumps(st))


def seen():
    """Called on every robot /reports request."""
    with _lock:
        st = _load()
        was_down = st.get("down")
        st["last_seen"] = time.time()
        st["down"] = False
        _save(st)
    if was_down:
        fivec.gchat("\u2705 Medanta Patna robot is back and pasting again.")
        log.info("robot back")


def _check_down():
    if not config.ROBOT_DOWN_MIN:
        return
    with _lock:
        st = _load()
        last = st.get("last_seen")
        if not last or st.get("down") or time.time() - last < config.ROBOT_DOWN_MIN * 60:
            return
        st["down"] = True
        _save(st)
    mins = int((time.time() - last) / 60)
    fivec.gchat(f"\u26a0\ufe0f Medanta Patna robot has not checked in for {mins} min "
                f"(last {dt.datetime.fromtimestamp(last):%d/%m %H:%M}). Reports are NOT being pasted. "
                "Check the Medanta PC: logged in and unlocked? Run status_robot.bat.")
    log.warning("robot down: last seen %s min ago", mins)


def summary_text(day=None):
    day = day or dt.date.today()
    since = dt.datetime.combine(day, dt.time()).isoformat()
    ev = db.events_since(since)
    ok = [e for e in ev.values() if e["status"] == "SUCCESS"]
    bad = [e for e in ev.values() if e["status"] == "FAILURE"]
    skip = [e for e in ev.values() if e["status"] == "SKIPPED"]
    line = lambda e: (f"- {e['patient_id']} {e['patient_name'] or ''} | {e['report_name'] or e['report_id']}"
                      f" | {(e['message'] or '')[:90]}")
    out = [f"\U0001f4cb Medanta Patna robot - {day:%d %b %Y}",
           f"Pasted: {len(ok)}   Failed (needs a person): {len(bad)}   Not pasted: {len(skip)}"]
    if bad:
        out.append("\nFAILED - finish in eHIS:")
        out += [line(e) for e in bad[:30]]
    if skip:
        out.append("\nNOT PASTED (no matching pending exam in eHIS) - check / do by hand:")
        out += [line(e) for e in sorted(skip, key=lambda e: e["at"], reverse=True)[:60]]
        if len(skip) > 60:
            out.append(f"... and {len(skip) - 60} more")
    return "\n".join(out)


def _check_summary():
    if not config.SUMMARY_AT:
        return
    try:
        hh, mm = (int(x) for x in config.SUMMARY_AT.split(":"))
    except ValueError:
        return
    now = dt.datetime.now()
    if (now.hour, now.minute) < (hh, mm):
        return
    with _lock:
        st = _load()
        if st.get("summary_day") == now.date().isoformat():
            return
        st["summary_day"] = now.date().isoformat()
        _save(st)
    fivec.gchat(summary_text(now.date()))
    log.info("daily summary posted")


def _loop():
    while True:
        for job in (_check_down, _check_summary):
            try:
                job()
            except Exception as e:
                log.warning("%s failed: %s", job.__name__, e)
        time.sleep(60)


def start():
    threading.Thread(target=_loop, name="medanta-watch", daemon=True).start()
    log.info("watch started (robot down after %s min, summary at %s)", config.ROBOT_DOWN_MIN, config.SUMMARY_AT)
