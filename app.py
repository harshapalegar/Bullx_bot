from flask import Flask, request
from telegram import Bot
from telegram.utils.request import Request
from PIL import Image
from io import BytesIO
import re
import os
import logging
from datetime import datetime
import requests
from pymongo import MongoClient

# Environment variables
MONGODB_URI = os.environ.get('MONGODB_URI')
BOT_TOKEN = os.environ.get('BOT_TOKEN')
HELIUS_KEY = os.environ.get('HELIUS_KEY')

client = MongoClient(MONGODB_URI)
db = client.sol_wallets
wallets_collection = db.wallets

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def send_message_to_user(bot_token, user_id, message):
    request = Request(con_pool_size=8)
    bot = Bot(bot_token, request=request)
    bot.send_message(
        chat_id=user_id,
        text=message,
        parse_mode="Markdown",
        disable_web_page_preview=True)

def send_image_to_user(bot_token, user_id, message, image_url):
    request = Request(con_pool_size=8)
    bot = Bot(bot_token, request=request)
    image_bytes = get_image(image_url)
    bot.send_photo(
        chat_id=user_id,
        photo=image_bytes,
        caption=message,
        parse_mode="Markdown")
    
def get_image(url):
    response = requests.get(url).content
    image = Image.open(BytesIO(response))
    image = image.convert('RGB')
    max_size = (800, 800)
    image.thumbnail(max_size, Image.LANCZOS)  # Updated from ANTIALIAS
    image_bytes = BytesIO()
    image.save(image_bytes, 'JPEG', quality=85)
    image_bytes.seek(0)
    return image_bytes

def format_wallet_address(match_obj):
    wallet_address = match_obj.group(0)
    return wallet_address[:4] + "..." + wallet_address[-4:]

def get_compressed_image(asset_id):
    url = f'https://rpc.helius.xyz/?api-key={HELIUS_KEY}'
    r_data = {
        "jsonrpc": "2.0",
        "id": "my-id",
        "method": "getAsset",
        "params": [
            asset_id
        ]
    }
    r = requests.post(url, json=r_data)
    url_meta = r.json()['result']['content']['json_uri']
    r = requests.get(url=url_meta)
    return r.json()['image']

def check_image(data):
    token_mint = ''
    for token in data[0]['tokenTransfers']:
        if 'NonFungible' in token['tokenStandard']:
            token_mint = token['mint']
    
    if len(token_mint) > 0:
        url = f"https://api.helius.xyz/v0/token-metadata?api-key={HELIUS_KEY}"
        nft_addresses = [token_mint]
        r_data = {
            "mintAccounts": nft_addresses,
            "includeOffChain": True,
            "disableCache": False,
        }

        r = requests.post(url=url, json=r_data)
        j = r.json()
        if 'metadata' not in j[0]['offChainMetadata']:
            return ''
        if 'image' not in j[0]['offChainMetadata']['metadata']:
            return ''
        image = j[0]['offChainMetadata']['metadata']['image']
        return image
    else:
        if 'compressed' in data[0]['events']:
            if 'assetId' in data[0]['events']['compressed'][0]:
                asset_id = data[0]['events']['compressed'][0]['assetId']
                try:
                    image = get_compressed_image(asset_id)
                    return image
                except:
                    return ''
        return ''

def create_message(data):
    tx_type = data[0]['type'].replace("_", " ")
    tx = data[0]['signature']
    source = data[0]['source']
    description = data[0]['description']

    accounts = []
    for inst in data[0]["instructions"]:
        accounts = accounts + inst["accounts"]
    
    if len(data[0]['tokenTransfers']) > 0:
        for token in data[0]['tokenTransfers']:
            accounts.append(token['fromUserAccount'])
            accounts.append(token['toUserAccount'])
        accounts = list(set(accounts))

    image = check_image(data)
    
    found_docs = list(wallets_collection.find(
        {
            "address": {"$in": accounts},
            "status": "active"
        }
    ))
    found_users = [i['user_id'] for i in found_docs]
    found_users = set(found_users)
    logging.info(found_users)
    
    messages = []
    for user in found_users:
        if source != "SYSTEM_PROGRAM":
            message = f'*{tx_type}* on {source}'
        else:
            message = f'*{tx_type}*'
        if len(description) > 0:
            message = message + '\n\n' + data[0]['description']

            user_wallets = [i['address'] for i in found_docs if i['user_id']==user]
            for user_wallet in user_wallets:
                if user_wallet not in message:
                    continue
                formatted_user_wallet = user_wallet[:4] + '...' + user_wallet[-4:]
                message = message.replace(user_wallet, f'*YOUR WALLET* ({formatted_user_wallet})')

        formatted_text = re.sub(r'[A-Za-z0-9]{32,44}', format_wallet_address, message)
        formatted_text = formatted_text + f'\n[XRAY](https://xray.helius.xyz/tx/{tx}) | [Solscan](https://solscan.io/tx/{tx})'
        formatted_text = formatted_text.replace("#", "").replace("_", " ")
        messages.append({'user': user, 'text': formatted_text, 'image': image})
    return messages

app = Flask(__name__)

@app.route('/wallet', methods=['POST'])
def handle_webhook():
    data = request.json
    
    messages = create_message(data)

    for message in messages:
        db_entry = {
            "user": message['user'],
            "message": message['text'],
            "datetime": datetime.now()
        }
        db.messages.insert_one(db_entry)

        logging.info(message)

        if len(message['image']) > 0:
            try:
                send_image_to_user(BOT_TOKEN, message['user'], message['text'], message['image'])
            except Exception as e:
                logging.info(e)
                send_message_to_user(BOT_TOKEN, message['user'], message['text'])    
        else:
            send_message_to_user(BOT_TOKEN, message['user'], message['text'])

    logging.info('ok event')
    return 'OK'

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(debug=False, host='0.0.0.0', port=port)
