from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

from config import DAYS_TO_EXPIRE_OLD_PRODUCTS
from data_manager import DataManager
from logger import Logger
from models import ProductDetails, ProcessedProductDetails,Promotion

load_dotenv()

client = None
db = None
collection = None
products_collection = None
data_manager = DataManager()


async def connect_to_database():
    global client, db, collection, products_collection, promotion_collection
    try:
        Logger.info('Connecting to the database')
        client = AsyncIOMotorClient(os.getenv('MONGO_URI'), serverSelectionTimeoutMS=10000)
        await client.server_info()
        db = client['PromoBot']
        collection = db['Searches']
        products_collection = db['Products']
        promotion_collection = db['Promotions']
        Logger.info("Successfully connected to the database")
    except Exception as e:
        raise ConnectionError(f"Failed to connect to the database: {str(e)}")


async def add_search(search_text):
    Logger.info(f"Adding search term: {search_text}")
    await collection.insert_one({"text": search_text})
    Logger.info(f"Added search term: {search_text}")


async def remove_search(search_text):
    Logger.info(f"Removing search term: {search_text}")
    result = await collection.delete_one({"text": search_text})
    is_deleted = result.deleted_count > 0
    if is_deleted:
        Logger.info(f"Removed search term: {search_text}")
    else:
        Logger.info(f"Search term not found: {search_text}")
    return is_deleted


async def get_all_searches():
    cursor = collection.find()
    searches = [doc['text'] async for doc in cursor]
    Logger.info(f"Found {len(searches)} search terms")
    return searches


async def upsert_product(product_details: ProductDetails):
    current_time = datetime.utcnow()
    product_id = product_details.id

    update_data = {
        "last_updated": current_time,
        "product_image_url": product_details.product_image_url,
        "product_title": product_details.product_title,
        "product_url": product_details.product_url,
        "product_asin": product_details.product_asin,
        "product_price": product_details.product_price,
        "product_sales": product_details.product_sales,
        "promotion_code": product_details.promotion_code,
        "promotion_title": product_details.promotion_title
    }

    result = await products_collection.update_one(
        {"_id": product_id},
        {"$set": update_data},
        upsert=True
    )

    Logger.info(f"Upserted product: {product_id}")
    return result.upserted_id is not None


async def upsert_promotion(promotion: Promotion):
    current_time = datetime.utcnow()
    updated_promotion = {
        "last_updated": current_time,
        "promotion_code": promotion.promotion_code,
        "promotion_title": promotion.promotion_title,
        "promotion_url": promotion.promotion_url,
        "product_title": promotion.product_title,
        "product_price": promotion.product_price,
        "product_img": promotion.product_img,
        "product_url": promotion.product_url
    }

    result = await promotion_collection.update_one(
        {"product_url": promotion.product_url},
        {"$set": updated_promotion},
        upsert=True
    )
    return result

async def get_promotion_by_url(product_url: str):
    
    return await promotion_collection.find_one({"product_url": product_url})

async def process_products(product_list: list[ProductDetails]) -> ProcessedProductDetails:
    cutoff_date = datetime.utcnow() - timedelta(days=DAYS_TO_EXPIRE_OLD_PRODUCTS)
    cutoff_sales = data_manager.get_monthly_sales_cutoff()
    processed_product_details = ProcessedProductDetails()

    for product in product_list:
        product_id = product.id
        doc = await products_collection.find_one(
            {
                "_id": product_id,
                "last_updated": {"$gte": cutoff_date}
            }
        )

        if doc is None:
            if product.product_sales >= cutoff_sales:
                upserted = await upsert_product(product)
                if upserted:
                    processed_product_details.upserted.append(product)
                else:
                    Logger.warn(f"Failed to upsert product: {product_id}")
            else:
                processed_product_details.below_threshold.append(product)
                Logger.warn(f"Product sales below threshold: {product_id}")
        else:
            processed_product_details.up_to_date.append(product)

    Logger.info(f"Processed {len(product_list)} products")
    Logger.info(f"Upserted {len(processed_product_details.upserted)} products")
    Logger.info(f"Found {len(processed_product_details.up_to_date)} up-to-date products")
    Logger.info(f"Found {len(processed_product_details.below_threshold)} below threshold products")
    return processed_product_details
