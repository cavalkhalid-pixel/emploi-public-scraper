#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent de scraping pour emploi-public.ma - VERSION FRANÇAISE
Scanne les 4 catégories en français et recherche les annonces
dans les régions Souss-Massa et Guelmim-Oued Noun.
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
    "Accept-Language": "fr,en;q=0.9",
}

# Catégories en français
CATEGORIES = [
    {"name": "Concours de recrutement", "slug": "concours-liste", "detail_prefix": "concours"},
    {"name": "Emplois supérieurs", "slug": "emploi-sup-liste", "detail_prefix": "emploi-sup"},
    {"name": "Postes de responsabilités", "slug": "postes-respo-liste", "detail_prefix": "postes-respo"},
    {"name": "Recrutement des experts", "slug": "experts-liste", "detail_prefix": "experts"}
]

MAX_PAGES = 3

# Provinces cibles (en français et en arabe pour la recherche dans les PDF)
PROVINCES_SOUSS_MASSA = [
    "Agadir", "I dawtannane", "Inezgane", "Ait Melloul", "Taroudant",
    "Tiznit", "Chtouka", "Ait Baha", "Souss", "Souss-Massa",
    "أكادير", "إداوتنان", "إنزكان", "آيت ملول", "تارودانت",
    "تيزنيت", "شتوكة", "آيت باها", "سوس", "سوس ماسة"
]

PROVINCES_GUELMIM_OUED_NOUN = [
    "Guelmim", "Assa Zag", "Tarfaya", "Tan Tan", "Sidi Ifni",
    "كلميم", "أسا الزاك", "طرفاية", "طانطان", "سيدي إفني"
]

REGIONS_CIBLES = PROVINCES_SOUSS_MASSA + PROVINCES_GUELMIM_OUED_NOUN

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
SEEN_FILE = DATA_DIR / "annonces_vues_fr.json"
RESULTS_FILE = DATA_DIR / "resultats_fr.json"
LOG_FILE = DATA_DIR / "scraper_fr.log"

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

def parse_french_date(date_str):
    """
    Parse une date en français.
    Exemples: "5 Août 2026", "20 Juillet 2026"
    """
    mois_fr = {
        "janvier": 1, "février": 2, "mars": 3, "avril": 4,
        "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
        "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12
    }
    date_str = date_str.strip()
    # Pattern: jour mois année
    pattern = r"(\d{1,2})\s+([a-zA-ZàâäéèêëïîôöùûüÿçÀÂÄÉÈÊËÏÎÔÖÙÛÜŸÇ]+)\s+(\d{4})"
    match = re.search(pattern, date_str)
    if match:
        jour = int(match.group(1))
        mois_nom = match.group(2).lower()
        annee = int(match.group(3))
        mois = mois_fr.get(mois_nom)
        if mois:
            try:
                return date(annee, mois, jour)
            except ValueError:
                logger.warning(f"Date invalide: {date_str}")
                return None
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

def check_region_in_text(text):
    """Recherche les noms de provinces dans un texte (HTML ou PDF)"""
    if not text:
        return None
    text_lower = text.lower()
    for province in REGIONS_CIBLES:
        if province.lower() in text_lower:
            return province
    # Détection large
    if "souss" in text_lower or "ماسة" in text_lower:
        return "Région Souss-Massa (détectée)"
    if "guelmim" in text_lower or "oued noun" in text_lower or "كلميم" in text_lower:
        return "Région Guelmim-Oued Noun (détectée)"
    return None

# ============================================================================
# SCRAPING - VERSION FRANÇAISE
# ============================================================================

def get_liste_annonces(category_slug, page=0):
    """
    Récupère la liste des annonces d'une page de catégorie (version FR).
    """
    url = f"{BASE_URL}/fr/{category_slug}"
    if page > 0:
        url += f"?page={page}"
    
    logger.info(f"Scraping page: {url}")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        annonces = []
        
        # Rechercher les éléments .s-item qui contiennent les annonces
        items = soup.find_all("div", class_="s-item")
        
        for item in items:
            # Chercher le lien vers la page de détail
            link = item.find("a", href=True)
            if not link:
                continue
            
            href = link.get("href", "")
            uuid_match = re.search(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", href)
            if not uuid_match:
                continue
            
            uuid = uuid_match.group(0)
            
            # Construire l'URL complète
            if href.startswith("/"):
                detail_url = f"{BASE_URL}{href}"
            elif href.startswith("http"):
                detail_url = href
            else:
                detail_url = f"{BASE_URL}/fr/{href}"
            
            # Titre
            titre_elem = item.find("h2", class_="card-title")
            titre = titre_elem.get_text(strip=True) if titre_elem else ""
            
            # Administration
            admin_elem = item.find("div", class_="card-text")
            administration = admin_elem.get_text(strip=True) if admin_elem else ""
            if administration.startswith("Ministère") or administration.startswith("Province"):
                pass
            
            # Date limite
            date_text = ""
            footer = item.find("div", class_="card-footer")
            if footer:
                for div in footer.find_all("div"):
                    text = div.get_text(strip=True)
                    if "Limite de dépôt" in text or "Délai de dépôt" in text:
                        # Extraire la date
                        date_match = re.search(r"(\d{1,2}\s+[a-zA-Zàâäéèêëïîôöùûüÿç]+\s+\d{4})", text)
                        if date_match:
                            date_text = date_match.group(1)
                        break
            
            # Nombre de postes
            nb_postes = ""
            if footer:
                for div in footer.find_all("div"):
                    text = div.get_text(strip=True)
                    if "poste" in text.lower():
                        nb_match = re.search(r"(\d+)\s+poste", text)
                        if nb_match:
                            nb_postes = nb_match.group(1)
                        break
            
            annonces.append({
                "uuid": uuid,
                "titre": titre,
                "administration": administration,
                "date_limite_text": date_text,
                "detail_url": detail_url,
                "categorie": category_slug,
                "nb_postes": nb_postes
            })
        
        logger.info(f"  → {len(annonces)} annonces trouvées sur cette page")
        return annonces
    
    except Exception as e:
        logger.error(f"Erreur scraping page {url}: {e}")
        return []

def get_annonce_detail(detail_url):
    """
    Récupère les détails d'une annonce (version FR).
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
            "description": "",
            "page_text": ""
        }
        
        page_text = soup.get_text(separator=" ", strip=True)
        result["page_text"] = page_text
        
        # === DATE LIMITE ===
        # Chercher dans les éléments de la sidebar
        sidebar = soup.find("div", class_="s-content-box")
        if sidebar:
            for h3 in sidebar.find_all("h3", class_="h4"):
                span = h3.find("span")
                if span and ("Délai de dépôt" in span.get_text() or "Limite de dépôt" in span.get_text()):
                    date_text = h3.get_text(strip=True).replace(span.get_text(strip=True), "").strip()
                    if date_text:
                        result["date_limite_text"] = date_text
                        result["date_limite"] = parse_french_date(date_text)
                    break
        
        # Si non trouvé, chercher dans tout le texte
        if not result["date_limite_text"]:
            date_patterns = [
                r"Délai de dépôt des candidatures\s*[:]?\s*(\d{1,2}\s+[a-zA-Zàâäéèêëïîôöùûüÿç]+\s+\d{4})",
                r"Limite de dépôt\s*[:]?\s*(\d{1,2}\s+[a-zA-Zàâäéèêëïîôöùûüÿç]+\s+\d{4})",
            ]
            for pattern in date_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    result["date_limite_text"] = match.group(1).strip()
                    result["date_limite"] = parse_french_date(result["date_limite_text"])
                    break
        
        # === PDF (Arrêté d'ouverture) ===
        # Chercher dans la section "Téléchargement"
        pdf_links = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            link_text = link.get_text(strip=True)
            
            # Détecter l'arrêté
            if "arrete" in href.lower() or "Arrêté" in link_text or "arrêté" in link_text.lower():
                if href.startswith("/"):
                    full_url = f"{BASE_URL}{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    full_url = f"{BASE_URL}/fr/{href}"
                pdf_links.append({
                    "url": full_url,
                    "text": link_text,
                    "score": 10
                })
            
            # Détecter les PDF en général
            elif href.endswith(".pdf") or ".pdf" in href:
                if href.startswith("/"):
                    full_url = f"{BASE_URL}{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    full_url = f"{BASE_URL}/fr/{href}"
                pdf_links.append({
                    "url": full_url,
                    "text": link_text,
                    "score": 5
                })
        
        # Prendre le meilleur PDF
        if pdf_links:
            pdf_links.sort(key=lambda x: x["score"], reverse=True)
            best = pdf_links[0]
            result["pdf_url"] = best["url"]
            result["pdf_nom"] = best["text"]
            logger.info(f"    PDF trouvé: {best['text']}")
        else:
            logger.info("    Aucun PDF trouvé")
        
        # === ADMINISTRATION ===
        if sidebar:
            for h3 in sidebar.find_all("h3", class_="h4"):
                span = h3.find("span")
                if span and "Administration qui recrute" in span.get_text():
                    admin_text = h3.get_text(strip=True).replace(span.get_text(strip=True), "").strip()
                    if admin_text:
                        result["administration"] = admin_text
                    break
        
        if not result["administration"]:
            admin_match = re.search(r"Administration qui recrute\s*[:]?\s*(.+?)(?:\n|$)", page_text)
            if admin_match:
                result["administration"] = admin_match.group(1).strip()
        
        return result
    
    except Exception as e:
        logger.error(f"Erreur détails annonce {detail_url}: {e}")
        return {"date_limite": None, "date_limite_text": "", "pdf_url": None, "pdf_nom": "", "administration": "", "description": "", "page_text": ""}

# ============================================================================
# EXÉCUTION PRINCIPALE
# ============================================================================

def run_scraper():
    logger.info("=" * 60)
    logger.info("DÉMARRAGE DU SCRAPER emploi-public.ma (FR)")
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
                
                # Utiliser la date de la liste si non trouvée dans les détails
                if not details["date_limite_text"] and annonce["date_limite_text"]:
                    details["date_limite_text"] = annonce["date_limite_text"]
                    details["date_limite"] = parse_french_date(annonce["date_limite_text"])

                if not details["date_limite"]:
                    logger.info(f"  [PAS DE DATE] {annonce['titre'][:60]}...")
                    continue

                if not is_date_en_cours(details["date_limite"]):
                    total_expirees += 1
                    logger.info(f"  [EXPIRÉE] {annonce['titre'][:60]}... → {details['date_limite']}")
                    continue

                total_en_cours += 1
                logger.info(f"  [EN COURS] {annonce['titre'][:60]}... → {details['date_limite']}")

                # --- Détection de région ---
                region_trouvee = None
                pdf_text = ""

                if details["pdf_url"]:
                    total_pdf_lus += 1
                    logger.info(f"    → PDF: {details['pdf_url']}")
                    pdf_text = extract_text_from_pdf(details["pdf_url"])
                    if pdf_text:
                        region_trouvee = check_region_in_text(pdf_text)

                # Fallback : chercher dans le texte de la page HTML
                if not region_trouvee and details.get("page_text"):
                    region_trouvee = check_region_in_text(details["page_text"])
                    if region_trouvee:
                        logger.info(f"    → Région trouvée dans le texte de la page: {region_trouvee}")

                if region_trouvee:
                    total_match_region += 1
                    logger.info(f"    ✓✓✓ MATCH RÉGION: {region_trouvee}")
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
# ENVOI D'EMAIL
# ============================================================================

def send_email_report(new_results, all_results, total_traitees, total_en_cours, total_pdf_lus, total_match_region):
    if not SMTP_USER or not SMTP_PASSWORD or not EMAIL_TO:
        logger.warning("Configuration email incomplète, pas d'envoi.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        if new_results:
            msg["Subject"] = f"[Emploi Public FR] {len(new_results)} nouvelle(s) annonce(s) - {date.today().isoformat()}"
        else:
            msg["Subject"] = f"[Emploi Public FR] Rapport - {date.today().isoformat()}"
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO

        text_body = f"""
Agent Emploi-Public.ma (FR) - Rapport du {date.today().isoformat()}
{'=' * 60}

STATISTIQUES:
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
Date limite: {r['date_limite_text']}
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
- Souss-Massa: Agadir, Taroudant, Tiznit, Inezgane, Chtouka...
- Guelmim-Oued Noun: Guelmim, Assa Zag, Tarfaya, Tan Tan, Sidi Ifni...

Prochaine exécution: dans 3 jours
"""

        # Version HTML simplifiée
        html_body = f"""<!DOCTYPE html>
<html dir="ltr" lang="fr">
<head>
<meta charset="UTF-8">
<style>
body {{ font-family: Arial, sans-serif; }}
.header {{ background: #1a5276; color: white; padding: 20px; border-radius: 8px; }}
.stats {{ background: #f0f0f0; padding: 15px; border-radius: 8px; margin: 15px 0; }}
.stat-item {{ display: inline-block; margin: 5px 15px; }}
.stat-value {{ font-size: 24px; font-weight: bold; color: #1a5276; }}
.stat-label {{ font-size: 12px; color: #666; }}
.annonce {{ border: 1px solid #ddd; margin: 15px 0; padding: 15px; border-radius: 8px; background: #f9f9f9; }}
.titre {{ color: #1a5276; font-size: 18px; font-weight: bold; }}
.match {{ color: #27ae60; font-weight: bold; }}
.no-result {{ background: #fff3cd; padding: 20px; border-radius: 8px; text-align: center; }}
.footer {{ margin-top: 30px; padding: 15px; background: #eee; border-radius: 8px; text-align: center; }}
</style>
</head>
<body>
<div class="header"><h2>📋 Agent Emploi-Public.ma (FR)</h2><p>Rapport du {date.today().isoformat()}</p></div>
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
<div class="info"><strong>Administration:</strong> {r['administration']}</div>
<div class="info"><strong>Catégorie:</strong> {r['categorie']}</div>
<div class="info"><strong>Date limite:</strong> {r['date_limite_text']}</div>
<div class="info match">📍 {r['region_detectee']}</div>
<div class="info"><a href="{r['detail_url']}">🔗 Voir l'annonce</a> | <a href="{r['pdf_url']}">📄 Télécharger le PDF</a></div>
</div>
"""
        else:
            html_body += f"""
<div class="no-result">
<h3>📭 Aucune nouvelle annonce trouvée</h3>
<p>Le bot a analysé <strong>{total_traitees}</strong> annonces mais aucune ne correspond aux régions cibles.</p>
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
    new_results, all_results, total_traitees, total_en_cours, total_pdf_lus, total_match_region = run_scraper()
    send_email_report(new_results, all_results, total_traitees, total_en_cours, total_pdf_lus, total_match_region)
    logger.info("\nScraper terminé.")
