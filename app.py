from flask import Flask, jsonify
from dotenv import load_dotenv
import os
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

# Initialize Flask app
app = Flask(__name__)

# Shopify configuration
shop_url = os.getenv('SHOPIFY_SHOP_URL', '').strip()
api_key = os.getenv('SHOPIFY_API_KEY')
api_secret = os.getenv('SHOPIFY_API_SECRET')
access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')

# Google Sheets configuration
SPREADSHEET_ID = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
CREDENTIALS_FILE = os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE')

class GoogleSheetsHandler:
    def __init__(self, credentials_file, spreadsheet_id):
        """Initialize the Google Sheets handler with credentials"""
        self.spreadsheet_id = spreadsheet_id
        self.SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        
        # Column mappings
        self.COLUMN_MAPPINGS = {
            'handle': 'A',
            'title': 'B',
            'option1_name': 'C',
            'option1_value': 'D',
            'option2_name': 'E',
            'option2_value': 'F',
            'option3_name': 'G',
            'option3_value': 'H',
            'sku': 'I',
            'hs_code': 'J',
            'coo': 'K',
            'location': 'L',
            'incoming': 'M',
            'unavailable': 'N',
            'committed': 'O',
            'available': 'P',
            'on_hand': 'Q',
            'acdc_stock': 'R',
            'stock_difference': 'S',
            'last_checked': 'T',
            'action_required': 'U',
            'notes': 'V'
        }
        
        try:
            self.credentials = service_account.Credentials.from_service_account_file(
                credentials_file, scopes=self.SCOPES)
            self.service = build('sheets', 'v4', credentials=self.credentials)
            self.sheet = self.service.spreadsheets()
            self.ensure_sheet_setup()
            logging.info("Successfully initialized Google Sheets handler")
        except Exception as e:
            logging.error(f"Failed to initialize Google Sheets handler: {str(e)}")
            raise

    def ensure_sheet_setup(self):
        """Ensure sheet is set up with correct headers and formatting"""
        if not self.check_headers_exist():
            self.create_header_row()
            self.format_sheet()

    def check_headers_exist(self):
        """Check if headers are already set up"""
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range='A1:V1'
            ).execute()
            return 'values' in result and len(result['values']) > 0
        except Exception:
            return False

    def create_header_row(self):
        """Create the header row with all required columns"""
        headers = [
            'Handle', 'Title', 'Option1 Name', 'Option1 Value',
            'Option2 Name', 'Option2 Value', 'Option3 Name', 'Option3 Value',
            'SKU', 'HS Code', 'COO', 'Location', 'Incoming', 'Unavailable',
            'Committed', 'Available', 'On hand',
            'ACDC Stock', 'Stock Difference', 'Last Checked', 'Action Required', 'Notes'
        ]
        
        try:
            self.sheet.values().update(
                spreadsheetId=self.spreadsheet_id,
                range='A1:V1',
                valueInputOption='USER_ENTERED',
                body={'values': [headers]}
            ).execute()
            return True
        except Exception as e:
            logging.error(f"Failed to create header row: {str(e)}")
            return False

    def format_sheet(self):
        """Apply basic formatting to the sheet"""
        try:
            requests = [
                {
                    'repeatCell': {
                        'range': {'sheetId': 0, 'startRowIndex': 0, 'endRowIndex': 1},
                        'cell': {
                            'userEnteredFormat': {
                                'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9},
                                'textFormat': {'bold': True}
                            }
                        },
                        'fields': 'userEnteredFormat(backgroundColor,textFormat)'
                    }
                },
                {
                    'autoResizeDimensions': {
                        'dimensions': {
                            'sheetId': 0,
                            'dimension': 'COLUMNS',
                            'startIndex': 0,
                            'endIndex': 22
                        }
                    }
                }
            ]
            
            self.sheet.batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={'requests': requests}
            ).execute()
            return True
        except Exception as e:
            logging.error(f"Failed to format sheet: {str(e)}")
            return False

    def update_stock_levels(self, sku, shopify_data, acdc_stock):
        """Update stock levels and tracking info for a product"""
        try:
            # Find row with matching SKU
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range=f'{self.COLUMN_MAPPINGS["sku"]}:${self.COLUMN_MAPPINGS["sku"]}'
            ).execute()
            
            rows = result.get('values', [])
            row_number = None
            
            for i, row in enumerate(rows):
                if row and row[0] == sku:
                    row_number = i + 1
                    break
            
            if row_number is None:
                # New product
                row_number = len(rows) + 1
                shopify_stock = shopify_data.get('on_hand', '0')
                
                values = [
                    [
                        shopify_data.get('handle', ''),
                        shopify_data.get('title', ''),
                        shopify_data.get('option1_name', ''),
                        shopify_data.get('option1_value', ''),
                        shopify_data.get('option2_name', ''),
                        shopify_data.get('option2_value', ''),
                        shopify_data.get('option3_name', ''),
                        shopify_data.get('option3_value', ''),
                        sku,
                        shopify_data.get('hs_code', ''),
                        shopify_data.get('coo', ''),
                        shopify_data.get('location', ''),
                        shopify_data.get('incoming', '0'),
                        shopify_data.get('unavailable', '0'),
                        shopify_data.get('committed', '0'),
                        shopify_data.get('available', '0'),
                        shopify_stock,
                        str(acdc_stock),
                        'Yes' if int(shopify_stock) != int(acdc_stock) else 'No',
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'Check stock levels' if int(shopify_stock) != int(acdc_stock) else '',
                        ''
                    ]
                ]
                
                self.sheet.values().append(
                    spreadsheetId=self.spreadsheet_id,
                    range='A:V',
                    valueInputOption='USER_ENTERED',
                    insertDataOption='INSERT_ROWS',
                    body={'values': values}
                ).execute()
            else:
                # Update existing product
                shopify_stock = shopify_data.get('on_hand', '0')
                
                self.sheet.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f'R{row_number}:V{row_number}',
                    valueInputOption='USER_ENTERED',
                    body={
                        'values': [[
                            str(acdc_stock),
                            'Yes' if int(shopify_stock) != int(acdc_stock) else 'No',
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'Check stock levels' if int(shopify_stock) != int(acdc_stock) else '',
                            ''
                        ]]
                    }
                ).execute()
            
            logging.info(f"Successfully updated stock levels for SKU: {sku}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to update stock levels: {str(e)}")
            return False

    def get_all_products(self):
        """Get all products and their stock levels"""
        try:
            result = self.sheet.values().get(
                spreadsheetId=self.spreadsheet_id,
                range='A2:V'  # Exclude header row
            ).execute()
            return result.get('values', [])
        except Exception as e:
            logging.error(f"Failed to get products: {str(e)}")
            return []

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
        
        time.sleep(2)  # Wait for products to load
        
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
        
        # Get existing products from sheet
        products = sheets_handler.get_all_products()
        logger.info(f"Found {len(products)} products in sheet")
        
        for product in products:
            try:
                title = product[1]  # Title is in column B
                sku = product[8]    # SKU is in column I
                current_stock = product[16]  # Current stock in column Q
                
                logger.info(f"Processing product: {title}")
                
                # Get ACDC stock levels
                acdc_stock = scraper.get_stock_levels(title)
                if acdc_stock:
                    total_acdc_stock = (int(acdc_stock.get('edenvale', 0)) + 
                                      int(acdc_stock.get('germiston', 0)))
                    
                    # Update Google Sheet
                    shopify_data = {
                        'handle': product[0],
                        'title': title,
                        'on_hand': current_stock
                    }
                    
                    sheets_handler.update_stock_levels(
                        sku,
                        shopify_data,
                        total_acdc_stock
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
scheduler.add_job(sync_stock, '
