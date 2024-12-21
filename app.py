from flask import Flask, jsonify
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import logging
from difflib import SequenceMatcher
from apscheduler.schedulers.background import BackgroundScheduler
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Google Sheets configuration
SPREADSHEET_ID = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
CREDENTIALS_FILE = os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE')

class GoogleSheetsHandler:
    def __init__(self, credentials_file, spreadsheet_id):
        """Initialize the Google Sheets handler with credentials"""
        self.spreadsheet_id = spreadsheet_id
        self.SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        
        try:
            credentials = service_account.Credentials.from_service_account_file(
                credentials_file, scopes=self.SCOPES)
            self.service = build('sheets', 'v4', credentials=credentials)
            self.sheet = self.service.spreadsheets()
            logging.info("Successfully initialized Google Sheets handler")
        except Exception as e:
            logging.error(f"Failed to initialize Google Sheets handler: {str(e)}")
            raise

    def update_stock_levels(self, product_title, acdc_stock, current_shopify_stock=0):
        """Update stock levels for a product"""
        try:
            # First, find the row with the matching product title
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range='A:A'  # Search in first column
            ).execute()
            
            rows = result.get('values', [])
            row_number = None
            
            for i, row in enumerate(rows):
                if row and row[0] == product_title:
                    row_number = i + 1  # Add 1 because sheets are 1-indexed
                    break
            
            if row_number is None:
                # Product not found, append new row
                row_number = len(rows) + 1
                self.sheet.values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range='A:F',
                    valueInputOption='USER_ENTERED',
                    insertDataOption='INSERT_ROWS',
                    body={
                        'values': [[
                            product_title,
                            '',  # ACDC Price
                            '',  # Our Price
                            acdc_stock,
                            current_shopify_stock,
                            'Check Stock Discrepancy' if str(acdc_stock) != str(current_shopify_stock) else ''
                        ]]
                    }
                ).execute()
            else:
                # Update existing row
                self.sheet.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'D{row_number}:F{row_number}',
                    valueInputOption='USER_ENTERED',
                    body={
                        'values': [[
                            acdc_stock,
                            current_shopify_stock,
                            'Check Stock Discrepancy' if str(acdc_stock) != str(current_shopify_stock) else ''
                        ]]
                    }
                ).execute()
            
            # Update last updated timestamp
            self.mark_last_updated(row_number)
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
                range='A2:E'  # Exclude header row
            ).execute()
            return result.get('values', [])
        except Exception as e:
            logging.error(f"Failed to get products: {str(e)}")
            return []

    def mark_last_updated(self, row_number):
        """Update the last updated timestamp for a row"""
        try:
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            self.sheet.values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f'G{row_number}',
                valueInputOption='USER_ENTERED',
                body={
                    'values': [[timestamp]]
                }
            ).execute()
            return True
        except Exception as e:
            logging.error(f"Failed to update timestamp: {str(e)}")
            return False

class ACDCStockScraper:
    def __init__(self):
        self.base_url = "https://acdc.co.za"
        self.setup_driver()
        
    def setup_driver(self):
        """Setup webdriver with Railway-compatible options"""
        options = webdriver.FirefoxOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        self.driver = webdriver.Firefox(options=options)
        logger.info("Webdriver initialized successfully")

    def similarity_ratio(self, a, b):
        """Calculate similarity between two strings"""
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def find_matching_product(self, search_title, threshold=0.8):
        """Search for a product by title and return the most similar match"""
        search_url = f"{self.base_url}/search?q={search_title}"
        self.driver.get(search_url)
        
        # Wait for products to load
        time.sleep(2)
        
        products = self.driver.find_elements(By.CLASS_NAME, "product-title")
        
        best_match = None
        best_ratio = 0
        
        for product in products:
            ratio = self.similarity_ratio(search_title, product.text)
            if ratio > best_ratio and ratio >= threshold:
                best_match = product
                best_ratio = ratio
        
        return best_match

    def get_stock_levels(self, product_title):
        """Get stock levels for a specific product"""
        matching_product = self.find_matching_product(product_title)
        
        if not matching_product:
            logger.warning(f"No matching product found for: {product_title}")
            return None

        product_url = matching_product.find_element(By.XPATH, "..").get_attribute("href")
        self.driver.get(product_url)
        
        try:
            stock_table = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "stock-table"))
            )
            
            stock_data = {
                'edenvale': None,
                'germiston': None,
                'status': 'unknown'
            }
            
            in_stock_element = self.driver.find_element(By.CLASS_NAME, "stock-status")
            stock_data['status'] = in_stock_element.text
            
            rows = stock_table.find_elements(By.TAG_NAME, "tr")
            for row in rows:
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) >= 2:
                    branch = cells[0].text.lower()
                    quantity = cells[1].text
                    if branch == 'edenvale':
                        stock_data['edenvale'] = quantity
                    elif branch == 'germiston':
                        stock_data['germiston'] = quantity
            
            return stock_data
            
        except Exception as e:
            logger.error(f"Error getting stock levels: {str(e)}")
            return None

    def close(self):
        """Clean up resources"""
        self.driver.quit()

def sync_stock():
    """Main function to sync stock levels"""
    logger.info("Starting stock sync")
    scraper = None
    sheets_handler = None
    
    try:
        # Initialize Google Sheets handler
        sheets_handler = GoogleSheetsHandler(CREDENTIALS_FILE, SPREADSHEET_ID)
        
        # Initialize scraper
        logger.info("Initializing web scraper...")
        scraper = ACDCStockScraper()
        
        # Get all products from Google Sheet
        products = sheets_handler.get_all_products()
        logger.info(f"Found {len(products)} products in Google Sheet")
        
        for product in products:
            try:
                title = product[0]  # First column is title
                logger.info(f"Processing product: {title}")
                
                # Get ACDC stock levels
                acdc_stock = scraper.get_stock_levels(title)
                if acdc_stock:
                    total_acdc_stock = (int(acdc_stock.get('edenvale', 0)) + 
                                      int(acdc_stock.get('germiston', 0)))
                    
                    # Keep existing Shopify stock if available
                    current_shopify_stock = product[4] if len(product) > 4 else 0
                    
                    # Update Google Sheet
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
scheduler.add_job(sync_stock, 'cron', hour=0)  # Run at midnight
scheduler.start()

# API Routes
@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "endpoints": {
            "/health": "Check system health",
            "/trigger-sync": "Manually trigger ACDC stock sync",
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
        sheets_handler = GoogleSheetsHandler(CREDENTIALS_FILE, SPREADSHEET_ID)
        sheets_test = sheets_handler.get_all_products() is not None
    except Exception as e:
        sheets_test = False
        
    return jsonify({
        "google_sheets_working": sheets_test,
        "scraper_configured": True
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
