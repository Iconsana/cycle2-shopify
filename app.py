```python
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
import shopify
from apscheduler.schedulers.background import BackgroundScheduler
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime

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

# Shopify configuration
shop_url = os.getenv('SHOPIFY_SHOP_URL', '').strip()
api_key = os.getenv('SHOPIFY_API_KEY')
api_secret = os.getenv('SHOPIFY_API_SECRET')
access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')

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
        """Update stock levels for a product"""
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
        """Get all products and their stock levels"""
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range='A2:G'
            ).execute()
            return result.get('values', [])
        except Exception as e:
            logging.error(f"Failed to get products: {str(e)}")
            return []

[Rest of your existing ACDCStockScraper class and other functions remain the same]

def sync_stock():
    """Main function to sync stock levels"""
    logger.info("Starting stock sync")
    scraper = None
    sheets_handler = None
    
    try:
        # Initialize Google Sheets handler with credentials from environment
        credentials_path = get_credentials()
        sheets_handler = GoogleSheetsHandler(credentials_path, SPREADSHEET_ID)
        
        # Initialize scraper
        logger.info("Initializing web scraper...")
        scraper = ACDCStockScraper()
        
        # Get existing products from sheet
        products = sheets_handler.get_all_products()
        logger.info(f"Found {len(products)} products in sheet")
        
        for product in products:
            try:
                title = product[0]  # Title is in first column
                
                # Get ACDC stock levels
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
        # Clean up temporary credentials file
        if os.path.exists('/tmp/google_credentials.json'):
            os.remove('/tmp/google_credentials.json')

# Initialize scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(sync_stock, 'cron', hour=0)  # Run at midnight
scheduler.start()

# API Routes
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
    """Health check endpoint"""
    return jsonify({"status": "healthy"})
    
@app.route('/trigger-sync')
def trigger_sync():
    """Endpoint to manually trigger stock sync"""
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
    """Test endpoint to verify configuration"""
    try:
        # Test Google Sheets connection
        credentials_path = get_credentials()
        sheets_handler = GoogleSheetsHandler(credentials_path, SPREADSHEET_ID)
        sheets_test = sheets_handler.get_all_products() is not None
        
        # Clean up
        if os.path.exists('/tmp/google_credentials.json'):
            os.remove('/tmp/google_credentials.json')
            
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
```
