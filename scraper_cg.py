#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent de veille des Conseils du Gouvernement (Maroc) — version multi-sources.

Sources interrogées (dans cet ordre, le résultat est fusionné par date) :
  1. cg.gov.ma      -> liste + détail (lois, accords, nominations)
  2. sgg.gov.ma     -> tableau "مجلس الحكومة" (ordre du jour + compte rendu PDF)
  3. mcrpsc.gov.ma  -> communiqués "بلاغ صحفي حول انعقاد اجتماع مجلس الحكومة"

Pas de Selenium / Chrome : de simples requêtes HTTP suffisent (les 3 pages
sont rendues côté serveur). Si une source est bloquée, les autres prennent
le relais, et l'email indique clairement quelles sources ont répondu.
"""

import os
import re
import sys
import json
import logging
import smtplib
import time
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ============================================================================
# CONFIGURATION
# ============================================================================

CG_URL = "https://www.cg.gov.ma/ar/%D9%85%D8%AC%D9%84%D8%B3-%D8%A7%D9%84%D8%AD%D9%83%D9%88%D9%85%D8%A9"
SGG_URL = "https://www.sgg.gov.ma/arabe/travailgouvernemental.aspx"
MCRP_URL = "https://www.mcrpsc.gov.ma/%D8%A7%D8%AE%D8%A8%D8%A7%D8%B1/"

DATA_DIR = Path("data_cg")
DATA_DIR.mkdir(exist_ok=True)
SEEN_FILE = DATA_DIR / "conseils_vus.json"      # liste de dates ISO déjà traitées
RESULTS_FILE = DATA_DIR / "conseils.json"       # base complète

SMTP_SERVER = os.environ.get("SMTP_SERVER") or "smtp.gmail.com"
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar-MA,ar;q=0.9,fr;q=0.8,en;q=0.7",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("cg")

# ============================================================================
# DATES
# ============================================================================

MOIS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "ماي": 5, "مايو": 5,
    "يونيو": 6, "يونيوز": 6, "يوليوز": 7, "يوليو": 7, "غشت": 8, "أغسطس": 8, "اغسطس": 8,
    "شتنبر": 9, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نونبر": 11, "نوفمبر": 11,
    "دجنبر": 12, "ديسمبر": 12,
}
_MOIS_RE = "|".join(sorted(MOIS, key=len, reverse=True))
# "10 سبتمبر 2026" — les dates hégiriennes ("28 من ربيع الأول 1448") ne matchent pas
DATE_AR_RE = re.compile(rf"(\d{{1,2}})\s+({_MOIS_RE})\s+(20\d{{2}})")


def date_from_arabic(text):
    """Dernière date grégorienne écrite en arabe dans le texte -> 'YYYY-MM-DD'."""
    found = DATE_AR_RE.findall(text or "")
    if not found:
        return ""
    j, m, a = found[-1]
    try:
        return date(int(a), MOIS[m], int(j)).isoformat()
    except ValueError:
        return ""


def date_from_numeric(text, sep=r"[./]"):
    m = re.search(rf"(\d{{1,2}}){sep}(\d{{1,2}}){sep}(20\d{{2}})", text or "")
    if not m:
        return ""
    j, mo, a = map(int, m.groups())
    try:
        return date(a, mo, j).isoformat()
    except ValueError:
        return ""


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


# ============================================================================
# PARSEURS (purs : HTML -> liste de dicts, donc testables hors ligne)
# ============================================================================

def parse_cg(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    # NB : class_="a b" ne match QUE l'attribut exact "a b" ; le sélecteur CSS
    # ci-dessous match les éléments qui possèdent les deux classes.
    for art in soup.select("div.article-format.c-gov-img-wrp"):
        a = art.find("a", href=True)
        h4 = art.find("h4")
        t = art.find("time")
        titre = clean(h4.get_text()) if h4 else ""
        iso = (t.get("datetime", "")[:10] if t else "") or date_from_arabic(titre)
        if not a or not iso:
            continue
        p = art.find("p")
        out.append({
            "date": iso, "source": "cg.gov.ma", "titre": titre,
            "url": urljoin(CG_URL, a["href"]),
            "extrait": clean(p.get_text()) if p else "",
        })
    return out


def parse_sgg(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tr in soup.select("table.sggTable tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        iso = date_from_numeric(tds[0].get_text())
        # Conseil reporté : "(أُجِّل ليوم 22/07/2026)" -> la vraie date est celle-ci
        reporte = date_from_numeric(tds[1].get_text(), sep="/")
        iso = reporte or iso
        if not iso:
            continue
        oj = tds[1].find("a", href=True)
        cr = tds[2].find("a", href=True)
        out.append({
            "date": iso, "source": "sgg.gov.ma",
            "ordre_du_jour_url": urljoin(SGG_URL, oj["href"]) if oj else "",
            "compte_rendu_url": urljoin(SGG_URL, cr["href"]) if cr else "",
        })
    return out


def parse_mcrpsc(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for h5 in soup.select("h5.card-title"):
        titre = clean(h5.get_text())
        if "مجلس الحكومة" not in titre or "اجتماع" not in titre:
            continue
        iso = date_from_arabic(titre)
        box = h5.find_parent("div")
        a = box.find("a", href=True) if box else None
        if not iso or not a:
            continue
        out.append({
            "date": iso, "source": "mcrpsc.gov.ma", "titre": titre,
            "url": urljoin(MCRP_URL, a["href"]),
        })
    return out


def parse_cg_detail(html):
    soup = BeautifulSoup(html, "html.parser")

    def items(element_id):
        box = soup.find(id=element_id)
        return [clean(li.get_text(" ")) for li in box.find_all("li") if clean(li.get_text())] if box else []

    body = soup.find(id="read_content")
    pdf = ""
    for a in soup.find_all("a", href=True):
        if ".pdf" in a["href"].lower() and ("البلاغ" in a.get_text() or "COMM" in a["href"]):
            pdf = urljoin(CG_URL, a["href"])
            break
    return {
        "lois": items("loi"), "accords": items("agreement"), "nominations": items("nomination"),
        "pdf_url": pdf, "contenu": clean(body.get_text(" ")) if body else "",
    }


# ============================================================================
# RÉSEAU
# ============================================================================

def http_get(session, url, tries=3):
    last = None
    for i in range(tries):
        try:
            r = session.get(url, headers=HEADERS, timeout=45)
            r.raise_for_status()
            return r.content.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("  tentative %d/%d échouée (%s): %s", i + 1, tries, url[:60], e)
            time.sleep(3 * (i + 1))
    raise RuntimeError(str(last))


SOURCES = [
    ("cg.gov.ma", CG_URL, parse_cg),
    ("sgg.gov.ma", SGG_URL, parse_sgg),
    ("mcrpsc.gov.ma", MCRP_URL, parse_mcrpsc),
]


def collect(session):
    """Retourne (records_par_date, statut_par_source)."""
    merged, status = {}, {}
    for name, url, parser in SOURCES:
        log.info("Source %s : %s", name, url)
        try:
            items = parser(http_get(session, url))
            if not items:
                raise RuntimeError("page reçue mais 0 conseil reconnu (structure HTML modifiée ? page de blocage ?)")
            status[name] = f"OK ({len(items)} conseils)"
            log.info("  -> %d conseils", len(items))
        except Exception as e:  # noqa: BLE001
            status[name] = f"ERREUR : {str(e)[:150]}"
            log.error("  -> %s", status[name])
            continue
        for it in items:
            rec = merged.setdefault(it["date"], {"date": it["date"], "sources": []})
            rec["sources"].append(name)
            for k, v in it.items():
                if k not in ("date", "source") and v and not rec.get(k):
                    rec[k] = v
    return merged, status


# ============================================================================
# ÉTAT
# ============================================================================

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================================
# EMAIL
# ============================================================================

def build_email(new, status, all_failed, baseline):
    if all_failed:
        subject = "[Conseil du Gouvernement] ⚠ ERREUR : aucune source accessible"
    elif new:
        subject = f"[Conseil du Gouvernement] {len(new)} nouveau(x) conseil(s) - {date.today().isoformat()}"
    else:
        subject = f"[Conseil du Gouvernement] Aucun nouveau conseil - {date.today().isoformat()}"

    rows = "".join(
        f"<li><b>{escape(n)}</b> : {escape(s)}</li>" for n, s in status.items()
    )
    body = [f'<html dir="rtl" lang="ar"><body style="font-family:Arial,sans-serif">',
            f"<h2>📋 Agent Conseil du Gouvernement</h2><p>Rapport du {date.today().isoformat()}</p>",
            f'<ul dir="ltr" style="text-align:left">{rows}</ul>']
    if all_failed:
        body.append('<p style="background:#f8d7da;padding:12px">⚠ Aucune des 3 sources n\'a répondu : '
                    "ce n'est PAS « aucun nouveau conseil », c'est une panne d'accès (blocage IP probable).</p>")
    if baseline and new:
        body.append("<p><i>Première exécution : historique enregistré, seul le conseil le plus récent est listé.</i></p>")
    for r in new:
        body.append('<div style="border:1px solid #ddd;padding:12px;margin:12px 0;border-radius:6px">')
        body.append(f'<h3 style="color:#056e52">{escape(r.get("titre") or "مجلس الحكومة " + r["date"])}</h3>')
        body.append(f"<p>📅 {r['date']} — sources : {escape(', '.join(sorted(set(r['sources']))))}</p>")
        if r.get("extrait"):
            body.append(f"<p>{escape(r['extrait'])}</p>")
        links = [("🔗 Page", r.get("url")), ("📄 Communiqué PDF", r.get("pdf_url")),
                 ("🗒 Ordre du jour", r.get("ordre_du_jour_url")), ("📑 Compte rendu", r.get("compte_rendu_url"))]
        body.append("<p>" + " | ".join(f'<a href="{escape(u)}">{t}</a>' for t, u in links if u) + "</p>")
        for label, key in (("📜 مراسيم و قوانين", "lois"), ("🤝 اتفاقيات", "accords"), ("👤 تعيينات", "nominations")):
            if r.get(key):
                body.append(f"<h4>{label}</h4>" + "".join(f"<div>• {escape(x)}</div>" for x in r[key]))
        body.append("</div>")
    body.append("<hr><p><i>Agent automatique — cg.gov.ma / sgg.gov.ma / mcrpsc.gov.ma</i></p></body></html>")
    return subject, "".join(body)


def send_email(subject, html):
    if not (SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        log.warning("Config email incomplète, pas d'envoi.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, SMTP_USER, EMAIL_TO
    msg.attach(MIMEText(re.sub(r"<[^>]+>", " ", html), "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)
    log.info("Email envoyé à %s", EMAIL_TO)


# ============================================================================
# MAIN
# ============================================================================

def main():
    log.info("=== Agent Conseil du Gouvernement — %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))
    seen = set(load_json(SEEN_FILE, []))
    base = load_json(RESULTS_FILE, [])
    known = {r["date"] for r in base}

    session = requests.Session()
    merged, status = collect(session)
    all_failed = not merged

    baseline = not seen and not all_failed
    fresh = sorted((r for d, r in merged.items() if d not in seen), key=lambda r: r["date"], reverse=True)
    if baseline:                       # 1re exécution : on n'inonde pas la boîte mail
        seen.update(merged)
        fresh = fresh[:1]
    else:
        seen.update(r["date"] for r in fresh)

    for r in fresh:                    # enrichissement (lois/accords/nominations) via cg.gov.ma
        if r.get("url", "").startswith(CG_URL.split("/ar/")[0]):
            try:
                r.update({k: v for k, v in parse_cg_detail(http_get(session, r["url"], tries=2)).items() if v})
            except Exception as e:  # noqa: BLE001
                log.warning("Détail indisponible pour %s : %s", r["date"], e)

    for d, r in merged.items():
        if d not in known:
            base.append(r)
    for r in fresh:
        base = [b for b in base if b["date"] != r["date"]] + [r]
    base.sort(key=lambda r: r["date"], reverse=True)

    if not all_failed:                 # ne jamais écraser l'état si tout a échoué
        save_json(SEEN_FILE, sorted(seen))
        save_json(RESULTS_FILE, base)

    log.info("Nouveaux : %d | en base : %d | statut : %s", len(fresh), len(base), status)
    try:
        send_email(*build_email(fresh, status, all_failed, baseline))
    except Exception as e:  # noqa: BLE001
        log.error("Erreur envoi email : %s", e)

    if all_failed:
        sys.exit(1)                    # le job GitHub passe au rouge -> visible


if __name__ == "__main__":
    main()
