from flask import Flask, jsonify
from dotenv import load_dotenv
import os
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import logging
from difflib import SequenceMatcher

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

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

    def get_stock_levels(self, product_url):
        """Get stock levels for a specific product"""
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

    def health_check(self):
        """Health check method"""
        try:
            self.driver.get(self.base_url)
            return True
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return False

    def close(self):
        """Clean up resources"""
        self.driver.quit()

# API Routes

# Add this route at the top of your routes section in app.py
@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "endpoints": {
            "/health": "Check system health",
            "/check-stock/<product_title>": "Check stock for a specific product"
        }
    })

@app.route('/health')
def health():
    """Health check endpoint"""
    scraper = ACDCStockScraper()
    try:
        status = scraper.health_check()
        scraper.close()
        return jsonify({"status": "healthy" if status else "unhealthy"})
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.route('/check-stock/<product_title>')
def check_stock(product_title):
    """Endpoint to check stock for a specific product"""
    scraper = ACDCStockScraper()
    try:
        result = scraper.process_product(product_title)
        scraper.close()
        return jsonify({"success": result})
    except Exception as e:
        logger.error(f"Error checking stock: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
