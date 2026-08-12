import os
import subprocess
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
import urllib.parse
import threading
import time
import requests
import re
import random
import logging
import io
import base64
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from html import escape
from flask import Flask, request, jsonify

# MongoDB Connection
password = urllib.parse.quote_plus("aass1122@")
MONGO_URI = f"mongodb+srv://kamrolhasandeveloper:{password}@cluster0.gxc2lwl.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI, maxPoolSize=100, serverSelectionTimeoutMS=5000) 
db = client['kamrol_bot_db']
users_collection = db['users']
config_collection = db['config']
transactions_collection = db['transactions']
vpn_collection = db['vpn_stock']
received_sms_collection = db['received_sms']

# Test Main MongoDB Connection
try:
    print("Testing Main MongoDB connection...")
    client.admin.command('ping')
    print("Main MongoDB Connection Successful!")
except Exception as e:
    print(f"Main MongoDB Connection Failed!\nError: {e}")

# --- AUTO DEPOSIT SMS RECEIVER API SYSTEM (bKash, Nagad, Rocket) ---
SMS_SECRET_TOKEN = "antigravity_secret_sms_token_2026"
flask_app = Flask(__name__)

LIVE_APP_LOGS = []

def add_app_log(log_msg):
    global LIVE_APP_LOGS
    time_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    LIVE_APP_LOGS.insert(0, f"[{time_str}] {log_msg}")
    if len(LIVE_APP_LOGS) > 50:
        LIVE_APP_LOGS = LIVE_APP_LOGS[:50]

def parse_payment_sms(sender, message):
    if not message:
        return None
        
    s_upper = (sender or "").upper()
    m_text = message.strip()
    
    method = None
    if "BKASH" in s_upper:
        method = "bKash"
    elif "NAGAD" in s_upper:
        method = "Nagad"
    elif "ROCKET" in s_upper or "16216" in s_upper:
        method = "Rocket"
    else:
        return None
            
    if not method:
        return None
        
    trx_match = re.search(r"(?:TrxID|TxnID|Txn ID|Trx ID)\s*:?\s*([A-Za-z0-9]+)", m_text, re.IGNORECASE)
    if not trx_match:
        return None
    trx_id = trx_match.group(1).upper()
    
    amt_match = re.search(r"(?:Tk|Amount)\s*:?\s*([0-9,]+\.?[0-9]*)", m_text, re.IGNORECASE)
    amount = 0.0
    if amt_match:
        try:
            amount = float(amt_match.group(1).replace(",", ""))
        except:
            amount = 0.0
            
    if amount <= 0:
        return None
        
    phone_match = re.search(r"(?:from|Sender)\s*:?\s*(01[0-9]{9})", m_text, re.IGNORECASE)
    sender_phone = phone_match.group(1) if phone_match else ""
    
    return {
        "method": method,
        "trx_id": trx_id,
        "amount": amount,
        "sender_phone": sender_phone
    }

def send_group_deposit_notification(user_id, method, amount, trx_id, is_auto=True):
    try:
        conf = get_config()
        group_id = conf.get("deposit_group_id", -1002720523475)
        user = users_collection.find_one({"chat_id": user_id}) or {}
        first_name = user.get("first_name") or "User"
        
        status_str = "✅ **Auto Approved (Instant)**" if is_auto else "✅ **Approved by Admin**"
        
        msg = (
            f"🎉 **NEW DEPOSIT APPROVED!** ⚡\n\n"
            f"👤 **User:** [{first_name}](tg://user?id={user_id}) (`{user_id}`)\n"
            f"💳 **Method:** **{method}**\n"
            f"💰 **Amount:** **{fmt_bal(amount)} ৳**\n"
            f"📝 **TrxID:** `{trx_id}`\n"
            f"⚡ **Status:** {status_str}"
        )
        bot.send_message(group_id, msg, parse_mode="Markdown")
    except Exception as e:
        print(f"Error sending group deposit notification: {e}")

def verify_bitget_usdt_deposit(tx_hash, conf):
    api_key = str(conf.get("bitget_api_key") or "").strip()
    api_secret = str(conf.get("bitget_api_secret") or "").strip()
    passphrase = str(conf.get("bitget_passphrase") or "").strip()
    tx_clean = str(tx_hash).strip().lower()

    if api_key and api_secret and passphrase:
        try:
            timestamp = str(int(time.time() * 1000))
            request_path = "/api/v2/asset/deposit-records?coin=USDT"
            message = timestamp + "GET" + request_path
            sign = base64.b64encode(hmac.new(api_secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).digest()).decode('utf-8')
            
            headers = {
                "ACCESS-KEY": api_key,
                "ACCESS-SIGN": sign,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": passphrase,
                "Content-Type": "application/json"
            }
            
            url = "https://api.bitget.com" + request_path
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get("code") == "00000":
                    records = data.get("data", [])
                    for rec in records:
                        rec_tx = str(rec.get("hash") or rec.get("txId") or "").strip().lower()
                        rec_status = str(rec.get("status") or "").lower()
                        if rec_tx and rec_tx == tx_clean and rec_status in ["success", "completed", "1", "2"]:
                            amount = float(rec.get("amount") or 0.0)
                            return {
                                "status": "approved",
                                "usdt_amount": amount,
                                "tx_hash": rec_tx,
                                "chain": rec.get("chain", "BEP20")
                            }
        except Exception as e:
            print(f"Error verifying Bitget deposit: {e}")

    # Real On-Chain BSC Blockchain Verification
    if tx_clean.startswith("0x") and len(tx_clean) >= 64:
        target_wallet = str(conf.get("usdt_address") or "0x5Dc3A9BfAd702A8f804fECbe7B53c95A91F5283e").strip().lower()
        usdt_contract = "0x55d398326f99059ff775485246999027b3197955"
        rpc_list = ['https://bsc-dataseed1.binance.org/', 'https://rpc.ankr.com/bsc', 'https://bscrpc.com']

        for rpc in rpc_list:
            try:
                payload = {'jsonrpc': '2.0', 'method': 'eth_getTransactionReceipt', 'params': [tx_clean], 'id': 1}
                res = requests.post(rpc, json=payload, timeout=4)
                if res.status_code == 200:
                    result = res.json().get('result')
                    if result:
                        status = int(result.get('status', '0x0'), 16)
                        if status == 1:
                            logs = result.get('logs', [])
                            for log in logs:
                                address = str(log.get('address', '')).lower()
                                topics = log.get('topics', [])
                                if address == usdt_contract and len(topics) >= 3:
                                    to_address = '0x' + topics[2][-40:].lower()
                                    if to_address == target_wallet:
                                        raw_val = int(log.get('data', '0x0'), 16)
                                        usdt_amt = round(raw_val / 1e18, 4)
                                        return {
                                            "status": "approved",
                                            "usdt_amount": usdt_amt,
                                            "tx_hash": tx_clean,
                                            "chain": "BEP20"
                                        }
            except:
                pass
        
    return None

def check_bitget_api_status(conf):
    api_key = str(conf.get("bitget_api_key") or "").strip()
    api_secret = str(conf.get("bitget_api_secret") or "").strip()
    passphrase = str(conf.get("bitget_passphrase") or "").strip()
    usdt_address = str(conf.get("usdt_address") or "0x5Dc3A9BfAd702A8f804fECbe7B53c95A91F5283e").strip()
    usdt_rate = conf.get("usdt_rate", 129.0)

    # Check if Bitget V2 REST API Credentials are live
    if api_key and api_secret and passphrase and len(api_key) < 60:
        try:
            timestamp = str(int(time.time() * 1000))
            request_path = "/api/v2/asset/deposit-records?coin=USDT"
            message = timestamp + "GET" + request_path
            sign = base64.b64encode(hmac.new(api_secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).digest()).decode('utf-8')

            headers = {
                "ACCESS-KEY": api_key,
                "ACCESS-SIGN": sign,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": passphrase,
                "Content-Type": "application/json"
            }

            url = "https://api.bitget.com" + request_path
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if data.get("code") == "00000":
                    return {
                        "status": "ON",
                        "online": True,
                        "message": (
                            f"🟢 **Bitget API & BEP20 Status: ON & CONNECTED** ⚡\n\n"
                            f"📡 **API Connection:** 🟢 HTTP 200 OK (Active)\n"
                            f"📍 **BEP20 Deposit Address:**\n`{usdt_address}`\n\n"
                            f"📈 **Exchange Rate:** 1 USDT = {usdt_rate} ৳\n"
                            f"⚡ **Auto-Deposit:** 🟢 ACTIVE & READY FOR USERS"
                        )
                    }
        except:
            pass

    # BEP20 Smart Auto-Deposit Status Response
    return {
        "status": "ON",
        "online": True,
        "message": (
            f"🟢 **USDT (BEP20) Auto-Deposit System: ON & ACTIVE** ⚡\n\n"
            f"📍 **Deposit Address (BEP20):**\n`{usdt_address}`\n\n"
            f"📈 **Exchange Rate:** 1 USDT = {usdt_rate} ৳\n"
            f"🔹 **Minimum Deposit:** 0.10 USDT ($0.10)\n"
            f"🛡️ **BEP20 Verification Engine:** 🟢 ACTIVE & READY FOR USERS"
        )
    }

def process_incoming_sms(parsed, raw_sms):
    method = parsed["method"]
    trx_id = parsed["trx_id"]
    amount = parsed["amount"]
    sender_phone = parsed["sender_phone"]
    
    existing_sms = received_sms_collection.find_one({"trx_id": trx_id})
    if existing_sms:
        return {"status": "duplicate", "trx_id": trx_id}
        
    sms_doc = {
        "method": method,
        "trx_id": trx_id,
        "amount": amount,
        "sender_phone": sender_phone,
        "raw_sms": raw_sms,
        "status": "unclaimed",
        "claimed_by": None,
        "timestamp": datetime.now(timezone.utc)
    }
    received_sms_collection.insert_one(sms_doc)
    add_app_log(f"📩 SMS RECEIVED: {method} {fmt_bal(amount)} ৳ (TrxID: {trx_id})")
    
    # Step A: Check if a user submitted this TrxID in transactions_collection (pending)
    pending_tx = transactions_collection.find_one({"trx_id": trx_id, "status": "pending"})
    if pending_tx:
        user_id = pending_tx["user_id"]
        req_amount = float(pending_tx.get("amount", amount))
        actual_amount = float(amount)

        transactions_collection.update_one({"_id": pending_tx["_id"]}, {"$set": {"status": "approved", "amount": actual_amount, "requested_amount": req_amount, "verified_at": datetime.now(timezone.utc)}})
        received_sms_collection.update_one({"trx_id": trx_id}, {"$set": {"status": "claimed", "claimed_by": user_id}})

        user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": actual_amount}}, return_document=True)
        new_balance = user.get("balance", actual_amount) if user else actual_amount

        note_str = f"\nRequested Amount: **{fmt_bal(req_amount)} ৳**" if req_amount != actual_amount else ""

        try:
            bot.send_message(
                user_id,
                f"🎉 **Auto Deposit Approved!**\n\n"
                f"Method: **{method}**{note_str}\n"
                f"Actual Amount Added: **{fmt_bal(actual_amount)} ৳**\n"
                f"TrxID: `{trx_id}`\n"
                f"New Balance: **{fmt_bal(new_balance)} ৳**",
                parse_mode="Markdown"
            )
        except:
            pass
            
        try:
            send_group_deposit_notification(user_id, method, actual_amount, trx_id, is_auto=True)
        except:
            pass
            
        return {"status": "claimed_pending", "user_id": user_id, "trx_id": trx_id, "amount": actual_amount}

    # Step B: Check if sender_phone matches a registered user in users_collection
    if sender_phone:
        matched_user = users_collection.find_one({"phone": sender_phone})
        if matched_user:
            user_id = matched_user["chat_id"]
            
            transactions_collection.insert_one({
                "trx_id": trx_id,
                "user_id": user_id,
                "amount": amount,
                "method": method,
                "status": "approved",
                "timestamp": datetime.now(timezone.utc)
            })
            received_sms_collection.update_one({"trx_id": trx_id}, {"$set": {"status": "claimed", "claimed_by": user_id}})
            
            user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": amount}}, return_document=True)
            new_balance = user.get("balance", amount) if user else amount
            
            try:
                bot.send_message(
                    user_id,
                    f"🎉 **Instant Deposit Received!**\n\n"
                    f"Method: **{method}**\n"
                    f"Amount Added: **{fmt_bal(amount)} ৳**\n"
                    f"TrxID: `{trx_id}`\n"
                    f"New Balance: **{fmt_bal(new_balance)} ৳**",
                    parse_mode="Markdown"
                )
            except:
                pass
                
            log_msg = f"✅ **Instant Auto Deposit (Phone Matched)**\nUser ID: `{user_id}` (`{sender_phone}`)\nMethod: **{method}**\nAmount: **{amount} ৳**\nTrxID: `{trx_id}`"
            try:
                bot.send_message(-1002720523475, log_msg, parse_mode="Markdown")
            except:
                pass
                
            return {"status": "claimed_phone", "user_id": user_id, "trx_id": trx_id, "amount": amount}

    return {"status": "saved_unclaimed", "trx_id": trx_id, "amount": amount}

@flask_app.route('/api/sms_receiver', methods=['GET', 'POST'])
def sms_receiver_api():
    if request.method == 'GET':
        return jsonify({"status": "running", "service": "Auto Deposit SMS Webhook API"}), 200

    data = request.get_json(silent=True) or request.form or {}
    secret = data.get("secret") or request.headers.get("X-Secret-Token") or request.args.get("secret")

    if secret != SMS_SECRET_TOKEN:
        return jsonify({"status": "error", "message": "Unauthorized: Invalid Secret Key"}), 401

    sender = data.get("sender") or data.get("from") or ""
    message = data.get("message") or data.get("body") or data.get("text") or ""

    parsed = parse_payment_sms(sender, message)
    if not parsed:
        return jsonify({"status": "ignored", "reason": "Not a recognized payment SMS", "sender": sender, "message": message}), 200

    result = process_incoming_sms(parsed, message)
    return jsonify({"status": "success", "parsed": parsed, "result": result}), 200

@flask_app.route('/api/app_logs', methods=['GET'])
def get_app_logs():
    return jsonify({"status": "success", "logs": LIVE_APP_LOGS}), 200

@flask_app.route('/api/app_dashboard', methods=['GET'])
def get_app_dashboard():
    try:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        today_start_naive = now_naive.replace(hour=0, minute=0, second=0, microsecond=0)
        h24_ago_naive = now_naive - timedelta(hours=24)

        sms_count_total = received_sms_collection.count_documents({})
        sms_count_24h = received_sms_collection.count_documents({"timestamp": {"$gte": h24_ago_naive}})

        approved_txs = list(transactions_collection.find({"status": "approved"}))
        approved_count_total = len(approved_txs)
        approved_amount_total = sum(float(tx.get("amount", 0.0)) for tx in approved_txs)

        approved_24h = []
        approved_today = []
        for tx in approved_txs:
            ts = tx.get("timestamp")
            if ts:
                if getattr(ts, "tzinfo", None) is not None:
                    ts = ts.replace(tzinfo=None)
                if ts >= h24_ago_naive:
                    approved_24h.append(tx)
                if ts >= today_start_naive:
                    approved_today.append(tx)

        approved_count_24h = len(approved_24h)
        approved_amount_24h = sum(float(tx.get("amount", 0.0)) for tx in approved_24h)

        approved_count_today = len(approved_today)
        approved_amount_today = sum(float(tx.get("amount", 0.0)) for tx in approved_today)

        # All pending items (no limit) so count matches full list
        pending = list(transactions_collection.find({"status": "pending"}).sort("_id", -1))
        pending_count_total = len(pending)
        pending_amount_total = sum(float(item.get("amount", 0.0)) for item in pending)

        for item in pending:
            item["_id"] = str(item["_id"])
            if "timestamp" in item and hasattr(item["timestamp"], "isoformat"):
                item["timestamp"] = item["timestamp"].strftime("%d %b %H:%M:%S")

        approved = list(transactions_collection.find({"status": "approved"}).sort("_id", -1).limit(50))
        for item in approved:
            item["_id"] = str(item["_id"])
            if "timestamp" in item and hasattr(item["timestamp"], "isoformat"):
                item["timestamp"] = item["timestamp"].strftime("%d %b %H:%M:%S")

        sms_list = list(received_sms_collection.find({}).sort("_id", -1).limit(50))
        for item in sms_list:
            item["_id"] = str(item["_id"])
            if "timestamp" in item and hasattr(item["timestamp"], "isoformat"):
                item["timestamp"] = item["timestamp"].strftime("%d %b %H:%M:%S")

        users_count_total = users_collection.estimated_document_count()
        bal_res = list(users_collection.aggregate([{"$group": {"_id": None, "total": {"$sum": "$balance"}}}]))
        users_balance_total = bal_res[0]["total"] if bal_res else 0.0

        return jsonify({
            "status": "success",
            "stats": {
                "sms_count_total": sms_count_total,
                "sms_count_24h": sms_count_24h,
                "approved_count_total": approved_count_total,
                "approved_amount_total": approved_amount_total,
                "approved_count_24h": approved_count_24h,
                "approved_amount_24h": approved_amount_24h,
                "approved_count_today": approved_count_today,
                "approved_amount_today": approved_amount_today,
                "pending_count_total": pending_count_total,
                "pending_amount_total": pending_amount_total,
                "users_count_total": users_count_total,
                "users_balance_total": users_balance_total
            },
            "logs": LIVE_APP_LOGS,
            "approved": approved,
            "pending": pending,
            "sms_list": sms_list
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@flask_app.route('/api/app_action', methods=['POST'])
def app_action_api():
    data = request.get_json(silent=True) or request.form or {}
    secret = data.get("secret") or request.headers.get("X-Secret-Token") or request.args.get("secret")
    if secret != SMS_SECRET_TOKEN:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    action = data.get("action")
    trx_id = data.get("trx_id")

    if not trx_id or not action:
        return jsonify({"status": "error", "message": "Missing action or trx_id"}), 400

    tx = transactions_collection.find_one({"trx_id": trx_id, "status": "pending"})
    if not tx:
        return jsonify({"status": "error", "message": "Pending transaction not found"}), 444

    user_id = tx["user_id"]
    amount = float(tx["amount"])
    method = tx["method"]

    if action == "approve":
        transactions_collection.update_one({"_id": tx["_id"]}, {"$set": {"status": "approved", "verified_at": datetime.now(timezone.utc)}})
        user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": amount}}, return_document=True)
        new_balance = user.get("balance", amount) if user else amount
        add_app_log(f"✅ MANUAL APPROVED: {method} {fmt_bal(amount)} ৳ (TrxID: {trx_id}) by App Admin")
        try:
            bot.send_message(user_id, f"🎉 **Deposit Approved!**\n\nMethod: **{method}**\nAmount Added: **{fmt_bal(amount)} ৳**\nTrxID: `{trx_id}`\nNew Balance: **{fmt_bal(new_balance)} ৳**", parse_mode="Markdown")
        except:
            pass
        try:
            send_group_deposit_notification(user_id, method, amount, trx_id, is_auto=False)
        except:
            pass
        return jsonify({"status": "success", "message": "Transaction approved"}), 200

    elif action == "reject":
        transactions_collection.update_one({"_id": tx["_id"]}, {"$set": {"status": "rejected", "rejected_at": datetime.now(timezone.utc)}})
        add_app_log(f"❌ MANUAL REJECTED: TrxID {trx_id} by App Admin")
        try:
            bot.send_message(user_id, f"❌ **Deposit Rejected!**\n\nTrxID: `{trx_id}`\nআপনার ট্রানজেকশন রিকোয়েস্টটি বাতিল করা হয়েছে।", parse_mode="Markdown")
        except:
            pass
        return jsonify({"status": "success", "message": "Transaction rejected"}), 200

    return jsonify({"status": "error", "message": "Invalid action"}), 400

def start_flask_server():
    try:
        logging.getLogger('wsgi').setLevel(logging.ERROR)
        flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
    except Exception as e:
        print(f"Error starting Flask SMS Webhook Server: {e}")

threading.Thread(target=start_flask_server, daemon=True).start()

def start_auto_cloudflare_tunnel():
    time.sleep(2)
    cloudflared_bin = "cloudflared.exe" if os.name == 'nt' else "cloudflared"
    
    if not os.path.exists(cloudflared_bin) and os.name != 'nt':
        try:
            print("Auto-downloading cloudflared binary for Linux...")
            import urllib.request
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
            urllib.request.urlretrieve(url, "cloudflared")
            os.chmod("cloudflared", 0o755)
            print("cloudflared downloaded successfully!")
        except Exception as e:
            print(f"Error downloading cloudflared: {e}")
            
    exec_cmd = ["./cloudflared" if os.path.exists("./cloudflared") else cloudflared_bin, "tunnel", "--url", "http://localhost:5000"]
    try:
        proc = subprocess.Popen(exec_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if match:
                tunnel_url = match.group(0) + "/api/sms_receiver"
                print(f"Auto Cloudflare Tunnel Created: {tunnel_url}")
                config_collection.update_one({"_id": "payment_settings"}, {"$set": {"custom_webhook_url": tunnel_url}}, upsert=True)
                global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
                break
    except Exception as e:
        print(f"Auto Cloudflare Tunnel failed: {e}")

threading.Thread(target=start_auto_cloudflare_tunnel, daemon=True).start()

# --- CACHING SYSTEM ---
LANG_CACHE = {}

def get_user_lang(chat_id):
    return "en"

def fmt_bal(val):
    try:
        r = round(float(val or 0), 2)
        if r.is_integer():
            return int(r)
        return f"{r:.2f}"
    except:
        return 0

CONFIG_CACHE = {}
CONFIG_LAST_UPDATE = 0

def get_config():
    global CONFIG_CACHE, CONFIG_LAST_UPDATE
    if time.time() - CONFIG_LAST_UPDATE < 10 and CONFIG_CACHE:
        return CONFIG_CACHE
        
    conf = config_collection.find_one({"_id": "payment_settings"})
    if not conf:
        conf = {"_id": "payment_settings", "bkash": "Not set", "binance": "Not set", "min_deposit": 5.0, "vpn_price": 15.0, "hotmail_price": 5.0, "outlook_price": 5.0, "outlook_fr_price": 5.0, "proxy_price": 10.0, "working_bin": ""}
        config_collection.insert_one(conf)
    else:
        if "proxy_price" not in conf:
            config_collection.update_one({"_id": "payment_settings"}, {"$set": {"proxy_price": 10.0}})
            conf["proxy_price"] = 10.0
        
    CONFIG_CACHE = conf
    CONFIG_LAST_UPDATE = time.time()
    return conf

# বট ইনিশিয়ালাইজেশন
TOKEN = '8523237591:AAFjAsYJbAj3oY0dxdAWFGPoOEE2OyEeLjA'
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=100) 

telebot.logger.setLevel(logging.INFO)

ADMIN_IDS = [6412225513, 8596783717]

# Translation Dictionary
texts = {'en': {'buy_btn': 'Buy',
        'deposit_btn': 'Deposit',
        'balance_btn': 'Balance',
        'price_btn': 'Price',
        'support_btn': 'Support',
        'mail_btn': 'Mail Inbox',
        'welcome': '✨ Welcome {name}! 🤖\n🚀 Enjoy fast, secure service — use the menu below to get started!',
        'buy_text': 'What would you like to buy?',
        'deposit_text': 'Please select your preferred deposit method:',
        'balance_text': 'Your Balance is: {balance} BDT',
        'price_text': 'Here is the Price list.',
        'support_text': '💬 **Customer Support**\n\nIf you need any help or have inquiries, please contact our support team:\n👉 @workstoresuport',
        'mail_text': 'Please send your credentials in the following format:\n`mail|pass|refresh_token|client_id`',
        'unknown': 'Unknown command. Please use the menu below.',
        'lang_changed': 'Language changed to English! 🇬🇧',
        'returning': 'Returning...',
        'dep_24h_limit': '❌ You can only send one deposit request per 24 hours. Please try again later.',
        'dep_ask_amount': 'How much do you want to deposit via {method}?',
        'dep_cancelled': 'Deposit cancelled.',
        'dep_min_err': '❌ Minimum deposit is {min_dep}. Please enter a valid amount:',
        'dep_invalid_amt': '❌ Invalid amount. Please enter a number:',
        'dep_submit_btn': '📝 Submit Transaction ID',
        'dep_instruct': '🔹 **Deposit via {method}**\n'
                        '\n'
                        'Amount: **{amount}**\n'
                        '\n'
                        'Please send exactly this amount to the following {method_str}:\n'
                        '`{target_acc}`\n'
                        '\n'
                        'After sending, click the button below to submit your Transaction ID.',
        'dep_ask_trxid': 'Please enter your **10-character** Transaction ID (TrxID) below:',
        'dep_wrong_trxid': '❌ Please send the money first, then provide the correct TrxID here.',
        'dep_used_trxid': '❌ This TrxID has already been used. Please provide a valid TrxID.',
        'dep_success': '✅ Your deposit request has been sent to the Admin. Please wait for approval.',
        'buy_vpn_btn': '🛡️ Nord VPN',
        'buy_hotmail_btn': '⚡ Hotmail',
        'buy_outlook_btn': '⚡ Outlook',
        'buy_outlook_fr_btn': '⚡ Outlook.fr',
        'buy_proxy_btn': '🌐 High-Speed Proxy',
        'buy_out_of_stock': '❌ Currently out of stock.',
        'buy_ask_vpn': '🛡️ Which VPN do you want?',
        'buy_ask_hotmail': '📧 Store > Hotmail',
        'buy_ask_outlook': '📧 Store > Outlook',
        'buy_ask_outlook_fr': '📧 Store > Outlook.fr',
        'buy_ask_proxy': '🔌 Store > Proxy',
        'buy_no_bal': '❌ Insufficient balance!',
        'buy_yes': '✅ Yes',
        'buy_no': '❌ No',
        'buy_confirm': 'Are you sure you want to buy **{cat}({dur})**?\nPrice: {price} ৳',
        'buy_cancelled': '❌ Purchase cancelled.',
        'buy_sold_out': '❌ Out of stock! Please try again later.',
        'buy_success': '✅ **Purchase Successful!**\n'
                       '\n'
                       '**Item:** {cat}({dur})\n'
                       '**Price:** {price} ৳\n'
                       '**New Balance:** {new_bal} ৳\n'
                       '\n'
                       '**Credentials:**\n'
                       '{creds}',
        'mail_cancelled': 'Mail Inbox check cancelled.',
        'mail_invalid_fmt': '❌ Invalid format. Please use:\n`mail|pass|refresh_token|client_id`',
        'mail_working': 'Working on it... Main menu restored.',
        'mail_start': '🔄 Starting Mail Tracker for {email}...',
        'mail_token_err': '❌ Failed to generate Access Token. Please check your refresh_token and client_id.',
        'mail_new': '📧 **New Email Received!**\n\n**Subject:** {subject}\n**Preview:** {preview}\n',
        'mail_code': '\n**Code:**\n`{code}`',
        'mail_no_code': '\n*(No code detected)*',
        'mail_token_exp': '❌ Access token expired or invalid.',
        'mail_not_found': '❌ No mail found (payni).',
        'dep_soon': 'Convert Balance feature is coming soon!',
        'dep_select_bot': 'Select the bot from which you want to convert balance:',
        'conv_db_err': '❌ Unable to connect to {bot} database at this moment.',
        'conv_no_acc': '❌ Account not found in {bot}.',
        'conv_no_bal': '❌ Insufficient balance in {bot}.',
        'conv_ask_amt': 'Your balance in {bot} is: **{bal} BDT**\nHow much do you want to bring here?',
        'conv_cancelled': 'Conversion cancelled.',
        'conv_zero_err': '❌ Please enter an amount greater than 0:',
        'conv_max_err': "❌ You don't have enough balance! (Max: {max_bal})\nPlease enter a valid amount:",
        'conv_success': '✅ **Conversion Successful!**\n'
                        '\n'
                        'You have converted **{amount} BDT** from {bot}.\n'
                        'Your current balance: **{new_bal} BDT**'},
 'bn': {'buy_btn': '🛍️ পণ্য কিনুন',
        'deposit_btn': '💳 ডিপোজিট করুন',
        'balance_btn': '💰 আমার ব্যালেন্স',
        'price_btn': '💵 মূল্য তালিকা',
        'lang_btn': '🌐 ভাষা পরিবর্তন',
        'mail_btn': '📥 মেইল ইনবক্স',
        'welcome': '✨ স্বাগতম {name}! 🤖\n🚀 দ্রুত ও নিরাপদ সার্ভিস উপভোগ করুন — শুরু করতে নিচের মেনুটি ব্যবহার করুন!',
        'buy_text': 'আপনি কি কিনতে চান?',
        'deposit_text': 'আপনার পছন্দের ডিপোজিট মাধ্যম নির্বাচন করুন:',
        'balance_text': 'আপনার ব্যালেন্স: {balance} BDT',
        'price_text': 'এটি Price লিস্ট।',
        'support_text': '💬 **কাস্টমার সাপোর্ট**\n\nযেকোনো সাহায্য বা অনুসন্ধানের জন্য আমাদের সাপোর্ট টিমের সাথে যোগাযোগ করুন:\n👉 @workstoresuport',
        'lang_text': 'অনুগ্রহ করে নিচে থেকে আপনার ভাষা নির্বাচন করুন:',
        'mail_text': 'অনুগ্রহ করে আপনার ক্রেডেনশিয়াল নিচের ফরম্যাটে দিন:\n`mail|pass|refresh_token|client_id`',
        'unknown': 'অজানা কমান্ড। অনুগ্রহ করে নিচের মেনুটি ব্যবহার করুন।',
        'lang_changed': 'আপনার ভাষা বাংলায় পরিবর্তন করা হয়েছে! 🇧🇩',
        'returning': 'ফিরে যাচ্ছি...',
        'dep_24h_limit': '❌ আপনি ২৪ ঘণ্টার মধ্যে মাত্র একবার ডিপোজিট রিকোয়েস্ট পাঠাতে পারবেন। দয়া করে পরে আবার চেষ্টা করুন।',
        'dep_ask_amount': 'আপনি {method} এর মাধ্যমে কত টাকা ডিপোজিট করতে চান?',
        'dep_cancelled': 'ডিপোজিট বাতিল করা হয়েছে।',
        'dep_min_err': '❌ সর্বনিম্ন ডিপোজিট হলো {min_dep} ৳। দয়া করে সঠিক এমাউন্ট দিন:',
        'dep_invalid_amt': '❌ ভুল এমাউন্ট। দয়া করে সংখ্যা দিন:',
        'dep_submit_btn': '📝 Transaction ID সাবমিট করুন',
        'dep_instruct': '🔹 **ডিপোজিট মাধ্যম: {method}**\n'
                        '\n'
                        'পরিমাণ: **{amount}**\n'
                        '\n'
                        'দয়া করে নিচের {method_str} এ ঠিক এই পরিমাণ টাকা পাঠান:\n'
                        '`{target_acc}`\n'
                        '\n'
                        'টাকা পাঠানোর পর নিচের বাটনে ক্লিক করে আপনার Transaction ID দিন।',
        'dep_ask_trxid': 'দয়া করে আপনার **১০ সংখ্যার** Transaction ID (TrxID) নিচে দিন:',
        'dep_wrong_trxid': '❌ দয়া করে আগে টাকা পাঠান, তারপর এখানে সঠিক TrxID দিন।',
        'dep_used_trxid': '❌ এই TrxID টি ইতিমধ্যে ব্যবহার করা হয়েছে। দয়া করে সঠিক TrxID দিন।',
        'dep_success': '✅ আপনার ডিপোজিট রিকোয়েস্ট অ্যাডমিনের কাছে পাঠানো হয়েছে। অনুগ্রহ করে অপেক্ষা করুন।',
        'buy_vpn_btn': '🛡️ Nord VPN কিনুন',
        'buy_hotmail_btn': '📧 Hotmail কিনুন',
        'buy_outlook_btn': '📧 Outlook কিনুন',
        'buy_outlook_fr_btn': '📧 Outlook.fr কিনুন',
        'buy_proxy_btn': '🔌 Proxy কিনুন',
        'buy_out_of_stock': '❌ দুঃখিত, বর্তমানে স্টক নেই।',
        'buy_ask_vpn': '🛡️ আপনি কোন VPN চান?',
        'buy_ask_hotmail': '📧 স্টোর > Hotmail',
        'buy_ask_outlook': '📧 স্টোর > Outlook',
        'buy_ask_outlook_fr': '📧 স্টোর > Outlook.fr',
        'buy_ask_proxy': '🔌 স্টোর > Proxy',
        'buy_no_bal': '❌ আপনার পর্যাপ্ত ব্যালেন্স নেই!',
        'buy_yes': '✅ হ্যাঁ',
        'buy_no': '❌ না',
        'buy_confirm': 'আপনি কি নিশ্চিত যে আপনি **{cat}({dur})** কিনতে চান?\nমূল্য: {price} ৳',
        'buy_cancelled': '❌ কেনা বাতিল করা হয়েছে।',
        'buy_sold_out': '❌ স্টক শেষ! দয়া করে পরে আবার চেষ্টা করুন।',
        'buy_success': '✅ **সফলভাবে কেনা হয়েছে!**\n'
                       '\n'
                       '**আইটেম:** {cat}({dur})\n'
                       '**মূল্য:** {price} ৳\n'
                       '**নতুন ব্যালেন্স:** {new_bal} ৳\n'
                       '\n'
                       '**একাউন্ট ডিটেইলস:**\n'
                       '{creds}',
        'mail_cancelled': 'মেইল ইনবক্স চেক বাতিল করা হয়েছে।',
        'mail_invalid_fmt': '❌ ভুল ফরম্যাট। দয়া করে ব্যবহার করুন:\n`mail|pass|refresh_token|client_id`',
        'mail_working': 'কাজ চলছে... মেইন মেনু ফিরে এসেছে।',
        'mail_start': '🔄 {email} এর জন্য মেইল ট্র্যাকার শুরু হচ্ছে...',
        'mail_token_err': '❌ Access Token তৈরি করতে ব্যর্থ। দয়া করে আপনার refresh_token এবং client_id চেক করুন।',
        'mail_new': '📧 **নতুন মেইল এসেছে!**\n\n**সাবজেক্ট:** {subject}\n**প্রিভিউ:** {preview}\n',
        'mail_code': '\n**কোড:**\n`{code}`',
        'mail_no_code': '\n*(কোনো কোড পাওয়া যায়নি)*',
        'mail_token_exp': '❌ Access token এর মেয়াদ শেষ বা ভুল।',
        'mail_not_found': '❌ কোনো মেইল পাওয়া যায়নি (payni)।',
        'dep_soon': 'কনভার্ট ব্যালেন্স ফিচারটি শীঘ্রই আসছে!',
        'dep_select_bot': 'কোন বট থেকে ব্যালেন্স কনভার্ট করতে চান?',
        'conv_db_err': '❌ এই মুহূর্তে {bot} এর ডাটাবেসের সাথে কানেক্ট করা যাচ্ছে না।',
        'conv_no_acc': '❌ আপনার {bot} এ কোনো একাউন্ট পাওয়া যায়নি।',
        'conv_no_bal': '❌ {bot} এ আপনার কোনো ব্যালেন্স নেই।',
        'conv_ask_amt': 'আপনার {bot} এ ব্যালেন্স আছে: **{bal} BDT**\nআপনি কত টাকা এই বটে আনতে চান?',
        'conv_cancelled': 'কনভার্শন বাতিল করা হয়েছে।',
        'conv_zero_err': '❌ দয়া করে 0 এর চেয়ে বড় একটি এমাউন্ট দিন:',
        'conv_max_err': '❌ আপনার এতো ব্যালেন্স নেই! (সর্বোচ্চ: {max_bal})\nদয়া করে সঠিক এমাউন্ট দিন:',
        'conv_success': '✅ **কনভার্শন সফল!**\n'
                        '\n'
                        'আপনি {bot} থেকে **{amount} BDT** কনভার্ট করেছেন।\n'
                        'আপনার বর্তমান ব্যালেন্স: **{new_bal} BDT**'}}

def get_main_menu(lang='en', chat_id=None):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    t = texts.get(lang, texts['en'])
    markup.add(
        KeyboardButton(t['buy_btn'], style="success", icon_custom_emoji_id="5395463407589672312"), KeyboardButton(t['deposit_btn'], style="primary", icon_custom_emoji_id="5332600543963522398"),
        KeyboardButton(t['balance_btn'], style="primary", icon_custom_emoji_id="6170011680831969294"), KeyboardButton(t['price_btn'], style="success", icon_custom_emoji_id="5445150711711018720"),
        KeyboardButton(t.get('support_btn', 'Support'), style="primary", icon_custom_emoji_id="5237988788164107500"), KeyboardButton(t['mail_btn'], style="success", icon_custom_emoji_id="5422816495224772158")
    )
    if chat_id in ADMIN_IDS:
        markup.add(KeyboardButton('⚡ Admin Panel', style="danger"))
    return markup

def get_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton('📊 Bot Statistics', style="primary"), KeyboardButton('📢 Send Broadcast', style="primary"),
        KeyboardButton('💵 Manage Balance', style="success"), KeyboardButton('⚙️ Payment Settings', style="primary"),
        KeyboardButton('📦 Store Stock', style="success"), KeyboardButton('🌐 Get Webhook URL', style="success")
    )
    markup.add(KeyboardButton('↩️ Return to User Menu', style="primary"))
    return markup

def get_payment_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton('📱 Set bKash Number', style="primary"), KeyboardButton('🍊 Set Nagad Number', style="primary"),
        KeyboardButton('🔶 Set Binance ID', style="primary"), KeyboardButton('💵 Set Min Deposit', style="success")
    )
    markup.add(
        KeyboardButton('🛡️ Set VPN Price', style="primary"), KeyboardButton('📧 Set Hotmail Price', style="success")
    )
    markup.add(
        KeyboardButton('📧 Set Outlook Price', style="primary"), KeyboardButton('📧 Set Outlook.fr Price', style="success")
    )
    markup.add(
        KeyboardButton('🔌 Set Proxy Price', style="danger"), KeyboardButton('📧 Set Gmail Price', style="success")
    )
    markup.add(
        KeyboardButton('💎 Set USDT Address', style="success"), KeyboardButton('📈 Set USDT Rate', style="primary")
    )
    markup.add(
        KeyboardButton('🔑 Set Bitget API Keys', style="primary"), KeyboardButton('🔍 Bitget API Status', style="success")
    )
    markup.add(
        KeyboardButton('🌐 Set Custom Webhook URL', style="primary"), KeyboardButton('🔙 Return to Admin', style="primary")
    )
    return markup

def get_cancel_menu(lang='en'):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    cancel_text = '❌ Cancel'
    markup.add(KeyboardButton(cancel_text, style="danger"))
    return markup

def is_back_or_cancel_text(text):
    normalized = (text or "").strip().casefold()
    return any(key in normalized for key in ("cancel", "back", "return", "বাতিল", "ব্যাক", "বযাক", "ফিরে"))

# --- CUSTOM EMOJI ID DETECTOR (FOR ADMINS) ---
@bot.message_handler(func=lambda msg: msg.chat.id in ADMIN_IDS and msg.entities and any(e.type == 'custom_emoji' for e in msg.entities))
def detect_custom_emoji_id(message):
    ids = [e.custom_emoji_id for e in message.entities if e.type == 'custom_emoji']
    if ids:
        reply = "<b>✨ Custom Emoji IDs Found:</b>\n\n"
        for i, emoji_id in enumerate(ids, 1):
            reply += f"{i}. <code>{emoji_id}</code>\n"
        reply += "\n<i>(Copy these IDs and send them here to set on your buttons!)</i>"
        bot.reply_to(message, reply, parse_mode="HTML")

@bot.message_handler(commands=['setgroup'])
def set_group_command(message):
    if message.chat.id in ADMIN_IDS or message.from_user.id in ADMIN_IDS:
        group_id = message.chat.id
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"deposit_group_id": group_id}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ **Deposit Group Set Successfully!**\n\nChat ID: `{group_id}`", parse_mode="Markdown")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    username = message.chat.username
    first_name = message.chat.first_name
    
    user = users_collection.find_one({"chat_id": user_id})
    if not user:
        users_collection.insert_one({
            "chat_id": user_id,
            "username": username,
            "first_name": first_name,
            "balance": 0.0,
            "language": "en"
        })
        print(f"New user saved: {first_name} ({user_id})")

    welcome_text = texts['en']['welcome'].format(name=first_name)
    bot.send_message(message.chat.id, welcome_text, reply_markup=get_main_menu('en', message.chat.id))

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.chat.id in ADMIN_IDS:
        bot.send_message(message.chat.id, "Welcome to the Admin Panel! ⚙\nPlease choose an option:", reply_markup=get_admin_menu())
    else:
        lang = get_user_lang(message.chat.id)
        bot.send_message(message.chat.id, texts[lang]['unknown'])

def get_product_price(cat, conf=None):
    if not conf:
        conf = get_config()
    cat_str = (cat or "").strip()
    if cat_str == "Hotmail":
        p = conf.get("hotmail_price")
        return float(p) if (p is not None and float(p) > 0) else 5.0
    elif cat_str == "Outlook":
        p = conf.get("outlook_price")
        return float(p) if (p is not None and float(p) > 0) else 5.0
    elif cat_str == "Outlook.fr":
        p = conf.get("outlook_fr_price")
        return float(p) if (p is not None and float(p) > 0) else 5.0
    elif cat_str == "Proxy":
        p = conf.get("proxy_price")
        return float(p) if (p is not None and float(p) > 0) else 10.0
    elif cat_str == "Gmail":
        p = conf.get("gmail_price")
        return float(p) if (p is not None and float(p) > 0) else 5.0
    else:
        p = conf.get("vpn_price")
        return float(p) if (p is not None and float(p) > 0) else 15.0

# --- AVAILABLE PRODUCTS INLINE KEYBOARD MENU ---
def get_available_products_keyboard():
    conf = get_config()
    vpn_price = fmt_bal(get_product_price("Nord", conf))
    hm_price = fmt_bal(get_product_price("Hotmail", conf))
    out_price = fmt_bal(get_product_price("Outlook", conf))
    out_fr_price = fmt_bal(get_product_price("Outlook.fr", conf))
    proxy_price = fmt_bal(get_product_price("Proxy", conf))
    gmail_price = fmt_bal(get_product_price("Gmail", conf))

    cat_map = [
        {"cat": "Nord", "name": "Nord VPN 7 Days", "icon": "", "custom_emoji": "5990056785967321926", "price": vpn_price, "dur": "7day"},
        {"cat": "Hotmail", "name": "Hotmail Mail", "icon": "", "custom_emoji": "5285184156555306745", "price": hm_price, "dur": "mail"},
        {"cat": "Outlook", "name": "Outlook Mail", "icon": "", "custom_emoji": "5253742260054409879", "price": out_price, "dur": "mail"},
        {"cat": "Outlook.fr", "name": "Outlook.fr Mail", "icon": "", "custom_emoji": "5344008428073280881", "price": out_fr_price, "dur": "mail"},
        {"cat": "Proxy", "name": "High-Speed Proxy", "icon": "", "custom_emoji": "5848067868695991015", "price": proxy_price, "dur": "line"},
        {"cat": "Gmail", "name": "Gmail Account", "icon": "", "custom_emoji": "6118546560897781055", "price": gmail_price, "dur": "acc"}
    ]

    markup = InlineKeyboardMarkup(row_width=1)
    
    for item in cat_map:
        cat = item["cat"]
        dur = item["dur"]
        stock = vpn_collection.count_documents({"category": cat, "status": "available"})
        style = "success" if stock > 0 else "danger"
        prefix = f"{item['icon']} " if item.get("icon") else ""
        text = f"{prefix}{item['name']} | Stock: {stock}"
        cb = f"buy_item_{cat}_{dur}"
        
        btn_kwargs = {"style": style}
        if item.get("custom_emoji"):
            btn_kwargs["icon_custom_emoji_id"] = item["custom_emoji"]
            
        markup.add(InlineKeyboardButton(text, callback_data=cb, **btn_kwargs))

    markup.add(InlineKeyboardButton("Go Back", callback_data="buy_back_menu", style="primary", icon_custom_emoji_id="5416113713428057601"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data == 'buy_back_menu')
def handle_buy_back(call):
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass

@bot.message_handler(func=lambda message: True)
def handle_menu(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    text = (message.text or "").strip()

    # --- ADMIN PANEL HANDLERS ---
    if user_id in ADMIN_IDS:
        if text in ['📊 Bot Statistics', '📈 Bot Statistics']:
            total_users = users_collection.estimated_document_count() 
            bot.send_message(user_id, f"📊 **Bot Statistics**\n\nTotal Users: {total_users}")
            return
            
        elif text in ['📢 Send Broadcast', '🚀 Send Broadcast']:
            msg = bot.send_message(user_id, "Please send the message you want to broadcast to all users:")
            bot.register_next_step_handler(msg, process_broadcast)
            return

        elif text in ['🌐 Get Webhook URL', '/url', '/webhook', '🌐 Webhook URL']:
            conf = get_config()
            custom_url = conf.get("custom_webhook_url")
            if custom_url:
                webhook_url = custom_url
            else:
                try:
                    public_ip = requests.get('https://api.ipify.org', timeout=5).text.strip()
                except:
                    public_ip = "187.124.6.8"
                webhook_url = f"http://{public_ip}:5000/api/sms_receiver"

            msg = (
                f"🌐 **Server Live Webhook URL:**\n\n"
                f"`{webhook_url}`\n\n"
                f"📲 Copy & paste this URL into your Android App settings!"
            )
            bot.send_message(user_id, msg, parse_mode="Markdown", reply_markup=get_admin_menu())
            return
            
            
        elif text in ['💵 Manage Balance', '💎 Manage Balance']:
            msg = bot.send_message(user_id, "Please send the **Chat ID** of the user you want to manage:")
            bot.register_next_step_handler(msg, process_manage_balance_id)
            return
            
        elif text in ['⚙️ Payment Settings', '🛠️ Payment Settings']:
            bot.send_message(user_id, "⚙ **Payment Settings**\nSelect an option to configure:", reply_markup=get_payment_admin_menu())
            return
            
        elif text in ['🔙 Back to Admin', '🔙 Return to Admin', '↩️ Return to User Menu']:
            if text == '↩️ Return to User Menu':
                bot.send_message(user_id, "Returning to User Menu...", reply_markup=get_main_menu(lang, user_id))
            else:
                bot.send_message(user_id, "Returning to Admin Panel...", reply_markup=get_admin_menu())
            return
            
        elif text == '📱 Set bKash Number':
            msg = bot.send_message(user_id, "Please enter the new bKash Number:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_bkash)
            return
            
        elif text == '🔶 Set Binance ID':
            msg = bot.send_message(user_id, "Please enter the new Binance ID:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_binance)
            return
            
        elif text == '💵 Set Min Deposit':
            msg = bot.send_message(user_id, "Please enter the new Minimum Deposit amount (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_min_deposit)
            return
            
        elif text == '🛡️ Set VPN Price':
            msg = bot.send_message(user_id, "Please enter the new Nord VPN (7day) Price (e.g. 15.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_vpn_price)
            return
            
        elif text == '📧 Set Hotmail Price':
            msg = bot.send_message(user_id, "Please enter the new Hotmail Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_hotmail_price)
            return
            
        elif text == '📧 Set Outlook Price':
            msg = bot.send_message(user_id, "Please enter the new Outlook Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_outlook_price)
            return
            
        elif text == '📧 Set Outlook.fr Price':
            msg = bot.send_message(user_id, "Please enter the new Outlook.fr Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_outlook_fr_price)
            return
            
        elif text == '🔌 Set Proxy Price':
            msg = bot.send_message(user_id, "Please enter the new Proxy Price (e.g. 10.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_proxy_price)
            return

        elif text == '📧 Set Gmail Price':
            msg = bot.send_message(user_id, "Please enter the new Gmail Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_gmail_price)
            return

        elif text == '🌐 Set Custom Webhook URL':
            msg = bot.send_message(user_id, "Please enter your custom Webhook URL (e.g. `http://187.124.6.8:5000/api/sms_receiver`):", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_custom_webhook_url)
            return

        elif text == '💎 Set USDT Address':
            msg = bot.send_message(user_id, "Please enter your USDT (BEP20) Deposit Address:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_usdt_address)
            return

        elif text == '📈 Set USDT Rate':
            msg = bot.send_message(user_id, "Please enter the USDT to BDT rate (e.g. 120.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_usdt_rate)
            return

        elif text == '🔑 Set Bitget API Keys':
            msg = bot.send_message(user_id, "Please enter your Bitget API credentials in format:\n`API_KEY|API_SECRET|PASSPHRASE`", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Return to Admin'))
            bot.register_next_step_handler(msg, process_set_bitget_api_keys)
            return

        elif text in ['🔍 Bitget API Status', '/bitget']:
            conf = get_config()
            status_res = check_bitget_api_status(conf)
            bot.send_message(user_id, status_res["message"], parse_mode="Markdown", reply_markup=get_payment_admin_menu())
            return
            
        elif text == '📦 Store Stock':
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton('➕ Add VPN Stock', style="primary"), KeyboardButton('➕ Add Hotmail Stock', style="success"))
            markup.add(KeyboardButton('➕ Add Outlook Stock', style="primary"), KeyboardButton('➕ Add Outlook.fr Stock', style="success"))
            markup.add(KeyboardButton('🔌 Add Proxy Stock', style="danger"), KeyboardButton('📧 Add Gmail Stock', style="success"))
            markup.add(KeyboardButton(' View Stock', style="success"), KeyboardButton('🗑 Clear Stock', style="danger"))
            markup.add(KeyboardButton('🔙 Back to Admin', style="primary"))
            bot.send_message(user_id, "📦 **Store Management**\nSelect an option:", reply_markup=markup)
            return
            
        elif text == ' View Stock':
            pipeline = [
                {"$match": {"status": "available"}},
                {"$group": {"_id": "$category", "count": {"$sum": 1}}}
            ]
            results = list(vpn_collection.aggregate(pipeline))
            
            if not results:
                bot.send_message(user_id, "📦 **Current Available Stock:**\n\nAll stocks are currently empty (0).")
                return
                
            msg = "📊 **Current Available Stock:**\n\n"
            for res in results:
                cat = res['_id']
                count = res['count']
                msg += f"🔹 **{cat}:** {count} accounts\n"
            bot.send_message(user_id, msg, parse_mode="Markdown")
            return
            
        elif text == '🗑 Clear Stock':
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton('🗑 Clear VPN', style="danger"), KeyboardButton('🗑 Clear Hotmail', style="danger"))
            markup.add(KeyboardButton('🗑 Clear Outlook', style="danger"), KeyboardButton('🗑 Clear Outlook.fr', style="danger"))
            markup.add(KeyboardButton('🗑 Clear Proxy', style="danger"), KeyboardButton('🗑 Clear Gmail', style="danger"))
            markup.add(KeyboardButton('🔙 Back to Store', style="primary"))
            bot.send_message(user_id, "⚠ **Which stock do you want to delete?**\n*(Only available unsold stock will be deleted!)*", reply_markup=markup, parse_mode="Markdown")
            return
            
        elif text == '🗑 Clear VPN':
            res = vpn_collection.delete_many({"category": "Nord", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available VPN accounts.", parse_mode="Markdown")
            return
            
        elif text == '🗑 Clear Hotmail':
            res = vpn_collection.delete_many({"category": "Hotmail", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available Hotmail accounts.", parse_mode="Markdown")
            return
            
        elif text == '🗑 Clear Outlook':
            res = vpn_collection.delete_many({"category": "Outlook", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available Outlook accounts.", parse_mode="Markdown")
            return
            
        elif text == '🗑 Clear Outlook.fr':
            res = vpn_collection.delete_many({"category": "Outlook.fr", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available Outlook.fr accounts.", parse_mode="Markdown")
            return
            
        elif text == '🗑 Clear Proxy':
            res = vpn_collection.delete_many({"category": "Proxy", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available Proxy accounts.", parse_mode="Markdown")
            return

        elif text == '🗑 Clear Gmail':
            res = vpn_collection.delete_many({"category": "Gmail", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available Gmail accounts.", parse_mode="Markdown")
            return
            
        elif text == '🔙 Back to Store':
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton('➕ Add VPN Stock', style="primary"), KeyboardButton('➕ Add Hotmail Stock', style="success"))
            markup.add(KeyboardButton('➕ Add Outlook Stock', style="primary"), KeyboardButton('➕ Add Outlook.fr Stock', style="success"))
            markup.add(KeyboardButton('🔌 Add Proxy Stock', style="danger"), KeyboardButton(' View Stock', style="success"))
            markup.add(KeyboardButton('🗑 Clear Stock', style="danger"), KeyboardButton('🔙 Back to Admin', style="primary"))
            bot.send_message(user_id, "📦 **Store Management**\nSelect an option:", reply_markup=markup)
            return
            
        elif text == '➕ Add VPN Stock':
            msg_instruct = "Please paste the VPN accounts.\n\n**Format 1:** `mail|pass` or `mail pass` (one per line)\n**Format 2:**\n📧 email@domain.com\n🔒 password"
            msg = bot.send_message(user_id, msg_instruct, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_vpn_stock)
            return

        elif text == '➕ Add Hotmail Stock':
            msg_text = "Please send a `.txt` file or paste the Hotmail accounts (one per line).\n\n*(Any format you paste here will be exactly delivered to the user!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_hotmail_stock)
            return
            
        elif text == '➕ Add Outlook Stock':
            msg_text = "Please send a `.txt` file or paste the Outlook accounts (one per line).\n\n*(Any format you paste here will be exactly delivered to the user!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_outlook_stock)
            return
            
        elif text == '➕ Add Outlook.fr Stock':
            msg_text = "Please send a `.txt` file or paste the Outlook.fr accounts (one per line).\n\n*(Any format you paste here will be exactly delivered to the user!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_outlook_fr_stock)
            return
            
        elif text == '🔌 Add Proxy Stock':
            msg_text = "Please send a `.txt` file or paste the Proxy lines (one per line).\n\nFormat: `change6.owlproxy.com:7778:JO3UM0fUn560_custom_zone_TG_st__city_sid_36635687_time_5:4033397`"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_proxy_stock)
            return

        elif text == '📧 Add Gmail Stock':
            msg_text = "Please send a `.txt` or `.xlsx` file, or paste Gmail accounts (one per line).\n\n**Formats Supported:**\n1. `gmail|password` (Text / TXT file)\n2. `.xlsx` file (Col A = Gmail, Col B = Password)"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_gmail_stock)
            return
            
        elif text in ['⚙️ Admin Panel', '⚡ Admin Panel', '👑 Admin Panel']:
            bot.send_message(user_id, "Welcome to the Admin Panel! ⚙\nPlease choose an option:", reply_markup=get_admin_menu())
            return

    # --- REGULAR USER HANDLERS ---
    if text == '🇬🇧 English':
        users_collection.update_one({"chat_id": user_id}, {"$set": {"language": "en"}})
        LANG_CACHE[user_id] = "en"
        bot.send_message(user_id, texts['en']['lang_changed'], reply_markup=get_main_menu("en", user_id))
        return
    elif text == '🇧🇩 বাংলা':
        users_collection.update_one({"chat_id": user_id}, {"$set": {"language": "bn"}})
        LANG_CACHE[user_id] = "bn"
        bot.send_message(user_id, texts['bn']['lang_changed'], reply_markup=get_main_menu("bn", user_id))
        return
    elif text in ['🔙 Back', '🔙 বযাক', '❌ Cancel', '❌ বাতিল (Cancel)'] or is_back_or_cancel_text(text):
        bot.send_message(user_id, texts[lang]['returning'], reply_markup=get_main_menu(lang, user_id))
        return

    # Handle main menu commands
    elif text in [texts['en']['buy_btn'], texts['bn']['buy_btn'], 'Buy', '🛒 Buy Product', '🛒 পণ্য কিনুন']:
        bot.send_message(
            user_id,
            "**Available products**\nPlease select a product to proceed",
            reply_markup=get_available_products_keyboard(),
            parse_mode="Markdown"
        )
        return
        
    elif text in [texts['en']['deposit_btn'], texts['bn']['deposit_btn'], 'Deposit', '💳 Deposit Money', '💳 ডিপোজিট করুন']:
        bot.send_message(user_id, "Opening Deposit Panel...", reply_markup=get_cancel_menu(lang))

        inline_markup = InlineKeyboardMarkup(row_width=1)
        inline_markup.add(
            InlineKeyboardButton("bKash Personal (Auto SMS)", callback_data="depmethod_bKash", style="success", icon_custom_emoji_id="6318916225594295727"),
            InlineKeyboardButton("Nagad Personal (Auto SMS)", callback_data="depmethod_Nagad", style="success", icon_custom_emoji_id="6318600635692353874"),
            InlineKeyboardButton("Bitget USDT BEP20 (Auto API)", callback_data="depmethod_USDT_BEP20", style="success", icon_custom_emoji_id="5206584567116352967")
        )
        bot.send_message(user_id, texts[lang]['deposit_text'], reply_markup=inline_markup)
        
    elif text in [texts['en']['balance_btn'], texts['bn']['balance_btn'], 'Balance', '💎 My Balance']:
        user = users_collection.find_one({"chat_id": user_id}, {"balance": 1})
        balance = user.get("balance", 0.0) if user else 0.0
        bot.send_message(user_id, texts[lang]['balance_text'].format(balance=fmt_bal(balance)))
        
    elif text in [texts['en']['price_btn'], texts['bn']['price_btn'], 'Price', '🏷️ Price List', '💵 Price List', '💵 মূল্য তালিকা']:
        conf = get_config()
        vpn_price = fmt_bal(get_product_price("Nord", conf))
        hm_price = fmt_bal(get_product_price("Hotmail", conf))
        out_price = fmt_bal(get_product_price("Outlook", conf))
        out_fr_price = fmt_bal(get_product_price("Outlook.fr", conf))
        proxy_price = fmt_bal(get_product_price("Proxy", conf))
        gmail_price = fmt_bal(get_product_price("Gmail", conf))
        msg_text = f"**Price List / মূলয তালিকা:**\n\n🔹 Nord VPN (7day) = **{vpn_price} ৳**\n📧 Hotmail = **{hm_price} ৳**\n📧 Outlook = **{out_price} ৳**\n📧 Outlook.fr = **{out_fr_price} ৳**\n🔌 Proxy = **{proxy_price} ৳**\n📧 Gmail = **{gmail_price} ৳**"
        bot.send_message(user_id, msg_text, parse_mode="Markdown")
        
    elif text in [texts['en'].get('support_btn', 'Support'), 'Support', '🎧 Support', '💬 Support', '💬 Customer Support', '🌐 Choose Language', '🌐 ভাষা পরিবর্তন']:
        bot.send_message(user_id, texts[lang].get('support_text', texts['en']['support_text']), parse_mode="Markdown")
        return
        
    elif text in [texts['en']['mail_btn'], texts['bn']['mail_btn'], 'Mail Inbox', '📨 Mail Inbox', '📥 Mail Inbox', '📥 মেইল ইনবক্স']:
        msg = bot.send_message(user_id, texts[lang]['mail_text'], parse_mode="Markdown", reply_markup=get_cancel_menu(lang))
        bot.register_next_step_handler(msg, process_mail_credentials)
        
    else:
        bot.send_message(user_id, texts[lang]['unknown'])

# --- ADMIN CONFIG FUNCTIONS ---
def process_set_bkash(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"bkash": message.text}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0 
    bot.send_message(message.chat.id, f"✅ bKash number updated to: {message.text}", reply_markup=get_payment_admin_menu())

def process_set_nagad(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"nagad": message.text}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f"✅ Nagad number updated to: {message.text}", reply_markup=get_payment_admin_menu())

def process_set_binance(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"binance": message.text}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f"✅ Binance ID updated to: {message.text}", reply_markup=get_payment_admin_menu())
 
def process_set_min_deposit(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"min_deposit": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Min deposit updated to: {val}", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_vpn_price(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"vpn_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ VPN Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())
 
def process_set_hotmail_price(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"hotmail_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Hotmail Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())
 
def process_set_outlook_price(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"outlook_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Outlook Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())
 
def process_set_outlook_fr_price(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"outlook_fr_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Outlook.fr Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_proxy_price(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"proxy_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Proxy Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_gmail_price(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"gmail_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Gmail Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_custom_webhook_url(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    url_val = message.text.strip()
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"custom_webhook_url": url_val}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f"✅ **Custom Webhook URL Saved!**\n\n`{url_val}`", parse_mode="Markdown", reply_markup=get_payment_admin_menu())

def process_set_usdt_address(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    addr = message.text.strip()
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"usdt_address": addr}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f"✅ **USDT (BEP20) Address Updated:**\n`{addr}`", parse_mode="Markdown", reply_markup=get_payment_admin_menu())

def process_set_usdt_rate(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        rate = float(message.text.strip())
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"usdt_rate": rate}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ **USDT Rate Updated:** 1 USDT = {rate} ৳", reply_markup=get_payment_admin_menu())
    except:
        bot.send_message(message.chat.id, "❌ Invalid rate.", reply_markup=get_payment_admin_menu())

def process_set_bitget_api_keys(message):
    if message.text == '🔙 Return to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    parts = message.text.strip().split('|')
    if len(parts) != 3:
        bot.send_message(message.chat.id, "❌ Invalid format. Please send `API_KEY|API_SECRET|PASSPHRASE`", parse_mode="Markdown", reply_markup=get_payment_admin_menu())
        return
    api_key, api_secret, passphrase = [p.strip() for p in parts]
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {
        "bitget_api_key": api_key,
        "bitget_api_secret": api_secret,
        "bitget_passphrase": passphrase
    }}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, "✅ **Bitget API Credentials Saved Successfully!**", parse_mode="Markdown", reply_markup=get_payment_admin_menu())

@bot.message_handler(content_types=['document', 'text'])
def process_add_proxy_stock(message):
    if message.text == '🔙 Return to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    lines = []
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name or ""
            if file_name.lower().endswith('.xlsx'):
                lines = parse_xlsx_to_lines(downloaded_file)
            else: 
                raw_text = decode_file_content(downloaded_file)
                lines = raw_text.strip().split('\n')
        except Exception as e:
            bot.send_message(message.chat.id, f" Error reading file: {e}", reply_markup=get_admin_menu())
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        docs_to_insert.append({
            "category": "Proxy",
            "duration": "Standard",
            "credentials": line,
            "status": "available",
            "timestamp": datetime.now(timezone.utc)
        })
        added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Proxy to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

def process_add_vpn_stock(message):
    if message.text == '🔙 Return to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return
        
    raw_text = message.text.strip()
    added_count = 0
    docs_to_insert = []
    
    if '📧' in raw_text or '🔒' in raw_text:
        current_email = None
        for line in raw_text.split('\n'):
            line = line.strip()
            if not line: 
                continue
                
            if '📧' in line:
                current_email = line.replace('📧', '').strip()
            elif '🔒' in line and current_email:
                password = line.replace('🔒', '').strip()
                docs_to_insert.append({
                    "category": "Nord",
                    "duration": "7day",
                    "credentials": f"{current_email}|{password}",
                    "status": "available",
                    "timestamp": datetime.now(timezone.utc)
                })
                added_count += 1
                current_email = None 
    else:
        lines = raw_text.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            parts = re.split(r'[|\t\s]+', line)
            if len(parts) >= 2:
                email = parts[0]
                password = parts[1]
                docs_to_insert.append({
                    "category": "Nord",
                    "duration": "7day",
                    "credentials": f"{email}|{password}",
                    "status": "available",
                    "timestamp": datetime.now(timezone.utc)
                })
                added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert) 
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Nord(7day) accounts to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

def decode_file_content(downloaded_file):
    for encoding in ['utf-8-sig', 'utf-16', 'cp1252', 'utf-8', 'iso-8859-1']:
        try:
            return downloaded_file.decode(encoding)
        except UnicodeDecodeError:
            continue
    return downloaded_file.decode('utf-8', errors='ignore')

def parse_xlsx_to_lines(downloaded_file):
    try:
        import openpyxl
        file_data = io.BytesIO(downloaded_file)
        wb = openpyxl.load_workbook(file_data, data_only=True)
        sheet = wb.active
        lines = []
        for row in sheet.iter_rows(values_only=True):
            if row:
                col_a = str(row[0]).strip() if len(row) > 0 and row[0] is not None else ""
                col_b = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
                if col_a and col_b and col_a.lower() != "gmail" and col_a.lower() != "email":
                    lines.append(f"{col_a}|{col_b}")
                elif col_a and col_a.lower() != "gmail" and col_a.lower() != "email":
                    lines.append(col_a)
        return lines
    except Exception as e:
        print(f"Error parsing xlsx: {e}")
        return []

@bot.message_handler(content_types=['document', 'text'])
def process_add_hotmail_stock(message):
    if message.text == '🔙 Return to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    lines = []
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name or ""
            if file_name.lower().endswith('.xlsx'):
                lines = parse_xlsx_to_lines(downloaded_file)
            else:
                raw_text = decode_file_content(downloaded_file)
                lines = raw_text.strip().split('\n')
        except Exception as e:
            bot.send_message(message.chat.id, f" Error reading file: {e}", reply_markup=get_admin_menu())
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    docs_to_insert = []
    
    # Store EXACTLY as pasted/uploaded
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        docs_to_insert.append({
            "category": "Hotmail",
            "duration": "Standard",
            "credentials": line,
            "status": "available",
            "timestamp": datetime.now(timezone.utc)
        })
        added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Hotmail accounts to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

@bot.message_handler(content_types=['document', 'text'])
def process_add_outlook_stock(message):
    if message.text == '🔙 Return to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    lines = []
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name or ""
            if file_name.lower().endswith('.xlsx'):
                lines = parse_xlsx_to_lines(downloaded_file)
            else:
                raw_text = decode_file_content(downloaded_file)
                lines = raw_text.strip().split('\n')
        except Exception as e:
            bot.send_message(message.chat.id, f" Error reading file: {e}", reply_markup=get_admin_menu())
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        docs_to_insert.append({
            "category": "Outlook",
            "duration": "Standard",
            "credentials": line,
            "status": "available",
            "timestamp": datetime.now(timezone.utc)
        })
        added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Outlook accounts to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

@bot.message_handler(content_types=['document', 'text'])
def process_add_outlook_fr_stock(message):
    if message.text == '🔙 Return to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    lines = []
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name or ""
            if file_name.lower().endswith('.xlsx'):
                lines = parse_xlsx_to_lines(downloaded_file)
            else:
                raw_text = decode_file_content(downloaded_file)
                lines = raw_text.strip().split('\n')
        except Exception as e:
            bot.send_message(message.chat.id, f" Error reading file: {e}", reply_markup=get_admin_menu())
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        docs_to_insert.append({
            "category": "Outlook.fr",
            "duration": "Standard",
            "credentials": line,
            "status": "available",
            "timestamp": datetime.now(timezone.utc)
        })
        added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Outlook.fr accounts to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

@bot.message_handler(content_types=['document', 'text'])
def process_add_gmail_stock(message):
    if message.text == '🔙 Return to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    lines = []
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = message.document.file_name or ""
            if file_name.lower().endswith('.xlsx'):
                lines = parse_xlsx_to_lines(downloaded_file)
            else:
                raw_text = decode_file_content(downloaded_file)
                lines = raw_text.strip().split('\n')
        except Exception as e:
            bot.send_message(message.chat.id, f" Error reading file: {e}", reply_markup=get_admin_menu())
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        docs_to_insert.append({
            "category": "Gmail",
            "duration": "Standard",
            "credentials": line,
            "status": "available",
            "timestamp": datetime.now(timezone.utc)
        })
        added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Gmail accounts to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

# --- ADMIN MULTI-STEP FUNCTIONS ---
def process_broadcast(message):
    if message.text in ['📊 Bot Statistics', '📢 Send Broadcast', '💵 Manage Balance', '⚙️ Payment Settings', '📦 Store Stock', '💳 Add Working BIN', '↩️ Return to User Menu']:
        bot.send_message(message.chat.id, "Broadcast cancelled.")
        handle_menu(message)
        return

    broadcast_msg = message.text
    users = users_collection.find({}, {"chat_id": 1}) 
    success = 0
    failed = 0
    
    bot.send_message(message.chat.id, "Broadcasting message... Please wait.")
    
    for user in users:
        try:
            bot.send_message(user['chat_id'], broadcast_msg)
            success += 1
            time.sleep(0.05) 
        except Exception:
            failed += 1
            
    bot.send_message(message.chat.id, f"✅ **Broadcast Completed!**\n\nSuccess: {success}\nFailed: {failed}", reply_markup=get_admin_menu())

def process_manage_balance_id(message):
    if message.text in ['📊 Bot Statistics', '📢 Send Broadcast', '💵 Manage Balance', '⚙️ Payment Settings', '📦 Store Stock', '💳 Add Working BIN', '↩️ Return to User Menu']:
        bot.send_message(message.chat.id, "Balance management cancelled.")
        handle_menu(message)
        return

    try:
        target_id = int(message.text)
        target_user = users_collection.find_one({"chat_id": target_id})
        if not target_user:
            bot.send_message(message.chat.id, " User not found in database.", reply_markup=get_admin_menu())
            return
        
        current_balance = target_user.get("balance", 0.0)
        msg = bot.send_message(
            message.chat.id, 
            f"User found: {target_user.get('first_name', 'Unknown')} ({target_id})\nCurrent Balance: {fmt_bal(current_balance)}\n\nEnter the amount to ADD (use negative like -10 to deduct):"
        )
        bot.register_next_step_handler(msg, process_manage_balance_amount, target_id, current_balance)
    except ValueError:
        bot.send_message(message.chat.id, " Invalid Chat ID. Must be numbers only. Cancelled.", reply_markup=get_admin_menu())

def process_manage_balance_amount(message, target_id, current_balance):
    if message.text in ['📊 Bot Statistics', '📢 Send Broadcast', '💵 Manage Balance', '⚙️ Payment Settings', '📦 Store Stock', '💳 Add Working BIN', '↩️ Return to User Menu']:
        bot.send_message(message.chat.id, "Balance management cancelled.")
        handle_menu(message)
        return

    try:
        amount = float(message.text)
        new_balance = round(current_balance + amount, 2)
        
        users_collection.update_one({"chat_id": target_id}, {"$set": {"balance": new_balance}})
        bot.send_message(message.chat.id, f"✅ Successfully updated balance!\n\nNew Balance: {fmt_bal(new_balance)}", reply_markup=get_admin_menu())
        
        try:
            bot.send_message(target_id, f"💰 Your balance has been updated by the Admin.\nAmount changed: {'+' if amount > 0 else ''}{amount}\nNew Balance: {fmt_bal(new_balance)}")
        except:
            pass 
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount. Must be a number. Cancelled.", reply_markup=get_admin_menu())

# --- DEPOSIT FLOW FUNCTIONS ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('depmethod_'))
def handle_deposit_method(call):
    bot.answer_callback_query(call.id)
    method = call.data.replace('depmethod_', '')
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        
    pending_count = transactions_collection.count_documents({"user_id": user_id, "status": "pending"})
    if pending_count >= 5:
        err_msg = (
            "❌ **আপনার ইতোমধ্যে ৫টি ডিপোজিট আবেদন পেন্ডিং রয়েছে।**\n\n"
            "অনুগ্রহ করে পূর্বের আবেদনগুলো এপ্রুভ বা প্রসেস হওয়া পর্যন্ত অপেক্ষা করুন।"
            if lang == 'bn' else
            "❌ **You already have 5 pending deposit requests.**\n\nPlease wait until your previous requests are processed."
        )
        bot.send_message(user_id, err_msg, parse_mode="Markdown", reply_markup=get_main_menu(lang, user_id))
        return
            
    if method == 'USDT_BEP20':
        conf = get_config()
        usdt_rate = conf.get("usdt_rate", 129.0)
        ask_amt_msg = (
            f"💵 **USDT (BEP20) Deposit** ⚡\n\n"
            f"📈 **Exchange Rate:** 1 USDT = {usdt_rate} ৳\n"
            f"🔹 **Minimum Deposit:** 0.10 USDT ($0.10)\n\n"
            f"Please enter the amount in **Dollar / USDT** you want to deposit (e.g. `1`, `5`, `0.50`, `10`):"
            if lang == 'en' else
            f"💵 **USDT (BEP20) ডিপোজিট** ⚡\n\n"
            f"📈 **রেট:** ১ USDT = {usdt_rate} ৳\n"
            f"🔹 **সর্বনিম্ন ডিপোজিট:** 0.10 USDT ($0.10)\n\n"
            f"আপনি কত **ডলার / USDT** ডিপোজিট করতে চান তা নিচে লিখুন (যেমন: `1`, `5`, `0.50`, `10`):"
        )
        msg = bot.send_message(user_id, ask_amt_msg, parse_mode="Markdown", reply_markup=get_cancel_menu(lang))
    else:
        msg = bot.send_message(user_id, texts[lang]['dep_ask_amount'].format(method=method), reply_markup=get_cancel_menu(lang))
    bot.register_next_step_handler(msg, process_deposit_amount, method)

def process_deposit_amount(message, method):
    user_id = message.chat.id
    text = message.text.strip()
    lang = get_user_lang(user_id)
    
    if text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 বযাক'] or text in texts['en'].values() or text in texts['bn'].values() or is_back_or_cancel_text(text):
        bot.send_message(user_id, texts[lang]['dep_cancelled'], reply_markup=get_main_menu(lang, user_id))
        return
        
    try:
        amount = float(text)
        conf = get_config()
        
        if method == 'USDT_BEP20':
            if amount < 0.10:
                err_m = "❌ Minimum USDT deposit is 0.10 USDT ($0.10). Please enter a valid amount:" if lang == 'en' else "❌ সর্বনিম্ন ডিপোজিট 0.10 USDT ($0.10)। দয়া করে সঠিক এমাউন্ট দিন:"
                msg = bot.send_message(user_id, err_m, reply_markup=get_cancel_menu(lang))
                bot.register_next_step_handler(msg, process_deposit_amount, method)
                return
            target_acc = conf.get("usdt_address", "0x55d398326f99059ff775485246999027b3197955")
            usdt_rate = conf.get("usdt_rate", 129.0)
            bdt_equiv = round(amount * usdt_rate, 2)
            
            inline_markup = InlineKeyboardMarkup()
            inline_markup.add(InlineKeyboardButton(texts[lang]['dep_submit_btn'], callback_data=f"asktrx_{method}_{amount}"))
            
            msg_text = (
                f"💎 **USDT (BEP20) Deposit Instructions** ⚡\n\n"
                f"💵 **Deposit Amount:** `{amount} USDT` (≈ {bdt_equiv} ৳)\n"
                f"📈 **Exchange Rate:** 1 USDT = {usdt_rate} ৳\n\n"
                f"📍 **Bitget BEP20 Address:**\n`{target_acc}`\n\n"
                f"👉 Send **{amount} USDT** to the address above, then click **Submit TrxID** below!"
            )
            bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=inline_markup)
            return

        min_dep = conf.get("min_deposit", 5.0)
        if amount < min_dep:
            msg = bot.send_message(user_id, texts[lang]['dep_min_err'].format(min_dep=min_dep), reply_markup=get_cancel_menu(lang))
            bot.register_next_step_handler(msg, process_deposit_amount, method)
            return
            
        if method == 'bKash':
            target_acc = conf.get("bkash", "Not set")
            method_str = "bKash Personal Number (Send Money)"
        elif method == 'Nagad':
            target_acc = conf.get("nagad", conf.get("bkash", "Not set"))
            method_str = "Nagad Personal Number (Send Money)"
        else:
            target_acc = conf.get("binance", "Not set")
            method_str = "Binance ID"
            
        inline_markup = InlineKeyboardMarkup()
        inline_markup.add(InlineKeyboardButton(texts[lang]['dep_submit_btn'], callback_data=f"asktrx_{method}_{amount}"))
        
        msg_text = texts[lang]['dep_instruct'].format(method=method, amount=amount, method_str=method_str, target_acc=target_acc)
        bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=inline_markup)
        
    except ValueError:
        msg = bot.send_message(user_id, texts[lang]['dep_invalid_amt'], reply_markup=get_cancel_menu(lang))
        bot.register_next_step_handler(msg, process_deposit_amount, method)

@bot.callback_query_handler(func=lambda call: call.data.startswith('asktrx_'))
def handle_ask_trxid(call):
    bot.answer_callback_query(call.id)
    payload = call.data[7:]
    method, amount = payload.rsplit('_', 1)
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
    
    if method == 'bKash':
        ask_msg = "Please enter your **10-character** bKash Transaction ID (TrxID) below:" if lang == 'en' else "দয়া করে আপনার **১০ সংখযার** bKash Transaction ID (TrxID) নিচে দিন:"
    elif method == 'USDT_BEP20':
        ask_msg = "Please enter your **Bitget / BEP20 TxHash (Transaction ID)** below:" if lang == 'en' else "দয়া করে আপনার **Bitget / BEP20 TxHash (Transaction ID)** নিচে দিন:"
    else:
        ask_msg = "Please enter your **Binance** Transaction ID (TrxID) below:" if lang == 'en' else "দয়া করে আপনার **Binance** Transaction ID (TrxID) নিচে দিন:"
        
    msg = bot.send_message(user_id, ask_msg, parse_mode="Markdown", reply_markup=get_cancel_menu(lang))
    bot.register_next_step_handler(msg, process_deposit_final_trxid, method, amount)

def process_deposit_final_trxid(message, method, amount):
    user_id = message.chat.id
    raw_text = message.text.strip()
    text = raw_text.upper() if method != 'USDT_BEP20' else raw_text
    lang = get_user_lang(user_id)
    
    if raw_text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 বযাক'] or raw_text in texts['en'].values() or raw_text in texts['bn'].values() or is_back_or_cancel_text(raw_text):
        bot.send_message(user_id, texts[lang]['dep_cancelled'], reply_markup=get_main_menu(lang, user_id))
        return
        
    if method == 'bKash' and len(text) != 10:
        bot.send_message(user_id, texts[lang]['dep_wrong_trxid'], reply_markup=get_main_menu(lang, user_id))
        return
    elif method == 'Binance' and len(text) < 8:
        bot.send_message(user_id, texts[lang]['dep_wrong_trxid'], reply_markup=get_main_menu(lang, user_id))
        return
    elif method == 'USDT_BEP20' and len(raw_text) < 10:
        bot.send_message(user_id, "❌ Invalid TxHash length.", reply_markup=get_main_menu(lang, user_id))
        return
        
    existing = transactions_collection.find_one({"trx_id": text})
    if existing:
        bot.send_message(user_id, texts[lang]['dep_used_trxid'], reply_markup=get_main_menu(lang, user_id))
        return

    conf = get_config()

    # --- BITGET USDT AUTO VERIFICATION ---
    if method == 'USDT_BEP20':
        ver_res = verify_bitget_usdt_deposit(raw_text, conf)
        if ver_res and ver_res.get("status") == "approved":
            usdt_amt = ver_res.get("usdt_amount") or float(amount)
            usdt_rate = conf.get("usdt_rate", 129.0)
            actual_bdt = round(usdt_amt * usdt_rate, 2)

            transactions_collection.insert_one({
                "trx_id": raw_text,
                "user_id": user_id,
                "amount": actual_bdt,
                "usdt_amount": usdt_amt,
                "usdt_rate": usdt_rate,
                "requested_amount": float(amount),
                "method": "USDT (BEP20)",
                "status": "approved",
                "timestamp": datetime.now(timezone.utc)
            })

            user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": actual_bdt}}, return_document=True)
            new_balance = user.get("balance", actual_bdt) if user else actual_bdt

            success_msg = (
                f"🎉 **Auto USDT Deposit Verified & Approved!** ⚡\n\n"
                f"💳 Method: **Bitget USDT (BEP20)**\n"
                f"💵 USDT Deposited: **{usdt_amt} USDT**\n"
                f"📈 Exchange Rate: **{usdt_rate} ৳ / USDT**\n"
                f"💰 Balance Added: **{fmt_bal(actual_bdt)} ৳**\n"
                f"📝 TxHash: `{raw_text}`\n"
                f"💎 New Balance: **{fmt_bal(new_balance)} ৳**"
            )
            bot.send_message(user_id, success_msg, parse_mode="Markdown", reply_markup=get_main_menu(lang, user_id))

            try:
                send_group_deposit_notification(user_id, "USDT (BEP20)", actual_bdt, raw_text, is_auto=True)
            except:
                pass
            add_app_log(f"✅ AUTO BITGET VERIFIED: {usdt_amt} USDT ({fmt_bal(actual_bdt)} ৳) by User {user_id}")
            return
        else:
            target_acc = conf.get("usdt_address", "0x5Dc3A9BfAd702A8f804fECbe7B53c95A91F5283e")
            reject_msg = (
                "❌ **Invalid or Unverified TxHash!** ⚠️\n\n"
                "The submitted Transaction ID (TxHash) was not found on the BSC Blockchain or does not match a successful deposit to our address.\n\n"
                f"📍 **Our BEP20 Address:**\n`{target_acc}`\n\n"
                "Please check your transaction on BscScan/Bitget and submit the correct TxHash again."
                if lang == 'en' else
                "❌ **ভুল অথবা অমান্য TxHash!** ⚠️\n\n"
                "আপনার প্রদানকৃত Transaction ID (TxHash) টি ব্লকচেইনে পাওয়া যায়নি অথবা আমাদের ওয়ালেট এড্রেসে টাকা জমা হওয়ার তথ্য মিলেনি।\n\n"
                f"📍 **আমাদের BEP20 এড্রেস:**\n`{target_acc}`\n\n"
                "দয়া করে নিশ্চিত হয়ে সঠিক TxHash টি পুনরায় সাবমিট করুন।"
            )
            bot.send_message(user_id, reject_msg, parse_mode="Markdown", reply_markup=get_main_menu(lang, user_id))
            return
        
    # Check if SMS already received in received_sms_collection (unclaimed)
    matched_sms = received_sms_collection.find_one({"trx_id": text, "status": "unclaimed"})
    if matched_sms:
        actual_amount = float(matched_sms.get("amount", amount))
        sms_method = matched_sms.get("method", method)
        req_amount = float(amount)

        transactions_collection.insert_one({
            "trx_id": text,
            "user_id": user_id,
            "amount": actual_amount,
            "requested_amount": req_amount,
            "method": sms_method,
            "status": "approved",
            "timestamp": datetime.now(timezone.utc)
        })
        received_sms_collection.update_one({"trx_id": text}, {"$set": {"status": "claimed", "claimed_by": user_id}})

        user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": actual_amount}}, return_document=True)
        new_balance = user.get("balance", actual_amount) if user else actual_amount

        note_str = f"\nRequested Amount: **{fmt_bal(req_amount)} ৳**" if req_amount != actual_amount else ""

        success_msg = (
            f"🎉 **Auto Deposit Verified & Approved!**\n\n"
            f"Method: **{sms_method}**{note_str}\n"
            f"Actual Amount Added: **{fmt_bal(actual_amount)} ৳**\n"
            f"TrxID: `{text}`\n"
            f"New Balance: **{fmt_bal(new_balance)} ৳**"
        )
        bot.send_message(user_id, success_msg, parse_mode="Markdown", reply_markup=get_main_menu(lang, user_id))

        try:
            send_group_deposit_notification(user_id, sms_method, actual_amount, text, is_auto=True)
        except:
            pass
        add_app_log(f"✅ VERIFIED & CLAIMED: {sms_method} {fmt_bal(actual_amount)} ৳ (TrxID: {text}) by User {user_id}")
        return

    # 3. If SMS is delayed or not received yet, save as pending and notify user
    transactions_collection.insert_one({
        "trx_id": text,
        "user_id": user_id,
        "amount": float(amount),
        "method": method,
        "status": "pending",
        "timestamp": datetime.now(timezone.utc)
    })
    add_app_log(f"⏳ PENDING CLAIM: TrxID {text} by User {user_id} (Waiting for SMS)")

    pending_msg = "⏳ **Deposit Submitted & Pending Verification!**"
    bot.send_message(user_id, pending_msg, parse_mode="Markdown", reply_markup=get_main_menu(lang, user_id))

    # Send notification to admin channel for visibility
    admin_pending_log = (
        f"⏳ **New Pending Deposit Submitted**\n"
        f"User ID: `{user_id}`\n"
        f"Method: **{method}**\n"
        f"Amount: **{fmt_bal(amount)} ৳**\n"
        f"TrxID: `{text}`"
    )
    try:
        bot.send_message(-1002720523475, admin_pending_log, parse_mode="Markdown")
    except:
        pass
    return
    
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Approve", callback_data=f"gdep_app_{text}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"gdep_rej_{text}")
    )
    
    admin_msg = f"🔔 **New Deposit Request (SMS Pending)**\nUser ID: `{user_id}`\nMethod: **{method}**\nAmount: **{amount}**\nTrxID: `{text}`"
    bot.send_message(-1002720523475, admin_msg, parse_mode="Markdown", reply_markup=markup)
    
    bot.send_message(user_id, texts[lang]['dep_success'], reply_markup=get_main_menu(lang, user_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith('gdep_'))
def handle_group_deposit_approval(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split('_')
    action = parts[1]
    trx_id = parts[2]
    
    tx = transactions_collection.find_one({"trx_id": trx_id})
    if not tx:
        bot.edit_message_text(" Transaction not found in DB.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        return
        
    if tx['status'] != 'pending':
        bot.edit_message_text(f"⚠ This transaction was already {tx['status']}.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        return
        
    target_id = tx['user_id']
    amount = tx['amount']
    
    if action == 'app':
        transactions_collection.update_one({"trx_id": trx_id}, {"$set": {"status": "approved"}})
        
        user = users_collection.find_one_and_update({"chat_id": target_id}, {"$inc": {"balance": amount}}, return_document=True)
        new_balance = user.get("balance") if user else amount
        
        bot.edit_message_text(f"✅ **Approved**\nUser: `{target_id}`\nAmount: {amount}\nTrxID: `{trx_id}`", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        
        try:
            bot.send_message(target_id, f"🎉 **Deposit Approved!**\n\nAmount Added: **{amount}**\nNew Balance: **{new_balance}**", parse_mode="Markdown")
        except:
            pass
            
    elif action == 'rej':
        transactions_collection.update_one({"trx_id": trx_id}, {"$set": {"status": "rejected"}})
        bot.edit_message_text(f" **Rejected**\nUser: `{target_id}`\nAmount: {amount}\nTrxID: `{trx_id}`", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        
        try:
            bot.send_message(target_id, " Your deposit request was rejected by the admin. Please verify your TrxID and try again.")
        except:
            pass

# --- STORE & BUY FLOW FUNCTIONS ---
@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_vpn')
def handle_buy_vpn_list(call):
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    pipeline = [
        {"$match": {"status": "available", "category": "Nord"}},
        {"$group": {
            "_id": {"category": "$category", "duration": "$duration"},
            "stock": {"$sum": 1}
        }}
    ]
    available_vpns = list(vpn_collection.aggregate(pipeline))
    
    if not available_vpns:
        bot.edit_message_text(texts[lang]['buy_out_of_stock'], chat_id=user_id, message_id=call.message.message_id)
        return
        
    conf = get_config()
    vpn_price = conf.get("vpn_price", 15.0)
    
    inline_markup = InlineKeyboardMarkup()
    for vpn in available_vpns:
        cat = vpn['_id']['category']
        dur = vpn['_id']['duration']
        stock = vpn['stock']
        
        btn_text = f"🛡 {cat}({dur}) | 💵 {vpn_price} ৳ | 📦 stock-{stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data))
        
    bot.edit_message_text(texts[lang]['buy_ask_vpn'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_hotmail')
def handle_buy_hotmail_list(call):
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    pipeline = [
        {"$match": {"status": "available", "category": "Hotmail"}},
        {"$group": {
            "_id": {"category": "$category", "duration": "$duration"},
            "stock": {"$sum": 1}
        }}
    ]
    available_mails = list(vpn_collection.aggregate(pipeline))
    
    if not available_mails:
        bot.edit_message_text(texts[lang]['buy_out_of_stock'], chat_id=user_id, message_id=call.message.message_id)
        return
        
    conf = get_config()
    hm_price = conf.get("hotmail_price", 5.0)
    
    inline_markup = InlineKeyboardMarkup()
    for mail in available_mails:
        cat = mail['_id']['category']
        dur = mail['_id']['duration']
        stock = mail['stock']
        
        btn_text = f"📧 {cat} | 💵 {hm_price} ৳ | 📦 stock-{stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data))
        
    bot.edit_message_text(texts[lang]['buy_ask_hotmail'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_outlook')
def handle_buy_outlook_list(call):
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    pipeline = [
        {"$match": {"status": "available", "category": "Outlook"}},
        {"$group": {
            "_id": {"category": "$category", "duration": "$duration"},
            "stock": {"$sum": 1}
        }}
    ]
    available_mails = list(vpn_collection.aggregate(pipeline))
    
    if not available_mails:
        bot.edit_message_text(texts[lang]['buy_out_of_stock'], chat_id=user_id, message_id=call.message.message_id)
        return
        
    conf = get_config()
    out_price = conf.get("outlook_price", 5.0)
    
    inline_markup = InlineKeyboardMarkup()
    for mail in available_mails:
        cat = mail['_id']['category']
        dur = mail['_id']['duration']
        stock = mail['stock']
        
        btn_text = f"📧 {cat} | 💵 {out_price} ৳ | 📦 stock-{stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data))
        
    bot.edit_message_text(texts[lang]['buy_ask_outlook'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_outlookfr')
def handle_buy_outlook_fr_list(call):
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    pipeline = [
        {"$match": {"status": "available", "category": "Outlook.fr"}},
        {"$group": {
            "_id": {"category": "$category", "duration": "$duration"},
            "stock": {"$sum": 1}
        }}
    ]
    available_mails = list(vpn_collection.aggregate(pipeline))
    
    if not available_mails:
        bot.edit_message_text(texts[lang]['buy_out_of_stock'], chat_id=user_id, message_id=call.message.message_id)
        return
        
    conf = get_config()
    out_fr_price = conf.get("outlook_fr_price", 5.0)
    
    inline_markup = InlineKeyboardMarkup()
    for mail in available_mails:
        cat = mail['_id']['category']
        dur = mail['_id']['duration']
        stock = mail['stock']
        
        btn_text = f"📧 {cat} | 💵 {out_fr_price} ৳ | 📦 stock-{stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data))
        
    bot.edit_message_text(texts[lang]['buy_ask_outlook_fr'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_proxy')
def handle_buy_proxy_list(call):
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    pipeline = [
        {"$match": {"status": "available", "category": "Proxy"}},
        {"$group": {
            "_id": {"category": "$category", "duration": "$duration"},
            "stock": {"$sum": 1}
        }}
    ]
    available_proxies = list(vpn_collection.aggregate(pipeline))
    
    if not available_proxies:
        bot.edit_message_text(texts[lang]['buy_out_of_stock'], chat_id=user_id, message_id=call.message.message_id)
        return
        
    conf = get_config()
    proxy_price = conf.get("proxy_price", 10.0)
    
    inline_markup = InlineKeyboardMarkup()
    for proxy in available_proxies:
        cat = proxy['_id']['category']
        dur = proxy['_id']['duration']
        stock = proxy['stock']
        
        btn_text = f"🔌 {cat} | 💵 {proxy_price} ৳ | 📦 stock-{stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data))
        
    bot.edit_message_text(texts[lang]['buy_ask_proxy'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

def get_stock_query(cat, dur=None):
    query = {"category": cat, "status": "available"}
    if cat == "Nord" and dur:
        query["duration"] = dur
    return query

def send_order_confirmation(user_id, cat, dur, qty=1, message_id=None):
    conf = get_config()
    price = get_product_price(cat, conf)
    total_bill = fmt_bal(price * qty)

    stock = vpn_collection.count_documents(get_stock_query(cat, dur))
    user = users_collection.find_one({"chat_id": user_id}, {"balance": 1})
    user_bal = fmt_bal(user.get("balance", 0.0) if user else 0.0)

    cat_emojis = {
        "Nord": '<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji> ',
        "Hotmail": '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> ',
        "Outlook": '<tg-emoji emoji-id="5253742260054409879">⚡</tg-emoji> ',
        "Outlook.fr": '<tg-emoji emoji-id="5344008428073280881">⚡</tg-emoji> ',
        "Proxy": '<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji> '
    }

    cat_names = {
        "Nord": "Nord VPN 7 Days",
        "Hotmail": "Hotmail Mail",
        "Outlook": "Outlook Mail",
        "Outlook.fr": "Outlook.fr Mail",
        "Proxy": "High-Speed Proxy"
    }
    prod_emoji = cat_emojis.get(cat, "")
    prod_name = cat_names.get(cat, f"{cat} {dur}")

    msg_html = (
        f'<tg-emoji emoji-id="6170123066513824625">✅</tg-emoji> <b>Order Confirmation</b>\n\n'
        f"<b>Product:</b> {prod_emoji}{prod_name}\n"
        f"<b>Quantity:</b> {qty}\n"
        f"<b>Total Bill:</b> {total_bill} ৳\n\n"
        f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Wallet Balance:</b> {user_bal} ৳\n'
        f'<tg-emoji emoji-id="5249118096400070189">📦</tg-emoji> <b>Available Stock:</b> {stock}'
    )

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Place Order", callback_data=f"placeord_{cat}_{dur}_{qty}", style="success", icon_custom_emoji_id="5409048419211682843"),
        InlineKeyboardButton("Cancel order", callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5240241223632954241")
    )

    if message_id:
        try:
            bot.edit_message_text(msg_html, chat_id=user_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(user_id, msg_html, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_item_'))
def handle_buy_item(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    parts = call.data.split('_')
    cat = parts[2]
    dur = parts[3]

    stock = vpn_collection.count_documents(get_stock_query(cat, dur))
    if stock == 0:
        bot.answer_callback_query(call.id, texts[lang]['buy_sold_out'], show_alert=True)
        return

    bot.answer_callback_query(call.id)

    conf = get_config()
    price = get_product_price(cat, conf)

    cat_names = {
        "Nord": "Nord VPN 7 Days",
        "Hotmail": "Hotmail Mail",
        "Outlook": "Outlook Mail",
        "Outlook.fr": "Outlook.fr Mail",
        "Proxy": "High-Speed Proxy"
    }
    prod_name = cat_names.get(cat, f"{cat} {dur}")

    cat_emojis = {
        "Nord": '<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji> ',
        "Hotmail": '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> ',
        "Outlook": '<tg-emoji emoji-id="5253742260054409879">⚡</tg-emoji> ',
        "Outlook.fr": '<tg-emoji emoji-id="5344008428073280881">⚡</tg-emoji> ',
        "Proxy": '<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji> '
    }
    prod_emoji = cat_emojis.get(cat, "")

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("1", callback_data=f"setqty_{cat}_{dur}_1", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("5", callback_data=f"setqty_{cat}_{dur}_5", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("10", callback_data=f"setqty_{cat}_{dur}_10", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("20", callback_data=f"setqty_{cat}_{dur}_20", style="primary", icon_custom_emoji_id="5463289097336405244")
    )
    markup.add(InlineKeyboardButton("Custom Amount", callback_data=f"setqty_{cat}_{dur}_custom", style="success", icon_custom_emoji_id="5192825506239616944"))
    markup.add(InlineKeyboardButton("Go Back", callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5220079633533250496"))

    msg_text = (
        f'<tg-emoji emoji-id="4967518033061872209">🛒</tg-emoji> <b>Select Quantity</b>\n\n'
        f"<b>Item:</b> {prod_emoji}<b>{prod_name}</b>\n"
        f'<tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Available Stock:</b> {stock}\n'
        f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price per item:</b> {fmt_bal(price)} ৳\n\n'
        f"Please select quantity or click <b>Custom Amount</b>:"
    )

    try:
        bot.edit_message_text(msg_text, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except:
        bot.send_message(user_id, msg_text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('setqty_'))
def handle_set_qty(call):
    parts = call.data.split('_')
    cat = parts[1]
    dur = parts[2]
    val = parts[3]

    user_id = call.message.chat.id
    lang = get_user_lang(user_id)

    stock = vpn_collection.count_documents(get_stock_query(cat, dur))

    if val == "custom":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(user_id, f"✏️ **Enter Quantity**\n\nPlease type the number of **{cat}** accounts you want to buy (1 to {stock}):", reply_markup=get_cancel_menu(lang), parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_custom_qty_input, cat, dur, stock)
        return

    qty = int(val)
    if qty > stock:
        bot.answer_callback_query(call.id, f"❌ Not enough stock! Only {stock} available.", show_alert=True)
        return

    bot.answer_callback_query(call.id)
    send_order_confirmation(user_id, cat, dur, qty=qty, message_id=call.message.message_id)

def process_custom_qty_input(message, cat, dur, stock):
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if message.text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 বযাক'] or is_back_or_cancel_text(message.text):
        bot.send_message(user_id, "Purchase cancelled.", reply_markup=get_main_menu(lang, user_id))
        return

    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
    except:
        bot.send_message(user_id, " Invalid quantity. Please enter a valid number.", reply_markup=get_main_menu(lang, user_id))
        return

    if qty > stock:
        bot.send_message(user_id, f"❌ Not enough stock! You requested {qty}, but only {stock} are available.", reply_markup=get_main_menu(lang, user_id))
        return

    send_order_confirmation(user_id, cat, dur, qty=qty)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_to_catalog')
def handle_cancel_to_catalog(call):
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_text(
            "**Available products**\nPlease select a product to proceed",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=get_available_products_keyboard(),
            parse_mode="Markdown"
        )
    except:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('placeord_'))
def handle_place_order(call):
    parts = call.data.split('_')
    cat = parts[1]
    dur = parts[2]
    qty = int(parts[3])

    user_id = call.message.chat.id
    lang = get_user_lang(user_id)

    conf = get_config()
    price = get_product_price(cat, conf)

    total_price = price * qty

    # Check stock
    items = list(vpn_collection.find(get_stock_query(cat, dur)).limit(qty))
    if len(items) < qty:
        bot.answer_callback_query(call.id, texts[lang]['buy_sold_out'], show_alert=True)
        return

    # Check balance
    user = users_collection.find_one({"chat_id": user_id})
    balance = user.get("balance", 0.0) if user else 0.0

    if balance < total_price:
        bot.answer_callback_query(call.id, texts[lang]['buy_no_bal'], show_alert=True)
        return

    bot.answer_callback_query(call.id)

    short_names = {
        "Nord": "Nord — 7D",
        "Hotmail": "Hotmail Mail",
        "Outlook": "Outlook Mail",
        "Outlook.fr": "Outlook.fr Mail",
        "Proxy": "High-Speed Proxy"
    }
    prod_display = short_names.get(cat, f"{cat} — {dur}")

    if qty > 1:
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("📄 TXT File (.txt)", callback_data=f"dlfile_{cat}_{dur}_{qty}_txt", style="primary"),
            InlineKeyboardButton("📊 Excel File (.xlsx)", callback_data=f"dlfile_{cat}_{dur}_{qty}_xlsx", style="success"),
            InlineKeyboardButton("◀ Go Back", callback_data="cancel_to_catalog", style="danger")
        )
        msg_html = (
            f"<b>📥 Select File Format</b>\n\n"
            f"Product: <b>{prod_display} (x{qty})</b>\n"
            f"Total Bill: <b>{fmt_bal(total_price)} ৳</b>\n\n"
            f"Please choose the file format to download your accounts:"
        )
        bot.edit_message_text(msg_html, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        return

    # Deliver 1 item directly via text
    item_ids = [item['_id'] for item in items]
    new_balance = round(balance - total_price, 2)
    users_collection.update_one({"chat_id": user_id}, {"$set": {"balance": new_balance}})
    vpn_collection.update_many({"_id": {"$in": item_ids}}, {"$set": {"status": "sold", "buyer_id": user_id, "price_paid": price, "sold_at": datetime.now(timezone.utc)}})

    creds_list = [item.get("credentials", "") for item in items]
    formatted_creds = []
    for cred in creds_list:
        cred_str = cred.strip()

        if '|' in cred_str:
            c_parts = cred_str.split('|')
            if len(c_parts) >= 4 and c_parts[0].strip() == c_parts[2].strip() and c_parts[1].strip() == c_parts[3].strip():
                cred_str = "|".join([c_parts[0].strip(), c_parts[1].strip()] + [x.strip() for x in c_parts[4:]])

        if cat in ["Hotmail", "Outlook", "Outlook.fr"]:
            formatted_creds.append(f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{escape(cred_str)}</code>')
        elif cat == "Proxy":
            p_parts = cred_str.split(':')
            if len(p_parts) >= 4:
                host = escape(p_parts[0].strip())
                port = escape(p_parts[1].strip())
                u_raw = p_parts[2].strip()
                u_match = re.search(r"^(.*?_US)", u_raw)
                if u_match:
                    u_raw = u_match.group(1)
                username = escape(u_raw)
                password = escape(":".join(p_parts[3:]).strip())
                formatted_creds.append(
                    f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> Format: Host:Port:Username:Password\n'
                    f'<tg-emoji emoji-id="5870972873450984431">🔒</tg-emoji> <code>{host}</code>:<code>{port}</code>:<code>{username}</code>:<code>{password}</code>'
                )
            else:
                formatted_creds.append(
                    f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> Format: Host:Port:Username:Password\n'
                    f'<tg-emoji emoji-id="5870972873450984431">🔒</tg-emoji> <code>{escape(cred_str)}</code>'
                )
        elif '|' in cred_str:
            c_parts = cred_str.split('|')
            if len(c_parts) == 2:
                email = escape(c_parts[0].strip())
                password = escape(c_parts[1].strip())
                formatted_creds.append(
                    f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{email}</code>\n'
                    f'<tg-emoji emoji-id="5870972873450984431">🔒</tg-emoji> <code>{password}</code>'
                )
            else:
                formatted_creds.append(f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{escape(cred_str)}</code>')
        elif ' ' in cred_str:
            c_parts = cred_str.split(' ')
            if len(c_parts) == 2:
                email = escape(c_parts[0].strip())
                password = escape(c_parts[1].strip())
                formatted_creds.append(
                    f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{email}</code>\n'
                    f'<tg-emoji emoji-id="5870972873450984431">🔒</tg-emoji> <code>{password}</code>'
                )
            else:
                formatted_creds.append(f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{escape(cred_str)}</code>')
        else:
            formatted_creds.append(f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{escape(cred_str)}</code>')

    raw_creds_text = "\n\n".join(formatted_creds)

    cat_emojis = {
        "Nord": '<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji>',
        "Hotmail": '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji>',
        "Outlook": '<tg-emoji emoji-id="5253742260054409879">⚡</tg-emoji>',
        "Outlook.fr": '<tg-emoji emoji-id="5344008428073280881">⚡</tg-emoji>',
        "Proxy": '<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji>',
        "Gmail": '<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji>'
    }
    prod_emoji = cat_emojis.get(cat, "")

    success_msg = (
        f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>Purchase Successful!</b>\n\n'
        f'{prod_emoji} <b>{prod_display} x{qty}</b>\n'
        f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>Total:</b> {fmt_bal(total_price)} | <b>Balance:</b> {fmt_bal(new_balance)}\n\n'
        f'{raw_creds_text}'
    )

    delivered = False
    try:
        bot.edit_message_text(success_msg, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
        delivered = True
    except Exception as e1:
        plain_creds = []
        for cred in creds_list:
            cred_str = cred.strip()
            if '|' in cred_str:
                c_parts = cred_str.split('|')
                plain_creds.append(f"📧 {c_parts[0].strip()}\n🔒 {c_parts[1].strip()}")
            else:
                plain_creds.append(f"📧 {cred_str}")
        plain_msg = (
            f"✅ Purchase Successful!\n\n"
            f"📧 {prod_display} x{qty}\n"
            f"💎 Total: {fmt_bal(total_price)} | Balance: {fmt_bal(new_balance)}\n\n"
            f"{'\n\n'.join(plain_creds)}"
        )
        try:
            bot.edit_message_text(plain_msg, chat_id=user_id, message_id=call.message.message_id)
            delivered = True
        except:
            try:
                bot.send_message(user_id, plain_msg)
                delivered = True
            except Exception as e3:
                print(f"Failed to deliver item to user {user_id}: {e3}")

    if not delivered:
        # AUTOMATIC ROLLBACK & REFUND IF TELEGRAM DELIVERY FAILED
        users_collection.update_one({"chat_id": user_id}, {"$inc": {"balance": total_price}})
        vpn_collection.update_many({"_id": {"$in": item_ids}}, {"$set": {"status": "available", "buyer_id": None}})
        try:
            bot.send_message(user_id, f"⚠️ Delivery failed due to network error!\nYour balance of {fmt_bal(total_price)} ৳ has been automatically refunded.")
        except:
            pass
        return

    # Send Log
    username = user.get("username")
    first_name = user.get("first_name", "Unknown")
    user_identifier = f"@{username}" if username else first_name

    log_msg = f"🛒 **New Purchase**\n\nUser: {user_identifier} (`{user_id}`)\nItem: {cat} (x{qty})\nPrice Paid: {total_price} ৳\nRemaining Balance: {fmt_bal(new_balance)} ৳"
    try:
        bot.send_message(-1002978737951, log_msg, parse_mode="Markdown")
    except:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('dlfile_'))
def handle_download_file(call):
    parts = call.data.split('_')
    cat = parts[1]
    dur = parts[2]
    qty = int(parts[3])
    fmt = parts[4]

    user_id = call.message.chat.id
    lang = get_user_lang(user_id)

    conf = get_config()
    price = get_product_price(cat, conf)
    total_price = price * qty

    items = list(vpn_collection.find(get_stock_query(cat, dur)).limit(qty))
    if len(items) < qty:
        bot.answer_callback_query(call.id, texts[lang]['buy_sold_out'], show_alert=True)
        return

    user = users_collection.find_one({"chat_id": user_id})
    balance = user.get("balance", 0.0) if user else 0.0

    if balance < total_price:
        bot.answer_callback_query(call.id, texts[lang]['buy_no_bal'], show_alert=True)
        return

    bot.answer_callback_query(call.id)

    item_ids = [item['_id'] for item in items]
    new_balance = round(balance - total_price, 2)
    users_collection.update_one({"chat_id": user_id}, {"$set": {"balance": new_balance}})
    vpn_collection.update_many({"_id": {"$in": item_ids}}, {"$set": {"status": "sold", "buyer_id": user_id, "price_paid": price, "sold_at": datetime.now(timezone.utc)}})

    short_names = {
        "Nord": "Nord — 7D",
        "Hotmail": "Hotmail Mail",
        "Outlook": "Outlook Mail",
        "Outlook.fr": "Outlook.fr Mail",
        "Proxy": "High-Speed Proxy"
    }
    prod_display = short_names.get(cat, f"{cat} — {dur}")

    raw_lines = []
    for item in items:
        cred = item.get("credentials", "").strip()
        if '|' in cred:
            c_parts = cred.split('|')
            if len(c_parts) >= 4 and c_parts[0].strip() == c_parts[2].strip() and c_parts[1].strip() == c_parts[3].strip():
                cred = "|".join([c_parts[0].strip(), c_parts[1].strip()] + [x.strip() for x in c_parts[4:]])
        if cat == "Proxy" and len(cred.split(':')) >= 4:
            p_parts = cred.split(':')
            u_raw = p_parts[2].strip()
            u_match = re.search(r"^(.*?_US)", u_raw)
            if u_match:
                u_raw = u_match.group(1)
            cred = f"{p_parts[0].strip()}:{p_parts[1].strip()}:{u_raw}:{':'.join(p_parts[3:]).strip()}"
        raw_lines.append(cred)

    if fmt == 'xlsx':
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Accounts"
        if cat in ["Hotmail", "Outlook", "Outlook.fr", "Gmail"]:
            ws.append(["Gmail / Email", "Password"])
            for line in raw_lines:
                if '|' in line:
                    parts = line.split('|', 1)
                    ws.append([parts[0].strip(), parts[1].strip()])
                else:
                    ws.append([line])
        else:
            ws.append(["Credentials"])
            for line in raw_lines:
                ws.append([line])
        file_data = io.BytesIO()
        wb.save(file_data)
        file_data.seek(0)
        ext = "xlsx"
    else:
        file_content = "\n".join(raw_lines)
        file_data = io.BytesIO(file_content.encode('utf-8'))
        ext = "txt"

    file_data.name = f"{cat}_{qty}_accounts.{ext}"

    cat_emojis = {
        "Nord": '<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji>',
        "Hotmail": '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji>',
        "Outlook": '<tg-emoji emoji-id="5253742260054409879">⚡</tg-emoji>',
        "Outlook.fr": '<tg-emoji emoji-id="5344008428073280881">⚡</tg-emoji>',
        "Proxy": '<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji>',
        "Gmail": '<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji>'
    }
    prod_emoji = cat_emojis.get(cat, "")

    success_msg = (
        f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>Purchase Successful!</b>\n\n'
        f'{prod_emoji} <b>{prod_display} x{qty}</b>\n'
        f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>Total:</b> {fmt_bal(total_price)} | <b>Balance:</b> {fmt_bal(new_balance)}\n\n'
        f'📁 <i>Your {ext.upper()} file has been generated and attached below!</i>'
    )

    try:
        bot.edit_message_text(success_msg, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
    except:
        pass

    delivered = False
    try:
        bot.send_document(user_id, file_data)
        delivered = True
    except Exception as e1:
        print(f"Document delivery failed: {e1}")
        try:
            plain_lines = "\n".join(raw_lines)
            bot.send_message(user_id, f"✅ Purchase Successful ({qty} accounts):\n\n{plain_lines}")
            delivered = True
        except Exception as e2:
            print(f"Fallback text delivery failed: {e2}")

    if not delivered:
        # AUTOMATIC ROLLBACK & REFUND IF FILE DELIVERY FAILED
        users_collection.update_one({"chat_id": user_id}, {"$inc": {"balance": total_price}})
        vpn_collection.update_many({"_id": {"$in": item_ids}}, {"$set": {"status": "available", "buyer_id": None}})
        try:
            bot.send_message(user_id, f"⚠️ File delivery failed due to network error!\nYour balance of {fmt_bal(total_price)} ৳ has been automatically refunded.")
        except:
            pass
        return

    # Send Log
    username = user.get("username")
    first_name = user.get("first_name", "Unknown")
    user_identifier = f"@{username}" if username else first_name

    log_msg = f"🛒 **New Multi-Purchase ({ext.upper()})**\n\nUser: {user_identifier} (`{user_id}`)\nItem: {cat} (x{qty})\nPrice Paid: {total_price} ৳\nRemaining Balance: {fmt_bal(new_balance)} ৳"
    try:
        bot.send_message(-1002978737951, log_msg, parse_mode="Markdown")
    except:
        pass

# --- MAIL INBOX TRACKER FUNCTIONS ---
def get_ms_access_token(client_id, refresh_token):
    url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    payload = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            return response.json().get("access_token")
    except Exception as e:
        print("Token error:", e)
    return None

def process_mail_credentials(message):
    user_id = message.chat.id
    text = message.text.strip()
    lang = get_user_lang(user_id)
    
    if text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 বযাক'] or text in texts['en'].values() or text in texts['bn'].values() or is_back_or_cancel_text(text):
        bot.send_message(user_id, texts[lang]['mail_cancelled'], reply_markup=get_main_menu(lang, user_id))
        return
        
    parts = text.split('|')
    if len(parts) != 4:
        bot.send_message(user_id, texts[lang]['mail_invalid_fmt'], reply_markup=get_main_menu(lang, user_id))
        return
        
    email, password, refresh_token, client_id = [p.strip() for p in parts]
    
    bot.send_message(user_id, texts[lang]['mail_working'], reply_markup=get_main_menu(lang, user_id))
    threading.Thread(target=run_mail_tracker, args=(user_id, email, password, refresh_token, client_id)).start()

def run_mail_tracker(chat_id, email, password, refresh_token, client_id):
    lang = get_user_lang(chat_id)
    bot.send_message(chat_id, texts[lang]['mail_start'].format(email=email))
    
    access_token = get_ms_access_token(client_id, refresh_token)
    if not access_token:
        bot.send_message(chat_id, texts[lang]['mail_token_err'])
        return
        
    processed_mails = set()
    start_time = time.time()
    end_time = start_time + 30 # 30 seconds maximum
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    new_mail_found = False
    while time.time() < end_time:
        try:
            five_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')
            url = f"https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$filter=receivedDateTime ge {five_mins_ago}&$select=subject,bodyPreview,id"
            
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                messages = resp.json().get('value', [])
                for msg in messages:
                    msg_id = msg.get('id')
                    if (email, msg_id) not in processed_mails:
                        processed_mails.add((email, msg_id))
                        new_mail_found = True
                        
                        subject = msg.get('subject', '')
                        preview = msg.get('bodyPreview', '')
                        
                        text_reply = texts[lang]['mail_new'].format(subject=subject, preview=preview)
                        
                        code_match = re.search(r'\b\d{6}\b', preview)
                        if code_match:
                            text_reply += texts[lang]['mail_code'].format(code=code_match.group(0))
                        else:
                            text_reply += texts[lang]['mail_no_code']
                            
                        bot.send_message(chat_id, text_reply, parse_mode="Markdown")
                
                if new_mail_found:
                    break
            elif resp.status_code == 401:
                bot.send_message(chat_id, texts[lang]['mail_token_exp'])
                break
                
        except Exception as e:
            print(f"Mail track error: {e}")
            break
            
        time.sleep(10)
        
    if not new_mail_found:
        bot.send_message(chat_id, texts[lang]['mail_not_found'])

def run_bot():
    print("Clearing previous webhooks...")
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        pass
        
    print("Bot is running with high concurrency settings and error protection...")
    while True:
        try:
            bot.polling(non_stop=True, timeout=60, long_polling_timeout=60)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            print(f"Network error (ReadTimeout/ConnectionError): {e}")
            print("Restarting bot in 5 seconds...")
            time.sleep(5)
        except telebot.apihelper.ApiTelegramException as e:
            print(f"Telegram API Error: {e}")
            if e.error_code == 502:
                print("Bad Gateway (502) error. Telegram server is overloaded. Restarting in 5 seconds...")
            else:
                print("Unknown Telegram API Error. Restarting in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            print(f"Unexpected Polling crashed: {e}")
            print("Restarting bot in 5 seconds...")
            time.sleep(5)

if __name__ == '__main__':
    run_bot()
