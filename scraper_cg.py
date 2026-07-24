#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent de scraping pour les Conseils du Gouvernement (cg.gov.ma)
Utilise Selenium avec undetected-chromedriver pour contourner Cloudflare.
"""

import os
import sys
import json
import re
import smtplib
import logging
import time
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import undetected_chromedriver as uc
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ============================================================================
# CONFIGURATION
# ============================================================================

BASE_URL = "https://www.cg.gov.ma"
LIST_URL = "/ar/%D9%85%D8%AC%D9%84%D8%B3-%D8%A7%D9%84%D8%AD%D9%83%D9%88%D9%85%D8%A9"

DATA_DIR = Path("data_cg")
DATA_DIR.mkdir(exist_ok=True)
SEEN_FILE = DATA_DIR / "conseils_vus.json"
RESULTS_FILE = DATA_DIR / "conseils.json"
LOG_FILE = DATA_DIR / "scraper_cg.log"

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

def load_seen():
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
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
    mois_arabe = {
        "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4,
        "ماي": 5, "يونيو": 6, "يوليوز": 7, "غشت": 8,
        "شتنبر": 9, "أكتوبر": 10, "نونبر": 11, "دجنبر": 12,
        "يوليو": 7, "أغسطس": 8, "سبتمبر": 9, "نوفمبر": 11, "ديسمبر": 12
    }
    date_str = date_str.strip()
    date_str = re.sub(r'^(الأحد|الاثنين|الثلاثاء|الأربعاء|الخميس|الجمعة|السبت)\s+', '', date_str)
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
    return None

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_list_items(soup, element_id):
    container = soup.find(id=element_id)
    if not container:
        return []
    items = []
    for li in container.find_all("li"):
        text = clean_text(li.get_text(separator="\n"))
        if text:
            items.append(text)
    return items

# ============================================================================
# SCRAPING avec Selenium
# ============================================================================

def get_page_with_selenium(url, max_retries=3):
    """
    Récupère le HTML d'une page avec undetected-chromedriver.
    """
    for attempt in range(max_retries):
        driver = None
        try:
            options = uc.ChromeOptions()
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1280,800')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option('excludeSwitches', ['enable-automation'])
            options.add_experimental_option('useAutomationExtension', False)
            
            driver = uc.Chrome(options=options, headless=True)
            
            driver.get(url)
            # Attendre que le contenu apparaisse (max 30s)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CLASS_NAME, "article-format"))
            )
            html = driver.page_source
            return html
        except Exception as e:
            logger.error(f"Tentative {attempt+1}/{max_retries} échouée pour {url}: {e}")
            time.sleep(5)
        finally:
            if driver:
                driver.quit()
    return None

def get_liste_conseils_using_selenium(page=0):
    url = f"{BASE_URL}{LIST_URL}"
    if page > 0:
        url += f"?page={page}"
    logger.info(f"Scraping avec Selenium: {url}")
    html = get_page_with_selenium(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    conseils = []
    articles = soup.find_all("div", class_="article-format c-gov-img-wrp")
    logger.info(f"  → {len(articles)} conseils trouvés sur cette page")
    for article in articles:
        link = article.find("a", href=True)
        if not link:
            continue
        href = link.get("href")
        if href.startswith("/"):
            detail_url = f"{BASE_URL}{href}"
        else:
            detail_url = href
        node_match = re.search(r"/node/(\d+)", detail_url)
        if not node_match:
            continue
        node_id = node_match.group(1)
        title_elem = article.find("h4", class_="h4")
        titre = clean_text(title_elem.get_text()) if title_elem else ""
        date_elem = article.find("span", class_="date")
        date_text = clean_text(date_elem.get_text()) if date_elem else ""
        date_obj = parse_arabic_date(date_text) if date_text else None
        p_elem = article.find("p")
        extrait = clean_text(p_elem.get_text()) if p_elem else ""
        conseils.append({
            "id": node_id,
            "url": detail_url,
            "titre": titre,
            "date_text": date_text,
            "date": date_obj.isoformat() if date_obj else "",
            "extrait": extrait
        })
    return conseils

def get_conseil_detail_using_selenium(url):
    logger.info(f"  Détails: {url}")
    html = get_page_with_selenium(url)
    if not html:
        return {"lois": [], "accords": [], "nominations": [], "pdf_url": None, "pdf_nom": "", "titre": "", "date_text": "", "date": "", "contenu": ""}
    soup = BeautifulSoup(html, "html.parser")
    result = {
        "lois": [],
        "accords": [],
        "nominations": [],
        "pdf_url": None,
        "pdf_nom": "",
        "description": "",
        "titre": "",
        "date_text": "",
        "date": "",
        "contenu": ""
    }
    title_elem = soup.find("h1", class_="h1")
    if title_elem:
        result["titre"] = clean_text(title_elem.get_text())
    date_elem = soup.find("span", class_="date")
    if date_elem:
        date_text = clean_text(date_elem.get_text())
        result["date_text"] = date_text
        date_obj = parse_arabic_date(date_text)
        if date_obj:
            result["date"] = date_obj.isoformat()
    content_div = soup.find("div", id="read_content")
    if content_div:
        result["contenu"] = clean_text(content_div.get_text(separator="\n"))
    result["lois"] = extract_list_items(soup, "loi")
    result["accords"] = extract_list_items(soup, "agreement")
    result["nominations"] = extract_list_items(soup, "nomination")
    pdf_link = None
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = clean_text(a.get_text())
        if "البلاغ الصحفي" in text or "COMM" in href:
            if href.endswith(".pdf") or ".pdf" in href:
                if href.startswith("/"):
                    pdf_link = f"{BASE_URL}{href}"
                else:
                    pdf_link = href
                result["pdf_nom"] = text
                break
    result["pdf_url"] = pdf_link
    return result

# ============================================================================
# EXÉCUTION PRINCIPALE
# ============================================================================

def run_scraper():
    logger.info("=" * 60)
    logger.info("DÉMARRAGE DU SCRAPER Conseil du Gouvernement (cg.gov.ma) - Selenium")
    logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    seen = load_seen()
    logger.info(f"Conseils déjà traités: {len(seen)}")

    all_results = load_results()
    new_results = []

    total_traites = 0
    total_nouveaux = 0

    for page in range(1):  # 1 page pour test
        conseils = get_liste_conseils_using_selenium(page)
        if not conseils:
            break

        for conseil in conseils:
            total_traites += 1
            cid = conseil["id"]

            if cid in seen:
                logger.info(f"  [DÉJÀ VU] {conseil['titre'][:50]}...")
                continue

            seen.add(cid)
            logger.info(f"  [NOUVEAU] {conseil['titre'][:50]}...")

            details = get_conseil_detail_using_selenium(conseil["url"])

            result = {
                "id": cid,
                "url": conseil["url"],
                "titre": details.get("titre") or conseil["titre"],
                "date_text": details.get("date_text") or conseil["date_text"],
                "date": details.get("date") or conseil["date"],
                "extrait": conseil["extrait"],
                "description": details.get("contenu", ""),
                "lois": details.get("lois", []),
                "accords": details.get("accords", []),
                "nominations": details.get("nominations", []),
                "pdf_url": details.get("pdf_url"),
                "pdf_nom": details.get("pdf_nom", ""),
                "date_detection": datetime.now().isoformat()
            }

            new_results.append(result)
            all_results.append(result)
            total_nouveaux += 1

    save_seen(seen)
    save_results(all_results)

    logger.info(f"\n{'=' * 60}")
    logger.info("RÉSUMÉ")
    logger.info(f"{'=' * 60}")
    logger.info(f"Conseils analysés: {total_traites}")
    logger.info(f"Nouveaux conseils: {total_nouveaux}")
    logger.info(f"Total en base: {len(all_results)}")

    return new_results, all_results, total_traites, total_nouveaux

# ============================================================================
# ENVOI D'EMAIL (inchangé)
# ============================================================================

def send_email_report(new_results, all_results, total_traites, total_nouveaux):
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        logger.warning("Configuration email incomplète, pas d'envoi.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        if new_results:
            msg["Subject"] = f"[Conseil du Gouvernement] {len(new_results)} nouveau(x) conseil(s) - {date.today().isoformat()}"
        else:
            msg["Subject"] = f"[Conseil du Gouvernement] Aucun nouveau conseil - {date.today().isoformat()}"
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO

        text_body = f"""
Agent Conseil du Gouvernement (cg.gov.ma) - Rapport du {date.today().isoformat()}
{'=' * 60}

STATISTIQUES:
- Conseils analysés: {total_traites}
- Nouveaux conseils: {total_nouveaux}
- Total en base: {len(all_results)}

"""

        if new_results:
            text_body += "NOUVEAUX CONSEILS:\n\n"
            for i, r in enumerate(new_results, 1):
                text_body += f"""
--- Conseil {i} ---
Titre: {r['titre']}
Date: {r['date_text']}
Extrait: {r['extrait']}
Lien: {r['url']}
PDF: {r['pdf_url'] if r['pdf_url'] else 'Non disponible'}

🔹 Lois / Décrets ({len(r['lois'])}):
"""
                for loi in r['lois']:
                    text_body += f"  - {loi}\n"

                text_body += f"""
🔹 Accords / Conventions ({len(r['accords'])}):
"""
                for accord in r['accords']:
                    text_body += f"  - {accord}\n"

                text_body += f"""
🔹 Nominations ({len(r['nominations'])}):
"""
                for nom in r['nominations']:
                    text_body += f"  - {nom}\n"

                text_body += "\n" + "-" * 40 + "\n"
        else:
            text_body += "Aucun nouveau conseil trouvé.\n"

        html_body = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: Arial, sans-serif; direction: rtl; }}
.header {{ background: #056e52; color: white; padding: 20px; border-radius: 8px; }}
.stats {{ background: #f0f0f0; padding: 15px; border-radius: 8px; margin: 15px 0; }}
.stat-item {{ display: inline-block; margin: 5px 15px; }}
.stat-value {{ font-size: 24px; font-weight: bold; color: #056e52; }}
.stat-label {{ font-size: 12px; color: #666; }}
.conseil {{ border: 1px solid #ddd; margin: 15px 0; padding: 15px; border-radius: 8px; background: #f9f9f9; }}
.titre {{ color: #056e52; font-size: 18px; font-weight: bold; }}
.date {{ color: #888; font-size: 14px; }}
.section {{ margin-top: 10px; background: #e8f0fe; padding: 10px; border-radius: 5px; }}
.section h4 {{ margin: 0 0 5px 0; color: #056e52; }}
.item {{ margin: 3px 0; padding: 3px 10px; background: #fff; border-radius: 3px; }}
.footer {{ margin-top: 30px; padding: 15px; background: #eee; border-radius: 8px; text-align: center; }}
</style>
</head>
<body>
<div class="header"><h2>📋 Agent Conseil du Gouvernement</h2><p>Rapport du {date.today().isoformat()}</p></div>
<div class="stats">
<div class="stat-item"><div class="stat-value">{total_traites}</div><div class="stat-label">Conseils analysés</div></div>
<div class="stat-item"><div class="stat-value">{total_nouveaux}</div><div class="stat-label">Nouveaux</div></div>
<div class="stat-item"><div class="stat-value">{len(all_results)}</div><div class="stat-label">Total en base</div></div>
</div>
"""
        if new_results:
            for r in new_results:
                html_body += f"""
<div class="conseil">
<div class="titre">{r['titre']}</div>
<div class="date">📅 {r['date_text']}</div>
<div class="extrait">{r['extrait']}</div>
<div class="info"><a href="{r['url']}">🔗 Lire la suite</a> | <a href="{r['pdf_url'] if r['pdf_url'] else '#'}">📄 Télécharger le PDF</a></div>
"""
                if r['lois']:
                    html_body += f"""
<div class="section"><h4>📜 مراسيم و قوانين</h4>
"""
                    for loi in r['lois']:
                        html_body += f'<div class="item">• {loi}</div>'
                    html_body += "</div>"

                if r['accords']:
                    html_body += f"""
<div class="section"><h4>🤝 اتفاقيات و معاهدات</h4>
"""
                    for accord in r['accords']:
                        html_body += f'<div class="item">• {accord}</div>'
                    html_body += "</div>"

                if r['nominations']:
                    html_body += f"""
<div class="section"><h4>👤 تعيينات</h4>
"""
                    for nom in r['nominations']:
                        html_body += f'<div class="item">• {nom}</div>'
                    html_body += "</div>"

                html_body += "</div>"
        else:
            html_body += """
<div style="background: #fff3cd; padding: 20px; border-radius: 8px; text-align: center;">
<h3>📭 Aucun nouveau conseil</h3>
</div>
"""

        html_body += f"""
<div class="footer">
<p><em>Agent automatique - cg.gov.ma</em></p>
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
    try:
        new_results, all_results, total_traites, total_nouveaux = run_scraper()
        send_email_report(new_results, all_results, total_traites, total_nouveaux)
        logger.info("\nScraper terminé.")
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
        sys.exit(1)
