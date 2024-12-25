from flask import Flask, jsonify
from dotenv import load_dotenv
import os
import json
import requests
from bs4 import BeautifulSoup
import time
import logging
from logging.handlers import RotatingFileHandler
from difflib import SequenceMatcher
from apscheduler.schedulers.background import BackgroundScheduler
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime
from urllib.parse import quote

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
handler = RotatingFileHandler('app.log', maxBytes=10000000, backupCount=5)
handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))
logger.addHandler(handler)

def get_google_credentials():
    creds_json = os.getenv('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS not set")
    
    return service_account.Credentials.from_service_account_info(
        json.loads(creds_json),
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )

# Initialize Flask app
app = Flask(__name__)

# Google Sheets configuration
SPREADSHEET_ID = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')

class GoogleSheetsHandler:
    def __init__(self, spreadsheet_id):
        self.spreadsheet_id = spreadsheet_id
        try:
            credentials = get_google_credentials()
            self.service = build('sheets', 'v4', credentials=credentials)
            self.sheet = self.service.spreadsheets()
        except Exception as e:
            logger.error(f"Sheets init failed: {str(e)}")
            raise

    def get_all_products(self):
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range='Sheet1!I2:I'
            ).execute()
            return result.get('values', [])
        except Exception as e:
            logging.error(f"Failed to get products: {str(e)}")
            return []

    def update_stock_levels(self, sku, acdc_stock):
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range='Sheet1!I:I'
            ).execute()
            
            rows = result.get('values', [])
            row_number = None
            
            for i, row in enumerate(rows):
                if row and row[0] == sku:
                    row_number = i + 1
                    break
                
            if row_number:
                self.sheet.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'Sheet1!P{row_number}:Q{row_number}',
                    valueInputOption='USER_ENTERED',
                    body={
                        'values': [[
                            str(acdc_stock),
                            str(acdc_stock)
                        ]]
                    }
                ).execute()
                
                logging.info(f"Successfully updated stock levels for {sku}")
                return True
                
            return False
                
        except Exception as e:
            logging.error(f"Failed to update stock levels: {str(e)}")
            return False

def get_stock_levels(sku):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        search_url = f"https://www.acdc.co.za/?search={quote(sku)}"
        response = requests.get(search_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        products = soup.select(".product-list-item")
        
        for product in products:
            # Look for exact SKU match
            product_sku = product.select_one(".sku")
            if product_sku and product_sku.text.strip().lower() == sku.lower():
                stock = {
                    'edenvale': 0,
                    'germiston': 0
                }
                
                # Find stock levels
                for row in product.select("tr"):
                    location = row.select_one("th")
                    if location:
                        loc_text = location.text.strip().lower()
                        if loc_text in ['edenvale', 'germiston']:
                            try:
                                stock_cell = row.select_one("td")
                                if stock_cell:
                                    stock_text = stock_cell.text.strip()
                                    stock[loc_text] = int(''.join(filter(str.isdigit, stock_text)))
                            except ValueError:
                                continue
                
                return stock
        
        return None
    except Exception as e:
        logger.error(f"Error getting stock levels for {sku}: {str(e)}")
        return None

def sync_stock():
    logger.info("Starting stock sync")
    sheets_handler = None
    
    try:
        sheets_handler = GoogleSheetsHandler(SPREADSHEET_ID)
        products = sheets_handler.get_all_products()
        logger.info(f"Found {len(products)} products in sheet")
        
        for product in products:
            try:
                sku = product[0]
                acdc_stock = get_stock_levels(sku)
                
                if acdc_stock:
                    total_acdc_stock = (int(acdc_stock.get('edenvale', 0)) + 
                                      int(acdc_stock.get('germiston', 0)))
                    
                    sheets_handler.update_stock_levels(
                        sku,
                        total_acdc_stock
                    )
                    logger.info(f"Updated sheet for {sku}")
                else:
                    logger.warning(f"No stock data found for: {sku}")
            except Exception as e:
                logger.error(f"Error processing product {sku}: {str(e)}")
                continue
                
    except Exception as e:
        logger.error(f"Sync failed with error: {str(e)}")
        raise

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(sync_stock, 'cron', hour=0)
scheduler.start()

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "endpoints": {
            "/health": "Check system health",
            "/trigger-sync": "Manually trigger stock sync",
            "/test-config": "Test configuration"
        }
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})
    
@app.route('/trigger-sync')
def trigger_sync():
    try:
        sync_stock()
        return jsonify({"status": "sync completed"})
    except Exception as e:
        return jsonify({
            "error": "Sync failed",
            "details": str(e)
        }), 500

@app.route('/test-config')
def test_config():
    try:
        sheets_handler = GoogleSheetsHandler(SPREADSHEET_ID)
        sheets_test = sheets_handler.get_all_products() is not None
            
        return jsonify({
            "google_sheets_working": sheets_test
        })
    except Exception as e:
        return jsonify({
            "error": "Configuration test failed",
            "details": str(e)
        }), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
