#!/usr/bin/env python3
from scraper_cg import get_conseil_detail

# Tester directement avec l'URL du conseil du 22/07/2026
url = "https://www.cg.gov.ma/ar/node/13007"
details = get_conseil_detail(url)

print("TITRE:", details.get("titre"))
print("DATE:", details.get("date_text"))
print("LOIS:", details.get("lois"))
print("ACCORDS:", details.get("accords"))
print("NOMINATIONS:", details.get("nominations"))
print("PDF:", details.get("pdf_url"))
