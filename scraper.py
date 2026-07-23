#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent de scraping pour emploi-public.ma - VERSION ARABE
Scanne toutes les annonces en cours et les regroupe par catégorie puis par administration.
"""

import os
import sys
import json
import re
import smtplib
import tempfile
import logging
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from collections import defaultdict

import requests
from bs4 import BeautifulSoup
import pdfplumber

# ============================================================================
# CONFIGURATION
# ============================================================================

BASE_URL = "https://www.emploi-public.ma"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,fr;q=0.9,en;q=0.8",
}

CATEGORIES = [
    {"name": "مناصب المسؤولية", "slug": "قائمة-مناصب-المسؤولية"},
    {"name": "المناصب العليا", "slug": "قائمة-المناصب-العليا"},
    {"name": "المباريات", "slug": "قائمة-المباريات"},
    {"name": "تشغيل الخبراء", "slug": "قائمة-تشغيل-الخبراء"}
]

MAX_PAGES = 3

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SEEN_FILE = DATA_DIR / "annonces_vues.json"
RESULTS_FILE = DATA_DIR / "resultats.json"
LOG_FILE = DATA_DIR / "scraper.log"

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def load_seen_annonces():
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen_annonces(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)

def load_results():
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_results(results):
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

def parse_arabic_date(date_str):
    """
    Parse une date arabe marocaine.
    Exemples: "17 يوليوز 2026", "1 غشت 2026", "30/12/2026"
    """
    mois_arabe = {
        "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4,
        "ماي": 5, "يونيو": 6, "يوليوز": 7, "غشت": 8,
        "شتنبر": 9, "أكتوبر": 10, "نونبر": 11, "دجنبر": 12,
        "يوليو": 7, "أغسطس": 8, "سبتمبر": 9, "نوفمبر": 11, "ديسمبر": 12
    }
    date_str = date_str.strip()
    date_str = re.sub(r'^.*?:\s*', '', date_str)
    date_str = re.sub(r'(\d+)(er|ère|ème)?', r'\1', date_str)

    pattern = r"(\d{1,2})\s+([\u0621-\u064A]+)\s+(\d{4})"
    match = re.search(pattern, date_str)
    if match:
        jour = int(match.group(1))
        mois_nom = match.group(2)
        annee = int(match.group(3))
        mois = mois_arabe.get(mois_nom)
        if mois:
            try:
                return date(annee, mois, jour)
            except ValueError:
                pass

    match2 = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", date_str)
    if match2:
        jour, mois, annee = map(int, match2.groups())
        try:
            return date(annee, mois, jour)
        except ValueError:
            pass

    logger.warning(f"Format de date non reconnu: {date_str}")
    return None

def is_date_en_cours(deadline_date):
    return deadline_date and deadline_date >= date.today()

def extract_text_from_pdf(pdf_url):
    try:
        response = requests.get(pdf_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(response.content)
            tmp_path = tmp.name
        text = ""
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        os.unlink(tmp_path)
        return text
    except Exception as e:
        logger.error(f"Erreur extraction PDF {pdf_url}: {e}")
        return ""

# ============================================================================
# SCRAPING
# ============================================================================

def get_liste_annonces(category_slug, page=0):
    url = f"{BASE_URL}/ar/{category_slug}"
    if page > 0:
        url += f"?page={page}"
    logger.info(f"Scraping page: {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        annonces = []

        items = soup.find_all("div", class_="s-item")
        logger.debug(f"Nombre d'items trouvés: {len(items)}")
        for item in items:
            link = item.find("a", href=True)
            if not link:
                continue
            href = link.get("href", "")
            uuid_match = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", href)
            if not uuid_match:
                continue
            uuid = uuid_match.group(0)

            if href.startswith("/"):
                detail_url = f"{BASE_URL}{href}"
            elif href.startswith("http"):
                detail_url = href
            else:
                detail_url = f"{BASE_URL}/ar/{href}"

            titre_elem = item.find("h2", class_="card-title")
            titre = titre_elem.get_text(strip=True) if titre_elem else ""

            admin_elem = item.find("div", class_="card-text")
            administration = admin_elem.get_text(strip=True) if admin_elem else ""

            date_text = ""
            footer = item.find("div", class_="card-footer")
            if footer:
                for div in footer.find_all("div"):
                    text = div.get_text(strip=True)
                    if "آخر أجل" in text:
                        match = re.search(r'آخر أجل[^:]*:\s*(.+)$', text)
                        if match:
                            date_text = match.group(1).strip()
                        break

            annonces.append({
                "uuid": uuid,
                "titre": titre,
                "administration": administration,
                "date_limite_text": date_text,
                "detail_url": detail_url,
                "categorie": category_slug
            })

        logger.info(f"  → {len(annonces)} annonces trouvées sur cette page")
        return annonces
    except Exception as e:
        logger.error(f"Erreur scraping page {url}: {e}")
        return []

def get_annonce_detail(detail_url):
    logger.info(f"  Détails: {detail_url}")
    try:
        response = requests.get(detail_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        result = {
            "date_limite": None,
            "date_limite_text": "",
            "pdf_url": None,
            "pdf_nom": "",
            "administration": "",
            "description": "",
            "page_text": ""
        }
        page_text = soup.get_text(separator=" ", strip=True)
        result["page_text"] = page_text

        sidebar = soup.find("div", class_="s-content-box")
        if sidebar:
            for h3 in sidebar.find_all("h3", class_="h4"):
                span = h3.find("span")
                if span and "آخر أجل" in span.get_text():
                    date_text = h3.get_text(strip=True).replace(span.get_text(strip=True), "").strip()
                    if date_text:
                        result["date_limite_text"] = date_text
                        result["date_limite"] = parse_arabic_date(date_text)
                    break

        if not result["date_limite_text"]:
            match = re.search(r"آخر أجل[^:]*:\s*([0-9]{1,2}\s+[\u0621-\u064A]+\s+[0-9]{4})", page_text)
            if match:
                result["date_limite_text"] = match.group(1).strip()
                result["date_limite"] = parse_arabic_date(result["date_limite_text"])
        if not result["date_limite_text"]:
            match2 = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", page_text)
            if match2:
                result["date_limite_text"] = match2.group(0)
                result["date_limite"] = parse_arabic_date(result["date_limite_text"])

        # PDF
        pdf_links = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            link_text = link.get_text(strip=True)
            if ("arrete" in href.lower() or
                ".pdf" in href.lower() or
                "قرار" in link_text or
                "فتح" in link_text or
                "ترشيح" in link_text):
                if href.startswith("/"):
                    full_url = f"{BASE_URL}{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    full_url = f"{BASE_URL}/ar/{href}"
                score = 0
                if "arrete" in href.lower():
                    score += 10
                if "قرار" in link_text:
                    score += 10
                if "فتح" in link_text:
                    score += 5
                if "باب" in link_text:
                    score += 5
                if "ترشيح" in link_text:
                    score += 5
                if ".pdf" in href.lower():
                    score += 3
                pdf_links.append({"url": full_url, "text": link_text, "score": score})

        if pdf_links:
            pdf_links.sort(key=lambda x: x["score"], reverse=True)
            best = pdf_links[0]
            result["pdf_url"] = best["url"]
            result["pdf_nom"] = best["text"]
            logger.info(f"    PDF trouvé: {best['text']} (score: {best['score']})")
        else:
            logger.info("    Aucun PDF trouvé")

        if sidebar:
            for h3 in sidebar.find_all("h3", class_="h4"):
                span = h3.find("span")
                if span and "الإدارة المنظمة" in span.get_text():
                    admin_text = h3.get_text(strip=True).replace(span.get_text(strip=True), "").strip()
                    if admin_text:
                        result["administration"] = admin_text
                    break
        if not result["administration"]:
            match = re.search(r"الإدارة المنظمة\s*[:]?\s*(.+?)(?:\n|$)", page_text)
            if match:
                result["administration"] = match.group(1).strip()

        return result
    except Exception as e:
        logger.error(f"Erreur détails annonce {detail_url}: {e}")
        return {"date_limite": None, "date_limite_text": "", "pdf_url": None, "pdf_nom": "", "administration": "", "description": "", "page_text": ""}

# ============================================================================
# EXÉCUTION PRINCIPALE
# ============================================================================

def run_scraper():
    logger.info("=" * 60)
    logger.info("DÉMARRAGE DU SCRAPER emploi-public.ma (AR) - MODE TOUTES ANNONCES EN COURS")
    logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    seen = load_seen_annonces()
    logger.info(f"Annonces déjà traitées: {len(seen)}")

    all_results = load_results()
    new_results = []  # toutes les annonces en cours

    total_traitees = 0
    total_en_cours = 0
    total_expirees = 0

    for category in CATEGORIES:
        logger.info(f"\n--- Catégorie: {category['name']} ---")
        for page in range(MAX_PAGES):
            logger.info(f"Page {page + 1}/{MAX_PAGES}")
            annonces = get_liste_annonces(category["slug"], page)
            if not annonces:
                logger.info("  Aucune annonce trouvée, arrêt.")
                break

            for annonce in annonces:
                total_traitees += 1
                uuid = annonce["uuid"]

                # On ne marque pas comme vu pour l'instant, on veut tout afficher
                # Mais on peut quand même éviter de re-scrapper en détail si déjà vu
                if uuid in seen:
                    logger.info(f"  [DÉJÀ VU] {annonce['titre'][:60]}...")
                    continue

                seen.add(uuid)
                details = get_annonce_detail(annonce["detail_url"])

                if not details["date_limite_text"] and annonce["date_limite_text"]:
                    details["date_limite_text"] = annonce["date_limite_text"]
                    details["date_limite"] = parse_arabic_date(annonce["date_limite_text"])

                if not details["date_limite"]:
                    logger.info(f"  [PAS DE DATE] {annonce['titre'][:60]}...")
                    continue

                if not is_date_en_cours(details["date_limite"]):
                    total_expirees += 1
                    logger.info(f"  [EXPIRÉE] {annonce['titre'][:60]}... → {details['date_limite']}")
                    continue

                total_en_cours += 1
                logger.info(f"  [EN COURS] {annonce['titre'][:60]}... → {details['date_limite']}")

                # On construit le résultat sans filtre de région
                result = {
                    "uuid": uuid,
                    "titre": annonce["titre"],
                    "administration": details["administration"] or annonce["administration"],
                    "categorie": category["name"],
                    "date_limite": details["date_limite"].isoformat(),
                    "date_limite_text": details["date_limite_text"],
                    "detail_url": annonce["detail_url"],
                    "pdf_url": details["pdf_url"],
                    "pdf_nom": details["pdf_nom"],
                    "date_detection": datetime.now().isoformat(),
                    "description": details["description"]
                }
                new_results.append(result)
                all_results.append(result)

    save_seen_annonces(seen)
    save_results(all_results)

    logger.info(f"\n{'=' * 60}")
    logger.info("RÉSUMÉ")
    logger.info(f"{'=' * 60}")
    logger.info(f"Annonces traitées: {total_traitees}")
    logger.info(f"Dates en cours: {total_en_cours}")
    logger.info(f"Dates expirées: {total_expirees}")
    logger.info(f"Nouveaux résultats: {len(new_results)}")
    logger.info(f"Total résultats en base: {len(all_results)}")

    return new_results, all_results, total_traitees, total_en_cours

# ============================================================================
# ENVOI D'EMAIL AVEC GROUPEMENT
# ============================================================================

def send_email_report(new_results, all_results, total_traitees, total_en_cours):
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        logger.warning("Configuration email incomplète, pas d'envoi.")
        return False

    try:
        # Regrouper par catégorie puis par administration
        grouped = {}
        for r in new_results:
            cat = r.get('categorie', 'Autre')
            if cat not in grouped:
                grouped[cat] = {}
            admin = r.get('administration', 'Administration inconnue')
            if admin not in grouped[cat]:
                grouped[cat][admin] = []
            grouped[cat][admin].append(r)

        msg = MIMEMultipart("alternative")
        if new_results:
            msg["Subject"] = f"[Emploi Public AR] {len(new_results)} annonces en cours - {date.today().isoformat()}"
        else:
            msg["Subject"] = f"[Emploi Public AR] Aucune annonce en cours - {date.today().isoformat()}"
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO

        # Texte brut
        text_body = f"""
Agent Emploi-Public.ma (AR) - Rapport du {date.today().isoformat()}
{'=' * 60}

STATISTIQUES:
- Annonces analysées: {total_traitees}
- Annonces en cours: {total_en_cours}
- Total en base: {len(all_results)}

"""
        if new_results:
            text_body += "ANNONCES EN COURS (classées par catégorie et administration):\n\n"
            for cat, admins in grouped.items():
                text_body += f"\n--- {cat} ---\n"
                for admin, annonces in admins.items():
                    text_body += f"\n  ** {admin} **\n"
                    for i, r in enumerate(annonces, 1):
                        text_body += f"""
    {i}. {r['titre']}
       Date limite: {r['date_limite_text']} ({r['date_limite']})
       Lien: {r['detail_url']}
       PDF: {r['pdf_url'] if r['pdf_url'] else 'Non disponible'}

"""
        else:
            text_body += "Aucune annonce en cours trouvée.\n"

        # HTML
        html_body = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: Arial, sans-serif; direction: rtl; }}
.header {{ background: #1a5276; color: white; padding: 20px; border-radius: 8px; }}
.stats {{ background: #f0f0f0; padding: 15px; border-radius: 8px; margin: 15px 0; }}
.stat-item {{ display: inline-block; margin: 5px 15px; }}
.stat-value {{ font-size: 24px; font-weight: bold; color: #1a5276; }}
.stat-label {{ font-size: 12px; color: #666; }}
.categorie {{ margin-top: 25px; background: #e8f0fe; padding: 10px; border-radius: 5px; }}
.administration {{ margin-top: 15px; background: #f0f8ff; padding: 10px; border-radius: 5px; }}
.annonce {{ border: 1px solid #ddd; margin: 10px 0; padding: 10px; border-radius: 5px; background: #f9f9f9; }}
.titre {{ color: #1a5276; font-weight: bold; }}
.footer {{ margin-top: 30px; padding: 15px; background: #eee; border-radius: 8px; text-align: center; }}
</style>
</head>
<body>
<div class="header"><h2>📋 Agent Emploi-Public.ma (AR)</h2><p>Rapport du {date.today().isoformat()}</p></div>
<div class="stats">
<div class="stat-item"><div class="stat-value">{total_traitees}</div><div class="stat-label">Annonces analysées</div></div>
<div class="stat-item"><div class="stat-value">{total_en_cours}</div><div class="stat-label">En cours</div></div>
<div class="stat-item"><div class="stat-value">{len(all_results)}</div><div class="stat-label">Total en base</div></div>
</div>
"""
        if new_results:
            for cat, admins in grouped.items():
                html_body += f'<div class="categorie"><h3>{cat}</h3>'
                for admin, annonces in admins.items():
                    html_body += f'<div class="administration"><h4>{admin}</h4>'
                    for r in annonces:
                        html_body += f"""
<div class="annonce">
<div class="titre">{r['titre']}</div>
<div class="info"><span class="label">آخر أجل:</span> {r['date_limite_text']}</div>
<div class="info"><a href="{r['detail_url']}">🔗 Voir l'annonce</a> | <a href="{r['pdf_url'] if r['pdf_url'] else '#'}">📄 Télécharger le PDF</a></div>
</div>
"""
                    html_body += '</div>'
                html_body += '</div>'
        else:
            html_body += '<div style="background: #fff3cd; padding: 20px; border-radius: 8px;"><h3>📭 Aucune annonce en cours</h3></div>'

        html_body += f"""
<div class="footer">
<p><em>Agent automatique - Emploi-Public.ma</em></p>
</div>
</body>
</html>"""

        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info(f"Email envoyé à {EMAIL_TO}")
        return True
    except Exception as e:
        logger.error(f"Erreur envoi email: {e}")
        return False

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    new_results, all_results, total_traitees, total_en_cours = run_scraper()
    send_email_report(new_results, all_results, total_traitees, total_en_cours)
    logger.info("\nScraper terminé.")
