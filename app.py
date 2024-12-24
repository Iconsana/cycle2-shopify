from flask import Flask, jsonify
from dotenv import load_dotenv
import os
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import logging
from logging.handlers import RotatingFileHandler
from difflib import SequenceMatcher
from apscheduler.schedulers.background import BackgroundScheduler
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime
from selenium.webdriver.firefox.options import Options
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

class ACDCStockScraper:
    def __init__(self):
        options = Options()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        self.driver = webdriver.Firefox(options=options)
        self.wait = WebDriverWait(self.driver, 10)

    def get_stock_levels(self, product_name):
        try:
            search_url = f"https://www.acdc.co.za/?search={quote(product_name)}"
            self.driver.get(search_url)
            time.sleep(2)
            
            products = self.wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "div.product-price-and-shipping")))
            
            best_match = self.find_best_match(products, product_name)
            if not best_match:
                logger.warning(f"No matching product found for: {product_name}")
                return None

            stock = {
                'edenvale': self._get_location_stock(best_match, 'Edenvale'),
                'germiston': self._get_location_stock(best_match, 'Germiston')
            }
            
            return stock
        except Exception as e:
            logger.error(f"Error getting stock levels for {product_name}: {str(e)}")
            return None

    def _get_location_stock(self, product_element, location):
        try:
            rows = product_element.find_elements(By.CSS_SELECTOR, "tr")
            for row in rows:
                cells = row.find_elements(By.CSS_SELECTOR, "th, td")
                for i, cell in enumerate(cells):
                    if cell.text.strip().lower() == location.lower():
                        stock_value = cells[i + 1].text.strip()
                        return int(''.join(filter(str.isdigit, stock_value)))
            return 0
        except Exception as e:
            logger.error(f"Error getting stock for {location}: {str(e)}")
            return 0

    def find_best_match(self, search_results, target_name):
        best_match = None
        highest_ratio = 0
        
        for result in search_results:
            try:
                name = result.find_element(By.CSS_SELECTOR, ".product-title").text
                ratio = SequenceMatcher(None, name.lower(), target_name.lower()).ratio()
                
                if ratio > highest_ratio and ratio > 0.8:
                    highest_ratio = ratio
                    best_match = result
            except Exception as e:
                logger.error(f"Error comparing product names: {str(e)}")
                continue
        
        return best_match

    def close(self):
        try:
            self.driver.quit()
            logger.info("Browser closed successfully")
        except Exception as e:
            logger.error(f"Error closing browser: {str(e)}")
            
   def _get_location_stock(self, product_element, location):
    try:
        # Find the row containing the location
        rows = product_element.find_elements(By.CSS_SELECTOR, "tr")
        for row in rows:
            cells = row.find_elements(By.CSS_SELECTOR, "th, td")
            for i, cell in enumerate(cells):
                if cell.text.strip().lower() == location.lower():
                    # Get the next cell which contains the stock number
                    stock_value = cells[i + 1].text.strip()
                    return int(''.join(filter(str.isdigit, stock_value)))
        return 0
    except Exception as e:
        logger.error(f"Error getting stock for {location}: {str(e)}")
        return 0

    def find_best_match(self, search_results, target_name):
        best_match = None
        highest_ratio = 0
        
        for result in search_results:
            try:
                name = result.find_element(By.CSS_SELECTOR, ".product-title").text
                ratio = SequenceMatcher(None, name.lower(), target_name.lower()).ratio()
                
                if ratio > highest_ratio and ratio > 0.8:
                    highest_ratio = ratio
                    best_match = result
            except:
                continue
        
        return best_match

    def close(self):
        try:
            self.driver.quit()
        except Exception as e:
            logger.error(f"Error closing browser: {str(e)}")

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

    def update_stock_levels(self, product_title, acdc_stock, shopify_stock):
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range='A:A'
            ).execute()
            
            rows = result.get('values', [])
            row_number = None
            
            for i, row in enumerate(rows):
                if row and row[0] == product_title:
                    row_number = i + 1
                    break
            
            if row_number is None:
                row_number = len(rows) + 1
                self.sheet.values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range='A:G',
                    valueInputOption='USER_ENTERED',
                    insertDataOption='INSERT_ROWS',
                    body={
                        'values': [[
                            product_title,
                            '',
                            '',
                            str(acdc_stock),
                            str(shopify_stock),
                            'Yes' if acdc_stock != shopify_stock else 'No',
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        ]]
                    }
                ).execute()
            else:
                self.sheet.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'D{row_number}:G{row_number}',
                    valueInputOption='USER_ENTERED',
                    body={
                        'values': [[
                            str(acdc_stock),
                            str(shopify_stock),
                            'Yes' if acdc_stock != shopify_stock else 'No',
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        ]]
                    }
                ).execute()
            
            logging.info(f"Successfully updated stock levels for {product_title}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to update stock levels: {str(e)}")
            return False

    def get_all_products(self):
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range='A2:G'
            ).execute()
            return result.get('values', [])
        except Exception as e:
            logging.error(f"Failed to get products: {str(e)}")
            return []

def sync_stock():
    logger.info("Starting stock sync")
    scraper = None
    sheets_handler = None
    
    try:
        sheets_handler = GoogleSheetsHandler(SPREADSHEET_ID)
        scraper = ACDCStockScraper()
        
        products = sheets_handler.get_all_products()
        logger.info(f"Found {len(products)} products in sheet")
        
        for product in products:
            try:
                title = product[0]
                acdc_stock = scraper.get_stock_levels(title)
                
                if acdc_stock:
                    total_acdc_stock = (int(acdc_stock.get('edenvale', 0)) + 
                                      int(acdc_stock.get('germiston', 0)))
                    
                    current_shopify_stock = int(product[4]) if len(product) > 4 else 0
                    
                    sheets_handler.update_stock_levels(
                        title,
                        total_acdc_stock,
                        current_shopify_stock
                    )
                    logger.info(f"Updated sheet for {title}")
                else:
                    logger.warning(f"No stock data found for: {title}")
            except Exception as e:
                logger.error(f"Error processing product {title}: {str(e)}")
                continue
                
    except Exception as e:
        logger.error(f"Sync failed with error: {str(e)}")
        raise
    finally:
        if scraper:
            scraper.close()

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
