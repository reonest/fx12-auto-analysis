#!/usr/bin/env python3
"""Official-first FX events and headline collector.

NO automatic S-grade news alerts. An article cannot become a 'breaking' news
item just because it was first fetched today: publication time is mandatory.
Only official scheduled event feeds and public RSS are fetched; third-party
FX calendars are links for GPT re-verification, not scraped or republished.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone, date
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from html import unescape
from urllib.parse import urlparse, urljoin

import requests
from icalendar import Calendar

UTC = timezone.utc
JST = ZoneInfo("Asia/Tokyo")
SOURCES = [
    {"id": "bls", "kind": "ics", "url": "https://www.bls.gov/schedule/news_release/bls.ics", "ccy": ["USD"]},
    {"id": "bea", "kind": "bea", "url": "https://apps.bea.gov/API/signup/release_dates.json", "ccy": ["USD"]},
    {"id": "frb_policy", "kind": "rss", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "ccy": ["USD"]},
    {"id": "frb_speeches", "kind": "rss", "url": "https://www.federalreserve.gov/feeds/speeches.xml", "ccy": ["USD"]},
    {"id": "ecb_press", "kind": "rss", "url": "https://www.ecb.europa.eu/rss/press.html", "ccy": ["EUR"]},
    {"id": "boj_news", "kind": "rss", "url": "https://www.boj.or.jp/rss/whatsnew.xml", "ccy": ["JPY"]},
    {"id": "rba_releases", "kind": "rss", "url": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml", "ccy": ["AUD"]},
]
REFERENCE_SITES = [
    {"name": "GMO外貨経済指標", "url": "https://www.gaikaex.com/gaikaex/mark/calendar/"},
    {"name": "みんかぶFX経済指標", "url": "https://fx.minkabu.jp/indicators"},
    {"name": "FXStreet経済指標", "url": "https://www.fxstreet.jp/economic-calendar"},
    {"name": "楽天証券経済カレンダー", "url": "https://www.rakuten-sec.co.jp/web/market/calendar/"},
    {"name": "マネックス証券指標", "url": "https://mst.monex.co.jp/pc/servlet/ITS/report/EconomyIndexCalendar"},
    {"name": "岡三オンライン指標", "url": "https://media.okasan-online.co.jp/marketinfo/economic_indicators/"},
    {"name": "SBI FXスプレッド実績", "url": "https://www.sbifxt.co.jp/service/detailedspread.html"},
    {"name": "英中銀MPC日程", "url": "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates"},
    {"name": "日銀公表予定", "url": "https://www.boj.or.jp/about/calendar/index.htm"},
    {"name": "カナダ銀行政策日程", "url": "https://www.bankofcanada.ca/core-functions/monetary-policy/key-interest-rate/"},
    {"name": "RBNZ公表・ニュース", "url": "https://www.rbnz.govt.nz/news-and-events"},
    {"name": "Banxico政策発表", "url": "https://www.banxico.org.mx/publications-and-press/announcements-of-monetary-policy-decisions/monetary-policy-announcements.html"},
    {"name": "米雇用統計予定", "url": "https://www.bls.gov/schedule/news_release/empsit.htm"},
]
IMPORTANT = (
    ("S", ("employment situation", "consumer price", "cpi", "nonfarm",
           "personal income and outlays", "gross domestic product",
           "monetary policy", "interest rate", "fomc", "mpc", "policy decision")),
    ("A", ("producer price", "ppi", "job openings", "jolts", "pce", "trade",
           "industrial production", "wage", "unemployment", "inflation")),
)
NEWS_MAX_AGE_MIN = 120  # not a claim of being live; only temporal eligibility


def utc_dt(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return None  # date-only event is not an exact announcement time
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat() if value else None


def jst(value):
    return value.astimezone(JST).strftime("%Y-%m-%d %H:%M JST") if value else None


def importance(title):
    name = str(title).lower()
    for level, words in IMPORTANT:
        if any(word in name for word in words):
            return level
    return "B"


def clean_text(raw, limit=190):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(raw or "")))).strip()[:limit]


def clean_url(link, source=None):
    url = str(link or "").strip()
    if source and url:
        url = urljoin(source, url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if source:
        origin = urlparse(source).hostname
        if not origin or (parsed.hostname != origin and not parsed.hostname.endswith("." + origin)):
            return None
    if parsed.scheme == "http":
        url = "https://" + url[len("http://"):]
    return url


def event_from(source, name, stamp, url):
    if not stamp:
        return None
    return {"id": hashlib.sha256(f"{source['id']}|{name}|{iso(stamp)}".encode()).hexdigest()[:20],
            "source": source["id"], "name": clean_text(name),
            "country": "US", "currencies": source["ccy"], "importance_hint": importance(name),
            "scheduled_utc": iso(stamp), "scheduled_jst": jst(stamp),
            "previous": None, "forecast": None, "actual": None,
            "url": clean_url(url) or source["url"],
            "time_status": "VERIFIED_FROM_SOURCE"}


def parse_bls_ics(raw, source, now, horizon=7):
    cal = Calendar.from_ical(raw)
    out = []
    for event in cal.walk("VEVENT"):
        value = event.get("DTSTART")
        if value is None:
            continue
        try:
            dt = value.dt
            if isinstance(dt, datetime) and dt.tzinfo is None:
                # Floating DTSTART is ambiguous; do not assume NY time.
                continue
            stamp = utc_dt(dt)
        except (ValueError, TypeError, AttributeError):
            continue
        if not stamp or not now <= stamp <= now + timedelta(days=horizon):
            continue
        name = str(event.get("SUMMARY") or "")
        link = event.get("URL") or source["url"]
        item = event_from(source, name, stamp, link)
        if item:
            out.append(item)
    return out


def parse_bea_json(data, source, now, horizon=7):
    if not isinstance(data, dict):
        raise ValueError("Invalid BEA schedule")
    out = []
    for name, item in data.items():
        dates = item.get("release_dates", []) if isinstance(item, dict) else []
        for value in dates:
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (TypeError, ValueError, AttributeError):
                continue
            stamp = utc_dt(stamp)
            if stamp and now <= stamp <= now + timedelta(days=horizon):
                entry = event_from(source, name, stamp, source["url"])
                if entry:
                    out.append(entry)
    return out


def child_text(element, names):
    for node in element.iter():
        if node.tag.rsplit("}", 1)[-1].lower() in names and node.text and node.text.strip():
            return node.text.strip()
    return ""


def parse_rss(raw, source, now):
    root = ET.fromstring(raw)
    entries = [node for node in root.iter()
               if node.tag.rsplit("}", 1)[-1].lower() in ("item", "entry")]
    out = []
    for entry in entries[:80]:
        title = clean_text(child_text(entry, {"title"}))
        link = child_text(entry, {"link"})
        if not link:
            for node in entry.iter():
                if node.tag.rsplit("}", 1)[-1].lower() == "link":
                    link = node.get("href", "")
                    if link:
                        break
        published = child_text(entry, {"pubdate", "published", "updated", "date"})
        stamp = None
        if published:
            try:
                stamp = parsedate_to_datetime(published)
            except (TypeError, ValueError, IndexError):
                try:
                    stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass
        stamp = utc_dt(stamp)
        if not title or not clean_url(link, source['url']):
            continue
        age = round((now - stamp).total_seconds() / 60) if stamp else None
        if age is None:
            state = "PUBLICATION_TIME_UNKNOWN"
        elif age < -5:
            state = "CLOCK_OR_FUTURE_TIME"
        elif age <= NEWS_MAX_AGE_MIN:
            state = "RECENT_SOURCE_PUBLICATION"
        else:
            state = "ARCHIVE_NOT_BREAKING"
        # No automatic S-grade status: headline, data impact, market response
        # and independent verification are not available from RSS alone.
        out.append({"id": hashlib.sha256((source["id"] + "|" + link).encode()).hexdigest()[:20],
                    "source": source["id"], "currencies": source["ccy"],
                    "title": title, "url": clean_url(link, source["url"]),
                    "published_utc": iso(stamp), "published_jst": jst(stamp),
                    "publication_age_minutes": age,
                    "first_seen_utc": None,
                    "observed_in_this_run_utc": iso(now),
                    "freshness": state, "s_grade": "NOT_EVALUATED",
                    "breaking_eligible": state == "RECENT_SOURCE_PUBLICATION",
                    "importance_hint": importance(title)})
    return out


def collect_events(session, now=None):
    now = now or datetime.now(UTC)
    status, events, news = [], [], []
    for source in SOURCES:
        state = {"id": source["id"], "url": source["url"],
                 "checked_utc": iso(now), "status": "UNAVAILABLE"}
        try:
            r = session.get(source["url"], timeout=15,
                            headers={"Accept": "application/json, application/rss+xml, application/xml, text/xml, text/calendar, */*"})
            r.raise_for_status()
            if len(r.content) > 5_000_000:
                raise ValueError("Payload too large")
            if source["kind"] == "ics":
                got = parse_bls_ics(r.content, source, now)
                events.extend(got)
            elif source["kind"] == "bea":
                got = parse_bea_json(r.json(), source, now)
                events.extend(got)
            else:
                got = parse_rss(r.content, source, now)
                news.extend(got)
            state.update({"status": "OK" if got else "EMPTY_OR_NO_MATCHING_RECORDS",
                          "records": len(got)})
        except (requests.RequestException, ValueError, ET.ParseError, TypeError) as exc:
            state["error"] = type(exc).__name__ + ": " + str(exc)[:100]
        status.append(state)
    events = sorted({(e["source"], e["name"], e["scheduled_utc"]): e for e in events}.values(),
                    key=lambda e: e["scheduled_utc"])
    news = sorted({n["id"]: n for n in news}.values(),
                  key=lambda n: n["published_utc"] or "", reverse=True)
    return {
        "generated_utc": iso(now), "events": events[:120], "news": news[:80],
        "sources": status, "reference_sites_not_scraped": REFERENCE_SITES,
        "calendar_coverage": "PARTIAL_OFFICIAL_SOURCES",
        "news_policy": "Source timestamp mandatory; old/undated articles never S-grade or breaking; no inferred actual/forecast.",
        "critical_note": "Do not infer no major event from an empty calendar; verify with official/third-party sources at analysis time."
    }
