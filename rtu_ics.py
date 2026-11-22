#!/usr/bin/env python3
"""RTU nodarbību grafiks -> .ics (iPhone / Google / Outlook kalendāram).

Lietošana:
    pip install requests
    python rtu_ics.py                      # Būvniecība (RBCB0), 1. kurss, 2. grupa
    python rtu_ics.py --program RBCB0 --course 1 --group 2 -o rtu.ics
"""
import argparse
import datetime as dt
import sys
from zoneinfo import ZoneInfo

import requests

BASE = "https://nodarbibas.rtu.lv"
TZ = ZoneInfo("Europe/Riga")
HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0 (rtu-ics)",
}

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Riga
BEGIN:DAYLIGHT
TZOFFSETFROM:+0200
TZOFFSETTO:+0300
TZNAME:EEST
DTSTART:19700329T030000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0300
TZOFFSETTO:+0200
TZNAME:EET
DTSTART:19701025T040000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def post(session, path, data):
    r = session.post(f"{BASE}/{path}", data=data, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def find_semester_program_id(session, program_code, course, group):
    session.get(f"{BASE}/?lang=lv", headers=HEADERS, timeout=30)
    # Ņem semestri, kas vēl nav beidzies (tekošo vai nākamo), ar agrāko sākumu.
    now_ms = dt.datetime.now(TZ).timestamp() * 1000
    semesters = []
    for sem_id in range(60, 0, -1):
        try:
            se = post(session, "getChousenSemesterStartEndDate", {"semesterId": sem_id})
        except Exception:
            continue
        if se and se.get("endDate", 0) >= now_ms:
            semesters.append(se)
        elif semesters:
            break
    for se in sorted(semesters, key=lambda x: x["startDate"]):
        sem_id = se["semesterId"]
        programs = post(session, "findProgramsBySemesterId", {"semesterId": sem_id})
        prog_id = None
        for dept in programs:
            for p in dept.get("program", []) if isinstance(dept, dict) else []:
                if p.get("code") == program_code:
                    prog_id = p["programId"]
        if prog_id is None:
            continue
        groups = post(session, "findGroupByCourseId",
                      {"courseId": course, "semesterId": sem_id, "programId": prog_id})
        for g in groups:
            if str(g["group"]) == str(group):
                return g["semesterProgramId"], se
    raise SystemExit("Grupa nav atrasta.")


def months_between(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def fetch_events(session, sp_id, se):
    start = dt.datetime.fromtimestamp(se["startDate"] / 1000, TZ)
    end = dt.datetime.fromtimestamp(se["endDate"] / 1000, TZ)
    seen, out = set(), []
    for y, m in months_between(start, end):
        for e in post(session, "getSemesterProgEventList",
                      {"semesterProgramId": sp_id, "month": m, "year": y}):
            if e["eventDateId"] in seen:
                continue
            seen.add(e["eventDateId"])
            day = dt.datetime.fromtimestamp(e["eventDate"] / 1000, TZ).date()
            s, f = e["customStart"], e["customEnd"]
            room = (e.get("room") or {}).get("roomName") or e.get("roomInfoText") or ""
            out.append({
                "id": e["eventDateId"],
                "start": dt.datetime(day.year, day.month, day.day, s["hour"], s["minute"]),
                "end": dt.datetime(day.year, day.month, day.day, f["hour"], f["minute"]),
                "title": e["eventTempName"],
                "room": " ".join(room.split()),
            })
    return out


def esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def fold(line):
    b = line.encode("utf-8")
    if len(b) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        c = ch.encode("utf-8")
        if len(cur) + len(c) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += c
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def build_ics(events, name):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//rtu-ics//LV",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(name)}", "X-WR-TIMEZONE:Europe/Riga",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
        *VTIMEZONE.splitlines(),
    ]
    for e in sorted(events, key=lambda x: x["start"]):
        lines += [
            "BEGIN:VEVENT",
            f"UID:rtu-{e['id']}@nodarbibas.rtu.lv",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Europe/Riga:{e['start']:%Y%m%dT%H%M%S}",
            f"DTEND;TZID=Europe/Riga:{e['end']:%Y%m%dT%H%M%S}",
            f"SUMMARY:{esc(e['title'])}",
            f"LOCATION:{esc(e['room'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", default="RBCB0")
    ap.add_argument("--course", default="1")
    ap.add_argument("--group", default="2")
    ap.add_argument("-o", "--output", default="rtu.ics")
    a = ap.parse_args()

    with requests.Session() as s:
        sp_id, se = find_semester_program_id(s, a.program, a.course, a.group)
        events = fetch_events(s, sp_id, se)
    name = f"RTU {a.program} {a.course}.k. {a.group}.gr."
    with open(a.output, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(events, name))
    print(f"{len(events)} nodarbības -> {a.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
