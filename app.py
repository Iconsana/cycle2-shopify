# At the top of app.py, update the Shopify configuration
shop_url = os.getenv('SHOPIFY_SHOP_URL')
api_key = os.getenv('SHOPIFY_API_KEY')
api_secret = os.getenv('SHOPIFY_API_SECRET')
access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')

# Ensure shop URL includes https://
if not shop_url.startswith('https://'):
    shop_url = f'https://{shop_url}'

shopify.ShopifyResource.set_site(f"{shop_url}/admin/api/2024-01")from flask import Flask, jsonify, render_template_string
from dotenv import load_dotenv
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import logging
from difflib import SequenceMatcher
import shopify
from apscheduler.schedulers.background import BackgroundScheduler

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Shopify configuration
shopify.Session.setup(api_key=os.getenv('SHOPIFY_API_KEY'), 
                     secret=os.getenv('SHOPIFY_API_SECRET'))

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

        # Get the product URL and navigate to it
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
            
            # Check if product is in stock
            in_stock_element = self.driver.find_element(By.CLASS_NAME, "stock-status")
            stock_data['status'] = in_stock_element.text
            
            # Get branch-specific stock levels
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

# Update the get_shopify_products function with better error handling
def get_shopify_products():
    """Get all products from Shopify store"""
    try:
        shop_url = os.getenv('SHOPIFY_SHOP_URL')
        access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')
        
        logger.info(f"Attempting to connect to shop: {shop_url}")
        
        if not shop_url or not access_token:
            raise ValueError("Missing Shopify credentials in environment variables")
            
        session = shopify.Session(shop_url, '2024-01', access_token)
        shopify.ShopifyResource.activate_session(session)
        
        products = shopify.Product.find()
        logger.info(f"Successfully connected to Shopify")
        return products
        
    except Exception as e:
        logger.error(f"Shopify connection error: {str(e)}")
        raise
    finally:
        shopify.ShopifyResource.clear_session()
def update_shopify_stock(product_id, stock_quantity):
    """Update Shopify product stock level"""
    session = shopify.Session(os.getenv('SHOPIFY_SHOP_URL'), 
                            '2024-01', 
                            os.getenv('SHOPIFY_ACCESS_TOKEN'))
    shopify.ShopifyResource.activate_session(session)
    
    try:
        product = shopify.Product.find(product_id)
        variant = product.variants[0]  # Assuming single variant
        variant.inventory_quantity = stock_quantity
        variant.save()
        logger.info(f"Updated stock for product {product_id} to {stock_quantity}")
    except Exception as e:
        logger.error(f"Error updating Shopify stock: {str(e)}")
    finally:
        shopify.ShopifyResource.clear_session()

def sync_stock():
    """Main function to sync stock levels"""
    logger.info("Starting stock sync")
    scraper = None
    try:
        # First, test Shopify connection
        logger.info("Testing Shopify connection...")
        shop_url = os.getenv('SHOPIFY_SHOP_URL')
        access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')
        session = shopify.Session(shop_url, '2024-01', access_token)
        shopify.ShopifyResource.activate_session(session)
        
        # Initialize scraper
        logger.info("Initializing web scraper...")
        scraper = ACDCStockScraper()
        
        # Get Shopify products
        logger.info("Fetching Shopify products...")
        shopify_products = get_shopify_products()
        logger.info(f"Found {len(shopify_products)} products in Shopify")
        
        for product in shopify_products:
            logger.info(f"Processing product: {product.title}")
            try:
                acdc_stock = scraper.get_stock_levels(product.title)
                if acdc_stock:
                    logger.info(f"Stock data found: {acdc_stock}")
                    total_stock = (int(acdc_stock.get('edenvale', 0)) + 
                                 int(acdc_stock.get('germiston', 0)))
                    update_shopify_stock(product.id, total_stock)
                else:
                    logger.warning(f"No stock data found for: {product.title}")
            except Exception as e:
                logger.error(f"Error processing product {product.title}: {str(e)}")
                continue
                
    except Exception as e:
        logger.error(f"Sync failed with error: {str(e)}")
        raise
    finally:
        if scraper:
            scraper.close()
        shopify.ShopifyResource.clear_session()
                    

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
        # Test Shopify connection first
        shop_url = os.getenv('SHOPIFY_SHOP_URL')
        access_token = os.getenv('SHOPIFY_ACCESS_TOKEN')
        
        if not shop_url or not access_token:
            return jsonify({
                "error": "Missing Shopify credentials",
                "shop_url_exists": bool(shop_url),
                "access_token_exists": bool(access_token)
            }), 500

        try:
            session = shopify.Session(shop_url, '2024-01', access_token)
            shopify.ShopifyResource.activate_session(session)
            # Test connection with a simple API call
            shop = shopify.Shop.current()
            logger.info(f"Successfully connected to shop: {shop.name}")
        except Exception as e:
            return jsonify({
                "error": "Shopify connection failed",
                "details": str(e)
            }), 500

        # If we get here, Shopify connection works
        sync_stock()
        return jsonify({"status": "sync completed"})
    except Exception as e:
        logger.error(f"Sync failed with error: {str(e)}")
        return jsonify({
            "error": str(e),
            "type": type(e).__name__,
            "details": getattr(e, 'message', str(e))
        }), 500

@app.route('/test-config')
def test_config():
    """Test endpoint to verify configuration"""
    return jsonify({
        "shopify_url_set": bool(os.getenv('SHOPIFY_SHOP_URL')),
        "shopify_token_set": bool(os.getenv('SHOPIFY_ACCESS_TOKEN')),
        "shopify_api_key_set": bool(os.getenv('SHOPIFY_API_KEY')),
        "shopify_secret_set": bool(os.getenv('SHOPIFY_API_SECRET'))
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
