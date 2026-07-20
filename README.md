# 🤖 Agent Emploi-Public.ma

Agent de scraping automatique pour le site [emploi-public.ma](https://www.emploi-public.ma/ar/) qui surveille les annonces d'emploi public au Maroc et envoie des notifications par email pour les postes situés dans les régions **Souss-Massa** ou **Guelmim-Oued Noun**.

---

## 📋 Fonctionnalités

- 🔍 Scanne les 4 catégories d'annonces :
  - مناصب المسؤولية (805+ annonces)
  - المناصب العليا (331+ annonces)
  - المباريات
  - تشغيل الخبراء (37 annonces)
- 📄 Lit les 3 premières pages de chaque catégorie
- 📅 Vérifie la date limite : **آخر أجل لإيداع الترشيحات**
- 📑 Télécharge et analyse le PDF **"قرار فتح باب الترشيح"**
- 🗺️ Détecte les postes dans les provinces cibles :
  - **Souss-Massa** : أكادير، تارودانت، تيزنيت، إنزكان، شتوكة...
  - **Guelmim-Oued Noun** : كلميم، أسا الزاك، طرفاية، طانطان، سيدي إفني...
- 📧 Envoie un email récapitulatif avec les annonces trouvées
- 🔄 Exécution automatique tous les **3 jours** via GitHub Actions

---

## 🚀 Installation

### 1. Créer un dépôt GitHub

1. Va sur [github.com/new](https://github.com/new)
2. Nomme-le `emploi-public-scraper`
3. Coche **"Add a README file"** (optionnel)
4. Clique sur **Create repository**

### 2. Uploader les fichiers

Télécharge les fichiers de ce projet et upload-les dans ton dépôt :

Je vais te fournir le contenu de chaque fichier directement dans le message, afin que tu puisses les copier-coller dans ton dépôt GitHub.
📄 1. scraper.py (Code principal)
Python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent de scraping pour emploi-public.ma
Scanne les annonces d'emploi public marocain et envoie des notifications
par email pour les postes situés dans les régions Souss-Massa ou Guelmim-Oued Noun.
"""

import os
import sys
import json
import re
import hashlib
import smtplib
import tempfile
import logging
from datetime import datetime, date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from urllib.parse import urljoin, urlparse

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
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Catégories à scanner (3 premières pages chacune)
CATEGORIES = [
    {
        "name": "مناصب المسؤولية",
        "slug": "قائمة-مناصب-المسؤولية",
        "detail_slug": "مناصب-المسؤولية"
    },
    {
        "name": "المناصب العليا",
        "slug": "قائمة-المناصب-العليا",
        "detail_slug": "المناصب-العليا"
    },
    {
        "name": "المباريات",
        "slug": "قائمة-المباريات",
        "detail_slug": "المباريات"
    },
    {
        "name": "تشغيل الخبراء",
        "slug": "قائمة-تشغيل-الخبراء",
        "detail_slug": "تشغيل-الخبراء"
    }
]

# Nombre de pages à scanner par catégorie
MAX_PAGES = 3

# Provinces cibles
PROVINCES_SOUSS_MASSA = [
    "أكادير", "إداوتنان", "إنزكان", "آيت ملول", "تارودانت",
    "تيزنيت", "شتوكة", "آيت باها", "أكادير إداوتنان",
    "إنزكان آيت ملول", "شتوكة آيت باها", "سوس", "سوس ماسة",
    "أكادير أيت ملول", "تارودانت", "تيزنيت"
]

PROVINCES_GUELMIM_OUED_NOUN = [
    "كلميم", "أسا الزاك", "طرفاية", "طانطان", "سيدي إفني",
    "أسا", "الزاك", "كلميم واد نون", "كلميم-واد-نون",
    "كلميم واد نون", "أسا-الزاك"
]

REGIONS_CIBLES = PROVINCES_SOUSS_MASSA + PROVINCES_GUELMIM_OUED_NOUN

# Fichiers de données
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SEEN_FILE = DATA_DIR / "annonces_vues.json"
RESULTS_FILE = DATA_DIR / "resultats.json"
LOG_FILE = DATA_DIR / "scraper.log"

# Configuration email (depuis variables d'environnement)
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
    """Charge les UUID des annonces déjà traitées."""
    if SEEN_FILE.exists():
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_annonces(seen):
    """Sauvegarde les UUID des annonces traitées."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


def load_results():
    """Charge les résultats précédents."""
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_results(results):
    """Sauvegarde les résultats."""
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def parse_arabic_date(date_str):
    """
    Parse une date arabe marocaine.
    Exemples: "17 يوليوز 2026", "5 يناير 2026", "4 غشت 2026"
    """
    mois_arabe = {
        "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4,
        "ماي": 5, "يونيو": 6, "يوليوز": 7, "غشت": 8,
        "شتنبر": 9, "أكتوبر": 10, "نونبر": 11, "دجنبر": 12,
        "يوليو": 7, "أغسطس": 8, "سبتمبر": 9, "نوفمبر": 11, "ديسمبر": 12
    }
    
    # Nettoyer la chaîne
    date_str = date_str.strip()
    
    # Pattern: jour mois année
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
    """Vérifie si la date limite est encore en cours."""
    if not deadline_date:
        return False
    return deadline_date >= date.today()


def extract_text_from_pdf(pdf_url):
    """Télécharge et extrait le texte d'un PDF."""
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
    """
    Vérifie si le texte contient une province des régions cibles.
    Retourne la province trouvée ou None.
    """
    if not text:
        return None
    
    text_lower = text.lower()
    
    for province in REGIONS_CIBLES:
        if province.lower() in text_lower:
            return province
    
    # Recherche plus souple pour les noms composés
    if "سوس" in text or "ماسة" in text:
        return "Région Souss-Massa (détectée)"
    if "كلميم" in text or "واد نون" in text or "واد النون" in text:
        return "Région Guelmim-Oued Noun (détectée)"
    
    return None


# ============================================================================
# FONCTIONS DE SCRAPING
# ============================================================================

def get_liste_annonces(category_slug, page=0):
    """
    Récupère la liste des annonces d'une page de catégorie.
    Retourne une liste de dicts avec titre, URL, date_limite.
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
        
        # Rechercher les blocs d'annonces
        # Structure: div contenant le titre en gras, puis l'administration, puis la date
        content = soup.find("div", class_=re.compile("content|main|region-content"))
        if not content:
            content = soup
        
        # Les annonces sont dans des éléments avec des titres en gras
        for strong in content.find_all("strong"):
            parent = strong.parent
            if not parent:
                continue
            
            # Chercher le lien vers la page détail
            link = None
            for a in parent.find_all("a", href=True):
                href = a["href"]
                if "/تفاصيل/" in href or "/ar/تفاصيل/" in href:
                    link = href
                    break
            
            if not link:
                continue
            
            # URL complète
            if link.startswith("/"):
                detail_url = f"{BASE_URL}{link}"
            elif link.startswith("http"):
                detail_url = link
            else:
                detail_url = f"{BASE_URL}/ar/{link}"
            
            # Extraire l'UUID de l'URL
            uuid_match = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", detail_url)
            if not uuid_match:
                continue
            
            annonce_uuid = uuid_match.group(0)
            
            # Titre
            titre = strong.get_text(strip=True)
            
            # Administration (texte après le titre)
            admin = ""
            next_sibling = strong.find_next_sibling()
            if next_sibling:
                admin = next_sibling.get_text(strip=True)
            
            # Date limite - chercher dans le texte du parent
            date_text = ""
            date_pattern = r"آخر أجل لإيداع ملفات الترشيح\s*:\s*(.+?)(?:\n|\r|\t|$)"
            parent_text = parent.get_text()
            date_match = re.search(date_pattern, parent_text)
            if date_match:
                date_text = date_match.group(1).strip()
            
            annonces.append({
                "uuid": annonce_uuid,
                "titre": titre,
                "administration": admin,
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
    """
    Récupère les détails d'une annonce: date limite précise et lien PDF.
    Retourne un dict avec date_limite, pdf_url, etc.
    """
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
        
        # Chercher la date limite
        # Format: "آخر أجل لإيداع الترشيحات" ou "آخر أجل لإيداع ملفات الترشيح"
        for elem in soup.find_all(text=re.compile("آخر أجل")):
            parent = elem.parent
            if parent:
                # Le texte de la date est souvent dans le même parent ou le suivant
                full_text = parent.get_text()
                date_match = re.search(r"آخر أجل[^\n]*:\s*(.+?)(?:\n|\r|$)", full_text)
                if date_match:
                    result["date_limite_text"] = date_match.group(1).strip()
                    result["date_limite"] = parse_arabic_date(result["date_limite_text"])
                    break
        
        # Chercher le PDF dans "فضاء التحميل"
        download_section = None
        for heading in soup.find_all(["h2", "h3", "h4", "strong"]):
            if "فضاء التحميل" in heading.get_text():
                download_section = heading.find_parent("div")
                break
        
        if download_section:
            for link in download_section.find_all("a", href=True):
                href = link["href"]
                link_text = link.get_text(strip=True)
                
                if "قرار فتح باب الترشيح" in link_text or "قرار" in link_text:
                    if href.startswith("/"):
                        result["pdf_url"] = f"{BASE_URL}{href}"
                    elif href.startswith("http"):
                        result["pdf_url"] = href
                    else:
                        result["pdf_url"] = f"{BASE_URL}/ar/{href}"
                    result["pdf_nom"] = link_text
                    break
        
        # Si pas trouvé dans la section, chercher tous les liens PDF
        if not result["pdf_url"]:
            for link in soup.find_all("a", href=re.compile(r"\.pdf$", re.I)):
                href = link["href"]
                link_text = link.get_text(strip=True)
                if "قرار" in link_text or "فتح" in link_text:
                    if href.startswith("/"):
                        result["pdf_url"] = f"{BASE_URL}{href}"
                    elif href.startswith("http"):
                        result["pdf_url"] = href
                    result["pdf_nom"] = link_text
                    break
        
        # Administration
        for elem in soup.find_all(text=re.compile("الإدارة المنظمة")):
            parent = elem.parent
            if parent:
                admin_text = parent.get_text()
                admin_match = re.search(r"الإدارة المنظمة\s*:?\s*(.+?)(?:\n|\r|$)", admin_text)
                if admin_match:
                    result["administration"] = admin_match.group(1).strip()
                    break
        
        # Description
        content_div = soup.find("div", class_=re.compile("content|field-body"))
        if content_div:
            result["description"] = content_div.get_text(separator="\n", strip=True)[:500]
        
        return result
    
    except Exception as e:
        logger.error(f"Erreur détails annonce {detail_url}: {e}")
        return {"date_limite": None, "date_limite_text": "", "pdf_url": None, "pdf_nom": "", "administration": "", "description": ""}


# ============================================================================
# FONCTION PRINCIPALE
# ============================================================================

def run_scraper():
    """Fonction principale du scraper."""
    logger.info("=" * 60)
    logger.info("DÉMARRAGE DU SCRAPER emploi-public.ma")
    logger.info(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    # Charger les annonces déjà vues
    seen = load_seen_annonces()
    logger.info(f"Annonces déjà traitées: {len(seen)}")
    
    # Charger les résultats existants
    all_results = load_results()
    
    # Nouvelles annonces trouvées
    new_results = []
    
    total_traitees = 0
    total_pdf_lus = 0
    total_match_region = 0
    
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
                
                # Vérifier si déjà traitée
                if uuid in seen:
                    logger.info(f"  [DÉJÀ VU] {annonce['titre'][:60]}...")
                    continue
                
                seen.add(uuid)
                
                # Récupérer les détails
                details = get_annonce_detail(annonce["detail_url"])
                
                # Mettre à jour avec les infos de la liste si manquantes
                if not details["date_limite_text"] and annonce["date_limite_text"]:
                    details["date_limite_text"] = annonce["date_limite_text"]
                    details["date_limite"] = parse_arabic_date(annonce["date_limite_text"])
                
                # Vérifier la date
                if not details["date_limite"]:
                    logger.info(f"  [PAS DE DATE] {annonce['titre'][:60]}...")
                    continue
                
                if not is_date_en_cours(details["date_limite"]):
                    logger.info(f"  [EXPIRÉE] {annonce['titre'][:60]}... → {details['date_limite']}")
                    continue
                
                logger.info(f"  [EN COURS] {annonce['titre'][:60]}... → {details['date_limite']}")
                
                # Vérifier le PDF
                if not details["pdf_url"]:
                    logger.info(f"    → Pas de PDF trouvé")
                    continue
                
                total_pdf_lus += 1
                logger.info(f"    → PDF: {details['pdf_url']}")
                
                # Extraire le texte du PDF
                pdf_text = extract_text_from_pdf(details["pdf_url"])
                
                if not pdf_text:
                    logger.info(f"    → PDF vide ou illisible")
                    continue
                
                # Vérifier la région
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
    
    # Sauvegarder
    save_seen_annonces(seen)
    save_results(all_results)
    
    logger.info(f"\n{'=' * 60}")
    logger.info("RÉSUMÉ")
    logger.info(f"{'=' * 60}")
    logger.info(f"Annonces traitées: {total_traitees}")
    logger.info(f"PDF lus: {total_pdf_lus}")
    logger.info(f"Match région: {total_match_region}")
    logger.info(f"Nouveaux résultats: {len(new_results)}")
    logger.info(f"Total résultats en base: {len(all_results)}")
    
    return new_results, all_results


# ============================================================================
# FONCTIONS EMAIL
# ============================================================================

def send_email_notification(new_results, all_results):
    """Envoie un email récapitulatif."""
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        logger.warning("Configuration email incomplète, pas d'envoi.")
        logger.warning(f"SMTP_USER={SMTP_USER[:5] if SMTP_USER else 'VIDE'}..., EMAIL_TO={EMAIL_TO}")
        return False
    
    try:
        # Créer le message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Emploi Public] {len(new_results)} nouvelle(s) annonce(s) - {date.today().isoformat()}"
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO
        
        # Corps texte
        text_body = f"""
Agent Emploi-Public.ma - Rapport du {date.today().isoformat()}
{'=' * 50}

NOUVELLES ANNONCES TROUVÉES: {len(new_results)}

"""
        
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
        
        text_body += f"""
{'=' * 50}
TOTAL ANNONCES EN BASE: {len(all_results)}

Pour voir tous les résultats: consultez le fichier data/resultats.json
"""
        
        # Corps HTML
        html_body = f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: Arial, sans-serif; direction: rtl; }}
.annonce {{ border: 1px solid #ddd; margin: 15px 0; padding: 15px; border-radius: 8px; background: #f9f9f9; }}
.titre {{ color: #1a5276; font-size: 18px; font-weight: bold; margin-bottom: 10px; }}
.info {{ margin: 5px 0; color: #333; }}
.label {{ font-weight: bold; color: #555; }}
.match {{ color: #27ae60; font-weight: bold; font-size: 16px; }}
.header {{ background: #1a5276; color: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
.footer {{ margin-top: 30px; padding: 15px; background: #eee; border-radius: 8px; text-align: center; }}
a {{ color: #2980b9; }}
</style>
</head>
<body>
<div class="header">
<h2>📋 Agent Emploi-Public.ma</h2>
<p> Rapport du {date.today().isoformat()}</p>
<p><span class="match">{len(new_results)} nouvelle(s) annonce(s) trouvée(s)</span></p>
</div>
"""
        
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
        
        html_body += f"""
<div class="footer">
<p>Total annonces en base: <strong>{len(all_results)}</strong></p>
<p><em>Agent automatique - Emploi-Public.ma</em></p>
</div>
</body>
</html>"""
        
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        
        # Envoyer
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"Email envoyé à {EMAIL_TO} ({len(new_results)} annonces)")
        return True
    
    except Exception as e:
        logger.error(f"Erreur envoi email: {e}")
        return False


def send_email_even_si_vide(all_results):
    """Envoie un email même si aucune nouvelle annonce (pour confirmer que le bot tourne)."""
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        return False
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Emploi Public] Aucune nouvelle annonce - {date.today().isoformat()}"
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO
        
        text = f"""
Agent Emploi-Public.ma - Rapport du {date.today().isoformat()}

Aucune nouvelle annonce trouvée pour les régions cibles.

Total annonces en base: {len(all_results)}

Le bot fonctionne correctement.
"""
        
        msg.attach(MIMEText(text, "plain", "utf-8"))
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        
        logger.info(f"Email (vide) envoyé à {EMAIL_TO}")
        return True
    
    except Exception as e:
        logger.error(f"Erreur envoi email vide: {e}")
        return False


# ============================================================================
# POINT D'ENTRÉE
# ============================================================================

if __name__ == "__main__":
    new_results, all_results = run_scraper()
    
    if new_results:
        send_email_notification(new_results, all_results)
    else:
        # Optionnel: envoyer un email même si vide pour confirmer le fonctionnement
        # send_email_even_si_vide(all_results)
        logger.info("Aucune nouvelle annonce, pas d'email envoyé.")
    
    logger.info("\nScraper terminé.")
📄 2. requirements.txt
txt
requests>=2.31.0
beautifulsoup4>=4.12.0
pdfplumber>=0.10.0
lxml>=4.9.0
📄 3. .github/workflows/scraper.yml
Crée le dossier .github/workflows/ puis le fichier scraper.yml :
yaml
name: Emploi-Public Scraper

on:
  schedule:
    # Tous les 3 jours à 08:00 UTC (09:00/10:00 heure du Maroc selon l'heure d'été)
    - cron: '0 8 */3 * *'
  
  # Permet le déclenchement manuel depuis l'interface GitHub
  workflow_dispatch:

jobs:
  scraper:
    runs-on: ubuntu-latest
    
    steps:
      - name: Checkout du code
        uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Cache pip dependencies
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt') }}
          restore-keys: |
            ${{ runner.os }}-pip-
      
      - name: Installation des dépendances
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      
      - name: Exécution du scraper
        env:
          SMTP_SERVER: ${{ secrets.SMTP_SERVER }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASSWORD: ${{ secrets.SMTP_PASSWORD }}
          EMAIL_TO: ${{ secrets.EMAIL_TO }}
        run: python scraper.py
      
      - name: Sauvegarde des résultats
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: scraper-results-${{ github.run_id }}
          path: |
            data/
          retention-days: 30
      
      - name: Commit des données (optionnel - persistance entre exécutions)
        if: always()
        run: |
          git config --local user.email "actions@github.com"
          git config --local user.name "GitHub Actions"
          git add data/ 2>/dev/null || true
          git diff --cached --quiet || git commit -m "Mise à jour des données scraper - $(date -u +%Y-%m-%d)"
          git push || true
📄 4. README.md
Markdown
Copier
Code
Aperçu
# 🤖 Agent Emploi-Public.ma

Agent de scraping automatique pour le site [emploi-public.ma](https://www.emploi-public.ma/ar/) qui surveille les annonces d'emploi public au Maroc et envoie des notifications par email pour les postes situés dans les régions **Souss-Massa** ou **Guelmim-Oued Noun**.

---

## 📋 Fonctionnalités

- 🔍 Scanne les 4 catégories d'annonces :
  - مناصب المسؤولية (805+ annonces)
  - المناصب العليا (331+ annonces)
  - المباريات
  - تشغيل الخبراء (37 annonces)
- 📄 Lit les 3 premières pages de chaque catégorie
- 📅 Vérifie la date limite : **آخر أجل لإيداع الترشيحات**
- 📑 Télécharge et analyse le PDF **"قرار فتح باب الترشيح"**
- 🗺️ Détecte les postes dans les provinces cibles :
  - **Souss-Massa** : أكادير، تارودانت، تيزنيت، إنزكان، شتوكة...
  - **Guelmim-Oued Noun** : كلميم، أسا الزاك، طرفاية، طانطان، سيدي إفني...
- 📧 Envoie un email récapitulatif avec les annonces trouvées
- 🔄 Exécution automatique tous les **3 jours** via GitHub Actions

---

## 🚀 Installation

### 1. Créer un dépôt GitHub

1. Va sur [github.com/new](https://github.com/new)
2. Nomme-le `emploi-public-scraper`
3. Coche **"Add a README file"** (optionnel)
4. Clique sur **Create repository**

### 2. Uploader les fichiers

Télécharge les fichiers de ce projet et upload-les dans ton dépôt :
emploi-public-scraper/
├── .github/
│   └── workflows/
│       └── scraper.yml      ← Workflow GitHub Actions
├── data/                     ← Dossier de données (créé automatiquement)
├── scraper.py               ← Code principal
├── requirements.txt         ← Dépendances Python
└── README.md               ← Ce fichier


### 3. Configurer les secrets GitHub (pour l'email)

Va dans **Settings → Secrets and variables → Actions → New repository secret** et ajoute :

| Secret | Description | Exemple |
|--------|-------------|---------|
| `SMTP_SERVER` | Serveur SMTP | `smtp.gmail.com` |
| `SMTP_PORT` | Port SMTP | `587` |
| `SMTP_USER` | Email d'envoi | `tonemail@gmail.com` |
| `SMTP_PASSWORD` | Mot de passe d'application | `xxxx xxxx xxxx xxxx` |
| `EMAIL_TO` | Email de réception | `tonemail@gmail.com` |

#### 📧 Configuration Gmail (recommandé)

1. Active la **vérification en 2 étapes** sur ton compte Google
2. Va sur [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Crée un mot de passe d'application pour "Mail"
4. Copie ce mot de passe dans `SMTP_PASSWORD`

#### 📧 Configuration Outlook/Hotmail

- `SMTP_SERVER` : `smtp.office365.com`
- `SMTP_PORT` : `587`
- `SMTP_PASSWORD` : mot de passe normal ou mot de passe d'application

### 4. Lancer manuellement (test)

1. Va dans l'onglet **Actions** de ton dépôt
2. Clique sur **"Emploi-Public Scraper"**
3. Clique sur **"Run workflow"** → **"Run workflow"**
4. Attends 2-3 minutes et vérifie les logs

---

## ⏰ Planification

Le scraper s'exécute automatiquement **tous les 3 jours à 8h UTC** (9h/10h heure du Maroc).

Pour modifier la fréquence, édite le fichier `.github/workflows/scraper.yml` :

```yaml
# Tous les jours à 8h UTC
- cron: '0 8 * * *'

# Tous les 2 jours
- cron: '0 8 */2 * *'

# Tous les 3 jours (défaut)
- cron: '0 8 */3 * *'

# Toutes les semaines (lundi 8h)
- cron: '0 8 * * 1'
