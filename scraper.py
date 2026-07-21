#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent de scraping pour emploi-public.ma - VERSION CORRIGÉE
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
    {"name": "مناصب المسؤولية", "slug": "قائمة-مناصب-المسؤولية", "detail_slug": "مناصب-المسؤولية"},
    {"name": "المناصب العليا", "slug": "قائمة-المناصب-العليا", "detail_slug": "المناصب-العليا"},
    {"name": "المباريات", "slug": "قائمة-المباريات", "detail_slug": "المباريات"},
    {"name": "تشغيل الخبراء", "slug": "قائمة-تشغيل-الخبراء", "detail_slug": "تشغيل-الخبراء"}
]

MAX_PAGES = 3

PROVINCES_SOUSS_MASSA = [
    "أكادير", "إداوتنان", "إنزكان", "آيت ملول", "تارودانت",
    "تيزنيت", "شتوكة", "آيت باها", "أكادير إداوتنان",
    "إنزكان آيت ملول", "شتوكة آيت باها", "سوس", "سوس ماسة",
    "أكادير أيت ملول", "تارودانت", "تيزنيت", "أكادير-إداوتنان",
    "إنزكان-آيت-ملول", "شتوكة-آيت-باها"
]

PROVINCES_GUELMIM_OUED_NOUN = [
    "كلميم", "أسا الزاك", "طرفاية", "طانطان", "سيدي إفني",
    "أسا", "الزاك", "كلميم واد نون", "كلميم-واد-نون",
    "كلميم واد نون", "أسا-الزاك", "كلميم-واد-نون",
    "سيدي-إفني", "طانطان", "طرفاية"
]

REGIONS_CIBLES = PROVINCES_SOUSS_MASSA + PROVINCES_GUELMIM_OUED_NOUN

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
    mois_arabe = {
        "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4,
        "ماي": 5, "يونيو": 6, "يوليوز": 7, "غشت": 8,
        "شتنبر": 9, "أكتوبر": 10, "نونبر": 11, "دجنبر": 12,
        "يوليو": 7, "أغسطس": 8, "سبتمبر": 9, "نوفمبر": 11, "ديسمبر": 12
    }
    date_str = date_str.strip()
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
                logger.warning(f"Date invalide: {date_str}")
                return None
    logger.warning(f"Format de date non reconnu: {date_str}")
    return None


def is_date_en_cours(deadline_date):
    if not deadline_date:
        return False
    return deadline_date >= date.today()


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


def check_region_in_text(text):
    if not text:
        return None
    text_normalized = text.lower().replace("-", " ").replace("_", " ")
    for province in REGIONS_CIBLES:
        province_normalized = province.lower().replace("-", " ").replace("_", " ")
        if province_normalized in text_normalized:
            return province
    if "سوس" in text or "ماسة" in text:
        return "Région Souss-Massa (détectée)"
    if "كلميم" in text or "واد نون" in text or "واد النون" in text:
        return "Région Guelmim-Oued Noun (détectée)"
    return None


# ============================================================================
# FONCTIONS DE SCRAPING - VERSION CORRIGÉE
# ============================================================================

def get_liste_annonces(category_slug, page=0):
    """
    Récupère la liste des annonces d'une page de catégorie.
    VERSION CORRIGÉE - Parse le HTML réel du site.
    """
    url = f"{BASE_URL}/ar/{category_slug}"
    if page > 0:
        url += f"?page={page}"
    
    logger.info(f"Scraping page: {url}")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        annonces = []
        
        # Méthode 1: Chercher les liens vers les pages de détail
        all_links = soup.find_all("a", href=True)
        
        for link in all_links:
            href = link.get("href", "")
            
            # Chercher les liens de détail avec UUID
            uuid_match = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", href)
            if not uuid_match:
                continue
            
            annonce_uuid = uuid_match.group(0)
            
            # Construire l'URL complète
            if href.startswith("/"):
                detail_url = f"{BASE_URL}{href}"
            elif href.startswith("http"):
                detail_url = href
            else:
                detail_url = f"{BASE_URL}/ar/{href}"
            
            # Le titre est souvent dans le texte du lien ou dans un parent
            titre = link.get_text(strip=True)
            
            # Si le titre est vide, chercher dans les parents
            if not titre:
                parent = link.find_parent(["div", "li", "article", "td"])
                if parent:
                    strong = parent.find("strong")
                    if strong:
                        titre = strong.get_text(strip=True)
                    else:
                        titre = parent.get_text(strip=True)[:200]
            
            # Éviter les doublons
            if any(a["uuid"] == annonce_uuid for a in annonces):
                continue
            
            # Chercher la date limite dans le texte environnant
            date_text = ""
            parent = link.find_parent(["div", "li", "article", "td"])
            if parent:
                parent_text = parent.get_text()
                date_match = re.search(r"آخر أجل لإيداع ملفات الترشيح\s*:\s*([0-9]{1,2}\s+[\u0621-\u064A]+\s+[0-9]{4})", parent_text)
                if date_match:
                    date_text = date_match.group(1).strip()
            
            annonces.append({
                "uuid": annonce_uuid,
                "titre": titre,
                "administration": "",
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
            "description": ""
        }
        
        page_text = soup.get_text()
        
        # Chercher la date limite
        date_patterns = [
            r"آخر أجل لإيداع الترشيحات\s*[:]?s*([0-9]{1,2}\s+[\u0621-\u064A]+\s+[0-9]{4})",
            r"آخر أجل لإيداع ملفات الترشيح\s*[:]?s*([0-9]{1,2}\s+[\u0621-\u064A]+\s+[0-9]{4})",
            r"آخر أجل\s*[:]?s*([0-9]{1,2}\s+[\u0621-\u064A]+\s+[0-9]{4})",
        ]
        
        for pattern in date_patterns:
            date_match = re.search(pattern, page_text)
            if date_match:
                result["date_limite_text"] = date_match.group(1).strip()
                result["date_limite"] = parse_arabic_date(result["date_limite_text"])
                break
        
        # Chercher les PDF
        pdf_links = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            link_text = link.get_text(strip=True)
            
            if href.endswith(".pdf") or ".pdf" in href:
                if href.startswith("/"):
                    full_url = f"{BASE_URL}{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    full_url = f"{BASE_URL}/ar/{href}"
                
                pdf_links.append({
                    "url": full_url,
                    "text": link_text,
                    "score": 0
                })
        
        # Scorer les PDF
        for pdf in pdf_links:
            text_lower = pdf["text"].lower()
            if "قرار" in text_lower:
                pdf["score"] += 10
            if "فتح" in text_lower:
                pdf["score"] += 5
            if "باب" in text_lower:
                pdf["score"] += 5
            if "ترشيح" in text_lower:
                pdf["score"] += 5
        
        if pdf_links:
            pdf_links.sort(key=lambda x: x["score"], reverse=True)
            best_pdf = pdf_links[0]
            result["pdf_url"] = best_pdf["url"]
            result["pdf_nom"] = best_pdf["text"]
            logger.info(f"    PDF trouvé: {best_pdf['text']} (score: {best_pdf['score']})")
        
        # Administration
        admin_match = re.search(r"الإدارة المنظمة\s*[:]?s*(.+?)(?:\n|\r|$)", page_text)
        if admin_match:
            result["administration"] = admin_match.group(1).strip()
        
        return result
    
    except Exception as e:
        logger.error(f"Erreur détails annonce {detail_url}: {e}")
        return {"date_limite": None, "date_limite_text": "", "pdf_url": None, "pdf_nom": "", "administration": "", "description": ""}


# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================

def run_scraper():
    logger.info("=" * 60)
    logger.info("DÉMARRAGE DU SCRAPER emploi-public.ma")
    logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    seen = load_seen_annonces()
    logger.info(f"Annonces déjà traitées: {len(seen)}")
    
    all_results = load_results()
    new_results = []
    
    total_traitees = 0
    total_pdf_lus = 0
    total_match_region = 0
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
                
                if not details["pdf_url"]:
                    logger.info(f"    → Pas de PDF trouvé")
                    continue
                
                total_pdf_lus += 1
                logger.info(f"    → PDF: {details['pdf_url']}")
                
                pdf_text = extract_text_from_pdf(details["pdf_url"])
                
                if not pdf_text:
                    logger.info(f"    → PDF vide ou illisible")
                    continue
                
                region_trouvee = check_region_in_text(pdf_text)
                
                if region_trouvee:
                    total_match_region += 1
                    logger.info(f"    ✓✓✓ MATCH RÉGION: {region_trouvee}")
                    
                    result = {
                        "uuid": uuid,
                        "titre": annonce["titre"],
                        "administration": details["administration"] or annonce["administration"],
                        "categorie": category["name"],
                        "date_limite": details["date_limite"].isoformat() if details["date_limite"] else "",
                        "date_limite_text": details["date_limite_text"],
                        "detail_url": annonce["detail_url"],
                        "pdf_url": details["pdf_url"],
                        "pdf_nom": details["pdf_nom"],
                        "region_detectee": region_trouvee,
                        "date_detection": datetime.now().isoformat(),
                        "description": details["description"]
                    }
                    
                    new_results.append(result)
                    all_results.append(result)
                else:
                    logger.info(f"    → Pas de match région")
    
    save_seen_annonces(seen)
    save_results(all_results)
    
    logger.info(f"\n{'=' * 60}")
    logger.info("RÉSUMÉ")
    logger.info(f"{'=' * 60}")
    logger.info(f"Annonces traitées: {total_traitees}")
    logger.info(f"Dates en cours: {total_en_cours}")
    logger.info(f"Dates expirées: {total_expirees}")
    logger.info(f"PDF lus: {total_pdf_lus}")
    logger.info(f"Match région: {total_match_region}")
    logger.info(f"Nouveaux résultats: {len(new_results)}")
    logger.info(f"Total résultats en base: {len(all_results)}")
    
    return new_results, all_results, total_traitees, total_en_cours, total_pdf_lus, total_match_region


# ============================================================================
# FONCTIONS EMAIL - TOUJOURS ENVOYER UN RAPPORT
# ============================================================================

def send_email_report(new_results, all_results, total_traitees, total_en_cours, total_pdf_lus, total_match_region):
    """Envoie TOUJOURS un email, même avec 0 résultats."""
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        logger.warning("Configuration email incomplète, pas d'envoi.")
        return False
    
    try:
        msg = MIMEMultipart("alternative")
        
        if new_results:
            msg["Subject"] = f"[Emploi Public] {len(new_results)} nouvelle(s) annonce(s) - {date.today().isoformat()}"
        else:
            msg["Subject"] = f"[Emploi Public] Rapport quotidien - {date.today().isoformat()}"
        
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO
        
        # Corps texte
        text_body = f"""
Agent Emploi-Public.ma - Rapport du {date.today().isoformat()}
{'=' * 60}

STATISTIQUES DE CETTE EXÉCUTION:
- Annonces analysées: {total_traitees}
- Dates en cours: {total_en_cours}
- PDF lus: {total_pdf_lus}
- Match région: {total_match_region}
- Nouvelles annonces: {len(new_results)}
- Total en base: {len(all_results)}

"""
        
        if new_results:
            text_body += f"NOUVELLES ANNONCES TROUVÉES: {len(new_results)}\n\n"
            for i, r in enumerate(new_results, 1):
                text_body += f"""
--- Annonce {i} ---
Titre: {r['titre']}
Administration: {r['administration']}
Catégorie: {r['categorie']}
Date limite: {r['date_limite_text']} ({r['date_limite']})
Région détectée: {r['region_detectee']}
Lien: {r['detail_url']}
PDF: {r['pdf_url']}

"""
        else:
            text_body += "Aucune nouvelle annonce trouvée pour les régions cibles.\n"
            text_body += "Le bot fonctionne correctement et continuera à surveiller.\n"
        
        text_body += f"""
{'=' * 60}
RÉGIONS SURVEILLÉES:
- Souss-Massa: أكادير، تارودانت، تيزنيت، إنزكان، شتوكة...
- Guelmim-Oued Noun: كلميم، أسا الزاك، طرفاية، طانطان، سيدي إفني...

Prochaine exécution: dans 3 jours
"""
        
        # Corps HTML
        html_body = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: Arial, sans-serif; direction: rtl; }}
.header {{ background: #1a5276; color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
.stats {{ background: #f0f0f0; padding: 15px; border-radius: 8px; margin: 15px 0; }}
.stat-item {{ display: inline-block; margin: 5px 15px; }}
.stat-value {{ font-size: 24px; font-weight: bold; color: #1a5276; }}
.stat-label {{ font-size: 12px; color: #666; }}
.annonce {{ border: 1px solid #ddd; margin: 15px 0; padding: 15px; border-radius: 8px; background: #f9f9f9; }}
.titre {{ color: #1a5276; font-size: 18px; font-weight: bold; margin-bottom: 10px; }}
.info {{ margin: 5px 0; color: #333; }}
.label {{ font-weight: bold; color: #555; }}
.match {{ color: #27ae60; font-weight: bold; font-size: 16px; }}
.no-result {{ background: #fff3cd; padding: 20px; border-radius: 8px; text-align: center; margin: 20px 0; }}
.footer {{ margin-top: 30px; padding: 15px; background: #eee; border-radius: 8px; text-align: center; }}
a {{ color: #2980b9; }}
</style>
</head>
<body>
<div class="header">
<h2>📋 Agent Emploi-Public.ma</h2>
<p>Rapport du {date.today().isoformat()}</p>
</div>

<div class="stats">
<div class="stat-item"><div class="stat-value">{total_traitees}</div><div class="stat-label">Annonces analysées</div></div>
<div class="stat-item"><div class="stat-value">{total_en_cours}</div><div class="stat-label">En cours</div></div>
<div class="stat-item"><div class="stat-value">{total_pdf_lus}</div><div class="stat-label">PDF lus</div></div>
<div class="stat-item"><div class="stat-value">{total_match_region}</div><div class="stat-label">Match région</div></div>
<div class="stat-item"><div class="stat-value">{len(new_results)}</div><div class="stat-label">Nouvelles</div></div>
</div>
"""
        
        if new_results:
            html_body += f"<h3>✅ {len(new_results)} nouvelle(s) annonce(s) trouvée(s)</h3>"
            for r in new_results:
                html_body += f"""
<div class="annonce">
<div class="titre">{r['titre']}</div>
<div class="info"><span class="label">الإدارة:</span> {r['administration']}</div>
<div class="info"><span class="label">الفئة:</span> {r['categorie']}</div>
<div class="info"><span class="label">آخر أجل:</span> {r['date_limite_text']}</div>
<div class="info match">📍 {r['region_detectee']}</div>
<div class="info"><a href="{r['detail_url']}">🔗 Voir l'annonce</a> | <a href="{r['pdf_url']}">📄 Télécharger le PDF</a></div>
</div>
"""
        else:
            html_body += f"""
<div class="no-result">
<h3>📭 Aucune nouvelle annonce trouvée</h3>
<p>Le bot a analysé <strong>{total_traitees}</strong> annonces mais aucune ne correspond aux régions cibles.</p>
<p>Il continuera à surveiller automatiquement.</p>
</div>
"""
        
        html_body += f"""
<div class="footer">
<p>Total annonces en base: <strong>{len(all_results)}</strong></p>
<p><em>Agent automatique - Emploi-Public.ma</em></p>
<p>Prochaine exécution: dans 3 jours</p>
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
# POINT D'ENTRÉE
# ============================================================================

if __name__ == "__main__":
    new_results, all_results, total_traitees, total_en_cours, total_pdf_lus, total_match_region = run_scraper()
    
    # TOUJOURS envoyer un email, même avec 0 résultats
    send_email_report(new_results, all_results, total_traitees, total_en_cours, total_pdf_lus, total_match_region)
    
    logger.info("\nScraper terminé.")
