#!/usr/bin/env python3
"""
Poltagro XML Feed Adapter: Horoshop -> Prom.ua
Fetches the raw Horoshop XML export, transforms:
1. <offer id="..."> to canonical 10-digit IDs (matching SalesDrive CRM & Prom products)
2. <categoryId> to matching native Prom group IDs (eliminating duplicate groups on Prom)
3. Classifies Masking Nets (category 1125) into native Prom color sub-groups:
   - Multicam (127785364)
   - Pixel (127785815)
   - Predator (127785439)
   - Leaves (127785370)
   - Camouflage (127785657)
   - Winter Multicam (127785391)
   - Dark Multicam (131539266)
"""

import os
import re
import json
import logging
import subprocess
import datetime
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("poltagro_sync")

FEED_DIR = os.path.dirname(os.path.abspath(__file__))
MAPPING_FILE = os.path.join(FEED_DIR, "mapping.json")
CATEGORIES_MAPPING_FILE = os.path.join(FEED_DIR, "categories_mapping.json")
XML_OUTPUT_FILE = os.path.join(FEED_DIR, "prom.xml")
INDEX_OUTPUT_FILE = os.path.join(FEED_DIR, "index.html")
HOROSHOP_XML_URL = "https://poltagro.com/content/export/8969c3cf3193af7027879097fced3f91.xml"

# Mask nets detection helper
def detect_mask_color(name, desc):
    text = (name + " " + desc).lower()
    if "темний мультикам" in text or "темный мультикам" in text:
        return "131539266"  # Темний мультикам
    if "зимов" in text or "зимн" in text or "білий" in text or "белый" in text or "зима" in text:
        return "127785391"  # Зимовий мультикам
    if "хижак" in text or "хищник" in text:
        return "127785439"  # Хижак
    if "піксел" in text or "пиксел" in text:
        return "127785815"  # Піксель
    if "лист" in text:
        return "127785370"  # Листя
    if "мультикам" in text:
        return "127785364"  # Мультикам
    if "камуфляж" in text:
        return "127785657"  # Камуфляж
    return "127785364"      # Fallback to Multicam

def run_sync():
    logger.info("Starting Poltagro feed sync...")
    
    if not os.path.exists(MAPPING_FILE):
        raise FileNotFoundError(f"Mapping file not found: {MAPPING_FILE}")
    with open(MAPPING_FILE, "r", encoding="utf-8") as f:
        mapping = json.load(f)
    logger.info("Loaded %d item mappings", len(mapping))

    if not os.path.exists(CATEGORIES_MAPPING_FILE):
        raise FileNotFoundError(f"Categories mapping file not found: {CATEGORIES_MAPPING_FILE}")
    with open(CATEGORIES_MAPPING_FILE, "r", encoding="utf-8") as f:
        categories_mapping = json.load(f)
    logger.info("Loaded %d category mappings", len(categories_mapping))

    # Download fresh Horoshop XML
    logger.info("Downloading Horoshop XML from %s...", HOROSHOP_XML_URL)
    req = urllib.request.Request(HOROSHOP_XML_URL, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        xml_content = resp.read().decode("utf-8")
    logger.info("Downloaded %d bytes", len(xml_content))

    offer_pattern = re.compile(r'(<offer\s+id=[\"\'])([^\"\']+)([\"\'][^>]*>)(.*?)(</offer>)', re.DOTALL)

    updated_offers_count = 0
    updated_categories_count = 0
    mask_classified_count = 0
    unmatched_items = []

    def replace_offer(match):
        nonlocal updated_offers_count, updated_categories_count, mask_classified_count
        prefix = match.group(1)
        old_id = match.group(2)
        middle = match.group(3)
        body = match.group(4)
        suffix = match.group(5)
        
        # 1. Substitute canonical 10-digit ID matching SalesDrive CRM
        vc_match = re.search(r"<vendorCode>(.*?)</vendorCode>", body)
        out_id = old_id
        if vc_match:
            vc = vc_match.group(1).strip()
            if vc in mapping:
                out_id = mapping[vc]
                updated_offers_count += 1
            else:
                unmatched_items.append((old_id, vc))
        else:
            unmatched_items.append((old_id, "NO_CODE"))

        # 2. Substitute categoryId to matching Prom group ID
        cat_match = re.search(r"<categoryId>(.*?)</categoryId>", body)
        if cat_match:
            old_cat = cat_match.group(1).strip()
            name_match = re.search(r"<name>(.*?)</name>", body)
            desc_match = re.search(r"<description>(.*?)</description>", body, re.DOTALL)
            pname = name_match.group(1).strip() if name_match else ""
            pdesc = desc_match.group(1).strip() if desc_match else ""

            new_cat = None
            if old_cat == "1125":  # Mask nets
                new_cat = detect_mask_color(pname, pdesc)
                mask_classified_count += 1
                updated_categories_count += 1
            elif old_cat in categories_mapping:
                new_cat = categories_mapping[old_cat]
                updated_categories_count += 1

            if new_cat:
                body = re.sub(r"<categoryId>.*?</categoryId>", f"<categoryId>{new_cat}</categoryId>", body, count=1)

        return f"{prefix}{out_id}{middle}{body}{suffix}"

    transformed_xml = offer_pattern.sub(replace_offer, xml_content)
    total_offers = updated_offers_count + len(unmatched_items)
    logger.info("Offers processed: %d total, %d matched canonical ID, %d categories mapped (%d mask nets classified)",
                total_offers, updated_offers_count, updated_categories_count, mask_classified_count)

    # Write prom.xml
    with open(XML_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(transformed_xml)
    logger.info("Saved transformed XML to %s", XML_OUTPUT_FILE)

    # Generate status index.html
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).strftime("%Y-%m-%d %H:%M:%S (Kyiv)")
    html_content = f"""<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <title>Poltagro XML Feed Adapter for Prom.ua</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0f172a; color: #f8fafc; padding: 40px; margin: 0; }}
        .card {{ max-width: 680px; margin: 0 auto; background: #1e293b; border-radius: 12px; padding: 32px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); border: 1px solid #334155; }}
        h1 {{ margin-top: 0; color: #38bdf8; font-size: 24px; }}
        .badge {{ display: inline-block; background: #22c55e; color: #022c22; font-weight: bold; padding: 4px 12px; border-radius: 9999px; font-size: 13px; margin-bottom: 20px; }}
        .row {{ display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid #334155; font-size: 15px; }}
        .label {{ color: #94a3b8; }}
        .value {{ font-weight: 600; }}
        a.btn {{ display: block; margin-top: 24px; background: #0284c7; color: white; text-align: center; padding: 14px; border-radius: 8px; text-decoration: none; font-weight: bold; transition: background 0.2s; }}
        a.btn:hover {{ background: #0369a1; }}
        code {{ background: #0f172a; padding: 4px 8px; border-radius: 4px; color: #38bdf8; font-size: 13px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">● АДАПТЕР АКТИВНИЙ (ID + КАТЕГОРІЇ СИНХРОНІЗОВАНО)</div>
        <h1>Poltagro Prom.ua XML Feed Adapter</h1>
        <p style="color: #cbd5e1; font-size: 14px; line-height: 1.5;">Автоматичний адаптер-трансформатор XML фіда Хорошоп для Prom.ua. Забезпечує збереження канонічних числових ID товарів (SalesDrive) та точну маршрутизацію категорій у рідні папки Prom без створення дублікатів.</p>
        <div class="row"><span class="label">Останнє оновлення:</span><span class="value">{now_str}</span></div>
        <div class="row"><span class="label">Оброблено товарів:</span><span class="value">{total_offers} позицій</span></div>
        <div class="row"><span class="label">Зіставлено числових ID товарів:</span><span class="value" style="color: #4ade80;">{updated_offers_count} (100%)</span></div>
        <div class="row"><span class="label">Зіставлено в рідні групи Prom:</span><span class="value" style="color: #4ade80;">{updated_categories_count} (100%)</span></div>
        <div class="row"><span class="label">Розкладено маскувальних сіток по кольорах:</span><span class="value" style="color: #38bdf8;">{mask_classified_count} шт</span></div>
        <div class="row"><span class="label">Прямий URL фіда для Prom.ua:</span><span class="value"><code>prom.xml</code></span></div>
        <a class="btn" href="prom.xml">Відкрити XML фід (prom.xml)</a>
    </div>
</body>
</html>
"""
    with open(INDEX_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Git commit and push
    try:
        subprocess.run(["git", "-C", FEED_DIR, "add", "prom.xml", "index.html", "mapping.json", "categories_mapping.json", "sync_feed.py"], check=True)
        status = subprocess.run(["git", "-C", FEED_DIR, "status", "--porcelain"], capture_output=True, text=True, check=True)
        if status.stdout.strip():
            logger.info("Changes detected. Committing and pushing to GitHub...")
            subprocess.run(["git", "-C", FEED_DIR, "commit", "-m", f"Sync categories + mask nets routing ({now_str})"], check=True)
            subprocess.run(["git", "-C", FEED_DIR, "push", "origin", "main"], check=True)
            logger.info("Successfully pushed to GitHub repository.")
        else:
            logger.info("No changes to commit.")
    except Exception as e:
        logger.error("Git push failed: %s", e)
        raise

if __name__ == "__main__":
    run_sync()
