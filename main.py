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
import logging
import io
import base64
import hmac
import hashlib
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from html import escape
from flask import Flask, request, jsonify
import socket

# --- RESILIENT DNS RESOLVER FOR TELEGRAM API (PREVENTS [Errno 11002] getaddrinfo failed) ---
_orig_getaddrinfo = socket.getaddrinfo
def _resilient_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        # Force IPv4 for api.telegram.org to bypass IPv6 routing failure on local ISPs
        return _orig_getaddrinfo(host, port, socket.AF_INET if host == 'api.telegram.org' else family, type, proto, flags)
    except Exception:
        if host == 'api.telegram.org':
            try:
                # Official fallback IP for Telegram Bot API if ISP DNS fails
                return _orig_getaddrinfo('149.154.166.110', port, socket.AF_INET, type, proto, flags)
            except Exception:
                pass
        raise

socket.getaddrinfo = _resilient_getaddrinfo

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
bad_gmail_collection = db['bad_gmail']
replacement_requests_collection = db['replacement_requests']

# Test Main MongoDB Connection
try:
    print("Testing Main MongoDB connection...")
    client.admin.command('ping')
    print("Main MongoDB Connection Successful!")
    try:
        vpn_collection.create_index([('buyer_id', 1), ('status', 1), ('sold_at', -1)], background=True)
        received_sms_collection.create_index([('trx_id', 1)], unique=True, background=True)
        transactions_collection.create_index([('trx_id', 1)], unique=True, background=True)
    except Exception:
        pass
except Exception as e:
    print(f"Main MongoDB Connection Failed!\nError: {e}")

# --- CONCURRENCY PROTECTION & ATOMIC TRX LOCK GATE ---
from contextlib import contextmanager

class TrxLockManager:
    def __init__(self):
        self._locks = {}
        self._global_lock = threading.Lock()

    @contextmanager
    def acquire(self, trx_id):
        clean_trx = str(trx_id or "").strip().upper()
        with self._global_lock:
            if clean_trx not in self._locks:
                self._locks[clean_trx] = threading.Lock()
            lock = self._locks[clean_trx]
        
        lock.acquire()
        try:
            yield
        finally:
            lock.release()
            with self._global_lock:
                if not lock.locked() and clean_trx in self._locks:
                    try:
                        del self._locks[clean_trx]
                    except KeyError:
                        pass

TRX_LOCK_GATE = TrxLockManager()

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
    m_upper = m_text.upper()
    
    method = None
    if "BKASH" in s_upper or "16247" in s_upper or "BKASH" in m_upper:
        method = "bKash"
    elif "NAGAD" in s_upper or "16167" in s_upper or "NAGAD" in m_upper:
        method = "Nagad"
    elif "ROCKET" in s_upper or "16216" in s_upper or "ROCKET" in m_upper:
        method = "Rocket"
    else:
        return None
            
    if not method:
        return None
        
    trx_match = re.search(r"(?:TrxID|TxnID|Txn ID|Trx ID|Trans ID|Transaction ID)\s*:?\s*(?:is\s*)?([A-Za-z0-9]+)", m_text, re.IGNORECASE)
    if not trx_match:
        return None
    trx_id = trx_match.group(1).upper()
    
    amt_match = re.search(r"(?:Tk|Amount|৳|Tk\.)\.?:?\s*(?:Tk\.?\s*)?([0-9,]+\.?[0-9]*)", m_text, re.IGNORECASE)
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
        
        status_str = '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Auto Approved (Instant)</b>' if is_auto else '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Approved by Admin</b>'
        
        msg = (
            f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>NEW DEPOSIT APPROVED!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
            f'<tg-emoji emoji-id="4967667085606912536">👤</tg-emoji> <b>User:</b> <a href="tg://user?id={user_id}">{first_name}</a> (<code>{user_id}</code>)\n'
            f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Method:</b> <b>{method}</b>\n'
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Amount:</b> <b>{fmt_bal(amount)} ৳</b>\n'
            f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <b>TrxID:</b> <code>{trx_id}</code>\n'
            f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>Status:</b> {status_str}'
        )
        bot.send_message(group_id, msg, parse_mode="HTML")
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
                            f'<tg-emoji emoji-id="5463289097336405244">🟢</tg-emoji> <b>Bitget API & BEP20 Status: ON & CONNECTED</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
                            f'<tg-emoji emoji-id="5249288301659041068">📡</tg-emoji> <b>API Connection:</b> <tg-emoji emoji-id="5463289097336405244">🟢</tg-emoji> HTTP 200 OK (Active)\n'
                            f'<tg-emoji emoji-id="5215391376081954505">📍</tg-emoji> <b>BEP20 Deposit Address:</b>\n<code>{usdt_address}</code>\n\n'
                            f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>Exchange Rate:</b> 1 USDT = {usdt_rate} ৳\n'
                            f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>Auto-Deposit:</b> <tg-emoji emoji-id="5463289097336405244">🟢</tg-emoji> ACTIVE & READY FOR USERS'
                        )
                    }
        except:
            pass

    # BEP20 Smart Auto-Deposit Status Response
    return {
        "status": "ON",
        "online": True,
        "message": (
            f'<tg-emoji emoji-id="5463289097336405244">🟢</tg-emoji> <b>USDT (BEP20) Auto-Deposit System: ON & ACTIVE</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
            f'<tg-emoji emoji-id="5215391376081954505">📍</tg-emoji> <b>Deposit Address (BEP20):</b>\n<code>{usdt_address}</code>\n\n'
            f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>Exchange Rate:</b> 1 USDT = {usdt_rate} ৳\n'
            f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Minimum Deposit:</b> 0.10 USDT ($0.10)\n'
            f'<tg-emoji emoji-id="5465154440287757794">🛡️</tg-emoji> <b>BEP20 Verification Engine:</b> <tg-emoji emoji-id="5463289097336405244">🟢</tg-emoji> ACTIVE & READY FOR USERS'
        )
    }

def process_incoming_sms(parsed, raw_sms):
    method = parsed["method"]
    trx_id = str(parsed["trx_id"]).strip().upper()
    amount = float(parsed["amount"])
    sender_phone = parsed.get("sender_phone", "")
    
    with TRX_LOCK_GATE.acquire(trx_id):
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
        try:
            received_sms_collection.insert_one(sms_doc)
        except Exception:
            return {"status": "duplicate", "trx_id": trx_id}
            
        add_app_log(f"📩 SMS RECEIVED: {method} {fmt_bal(amount)} ৳ (TrxID: {trx_id})")
        
        # Step A: Check if a user submitted this TrxID in transactions_collection (pending)
        # ATOMIC COMPARE-AND-SET: Only the thread that transitions 'pending' to 'approved' can proceed!
        claimed_tx = transactions_collection.find_one_and_update(
            {"trx_id": trx_id, "status": "pending"},
            {"$set": {
                "status": "approved",
                "amount": amount,
                "verified_at": datetime.now(timezone.utc)
            }},
            return_document=False
        )
        
        if claimed_tx:
            user_id = claimed_tx["user_id"]
            req_amount = float(claimed_tx.get("amount", amount))
            actual_amount = float(amount)

            received_sms_collection.update_one({"trx_id": trx_id}, {"$set": {"status": "claimed", "claimed_by": user_id, "claimed_at": datetime.now(timezone.utc)}})

            user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": actual_amount}}, return_document=True)
            new_balance = user.get("balance", actual_amount) if user else actual_amount

            note_str = f"\nRequested Amount: <b>{fmt_bal(req_amount)} ৳</b>" if req_amount != actual_amount else ""

            try:
                bot.send_message(
                    user_id,
                    f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>Auto Deposit Approved!</b>\n\n'
                    f'Method: <b>{method}</b>{note_str}\n'
                    f'Actual Amount Added: <b>{fmt_bal(actual_amount)} ৳</b>\n'
                    f'TrxID: <code>{trx_id}</code>\n'
                    f'New Balance: <b>{fmt_bal(new_balance)} ৳</b>',
                    parse_mode="HTML"
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
                # Atomically claim the SMS first
                claimed_sms = received_sms_collection.find_one_and_update(
                    {"trx_id": trx_id, "status": "unclaimed"},
                    {"$set": {"status": "claimed", "claimed_by": user_id, "claimed_at": datetime.now(timezone.utc)}},
                    return_document=False
                )
                if claimed_sms:
                    try:
                        transactions_collection.insert_one({
                            "trx_id": trx_id,
                            "user_id": user_id,
                            "amount": amount,
                            "method": method,
                            "status": "approved",
                            "timestamp": datetime.now(timezone.utc),
                            "verified_at": datetime.now(timezone.utc)
                        })
                    except Exception:
                        pass
                    
                    user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": amount}}, return_document=True)
                    new_balance = user.get("balance", amount) if user else amount
                    
                    try:
                        bot.send_message(
                            user_id,
                            f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>Instant Deposit Received!</b>\n\n'
                            f'Method: <b>{method}</b>\n'
                            f'Amount Added: <b>{fmt_bal(amount)} ৳</b>\n'
                            f'TrxID: <code>{trx_id}</code>\n'
                            f'New Balance: <b>{fmt_bal(new_balance)} ৳</b>',
                            parse_mode="HTML"
                        )
                    except:
                        pass
                        
                    log_msg = f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Instant Auto Deposit (Phone Matched)</b>\nUser ID: <code>{user_id}</code> (<code>{sender_phone}</code>)\nMethod: <b>{method}</b>\nAmount: <b>{amount} ৳</b>\nTrxID: <code>{trx_id}</code>'
                    try:
                        bot.send_message(-1002720523475, log_msg, parse_mode="HTML")
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
    raw_trx = data.get("trx_id")

    if not raw_trx or not action:
        return jsonify({"status": "error", "message": "Missing action or trx_id"}), 400

    trx_id = str(raw_trx).strip().upper()

    with TRX_LOCK_GATE.acquire(trx_id):
        if action == "approve":
            # ATOMIC COMPARE-AND-SET: only transitions if status is 'pending'
            tx = transactions_collection.find_one_and_update(
                {"trx_id": trx_id, "status": "pending"},
                {"$set": {"status": "approved", "verified_at": datetime.now(timezone.utc)}},
                return_document=False
            )
            if not tx:
                return jsonify({"status": "error", "message": "Pending transaction not found or already processed"}), 400

            user_id = tx["user_id"]
            amount = float(tx.get("amount", 0.0))
            method = tx.get("method", "bKash")

            received_sms_collection.update_one({"trx_id": trx_id}, {"$set": {"status": "claimed", "claimed_by": user_id, "claimed_at": datetime.now(timezone.utc)}})
            user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": amount}}, return_document=True)
            new_balance = user.get("balance", amount) if user else amount
            add_app_log(f"✅ MANUAL APPROVED: {method} {fmt_bal(amount)} ৳ (TrxID: {trx_id}) by App Admin")
            try:
                bot.send_message(user_id, f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>Deposit Approved!</b>\n\nMethod: <b>{method}</b>\nAmount Added: <b>{fmt_bal(amount)} ৳</b>\nTrxID: <code>{trx_id}</code>\nNew Balance: <b>{fmt_bal(new_balance)} ৳</b>', parse_mode="HTML")
            except:
                pass
            try:
                send_group_deposit_notification(user_id, method, amount, trx_id, is_auto=False)
            except:
                pass
            return jsonify({"status": "success", "message": "Transaction approved"}), 200

        elif action == "reject":
            tx = transactions_collection.find_one_and_update(
                {"trx_id": trx_id, "status": "pending"},
                {"$set": {"status": "rejected", "rejected_at": datetime.now(timezone.utc)}},
                return_document=False
            )
            if not tx:
                return jsonify({"status": "error", "message": "Pending transaction not found or already processed"}), 400

            user_id = tx["user_id"]
            add_app_log(f"❌ MANUAL REJECTED: TrxID {trx_id} by App Admin")
            try:
                bot.send_message(user_id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>Deposit Rejected!</b>\n\nTrxID: <code>{trx_id}</code>\nআপনার ডিপোজিট রিকোয়েস্টটি বাতিল করা হয়েছে।', parse_mode="HTML")
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

CURRENT_CLOUDFLARE_URL = None

def start_auto_cloudflare_tunnel():
    time.sleep(2)
    global CURRENT_CLOUDFLARE_URL
    cloudflared_bin = "cloudflared.exe" if os.name == 'nt' else "cloudflared"
    
    if not os.path.exists(cloudflared_bin):
        try:
            if os.name == 'nt':
                print("Auto-downloading cloudflared.exe for Windows...")
                import urllib.request
                url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
                urllib.request.urlretrieve(url, "cloudflared.exe")
                print("cloudflared.exe downloaded successfully!")
            else:
                print("Auto-downloading cloudflared binary for Linux...")
                import urllib.request
                url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
                urllib.request.urlretrieve(url, "cloudflared")
                os.chmod("cloudflared", 0o755)
                print("cloudflared downloaded successfully!")
        except Exception as e:
            print(f"Error downloading cloudflared: {e}")
            
    exec_bin = os.path.abspath(cloudflared_bin) if os.path.exists(cloudflared_bin) else cloudflared_bin
    log_file = os.path.abspath("cloudflared_tunnel.log")

    while True:
        try:
            if os.path.exists(log_file):
                try:
                    os.remove(log_file)
                except Exception:
                    pass

            exec_cmd = [exec_bin, "tunnel", "--url", "http://localhost:5000", "--logfile", log_file]
            proc = subprocess.Popen(exec_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tunnel_url = None

            # Poll logfile for generated trycloudflare URL (usually ready in 3-5 seconds)
            for _ in range(40):
                time.sleep(0.5)
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                            if match:
                                tunnel_url = match.group(0) + "/api/sms_receiver"
                                break
                    except Exception:
                        pass

            if tunnel_url:
                CURRENT_CLOUDFLARE_URL = tunnel_url
                print(f"\n======================================================\n🚀 Auto Cloudflare Tunnel Created: {tunnel_url}\n======================================================\n")
                config_collection.update_one({"_id": "payment_settings"}, {"$set": {"custom_webhook_url": tunnel_url}}, upsert=True)
                global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
                
                # Notify admin via Telegram with the live URL & secret
                for admin_id in ADMIN_IDS:
                    try:
                        bot.send_message(
                            admin_id,
                            f'<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> <b>Auto Deposit Webhook Tunnel Live!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                            f'━━━━━━━━━━━━━━━━━━━━━\n'
                            f'☁️ <b>Cloudflare URL (Mobile Data):</b>\n<code>{tunnel_url}</code>\n\n'
                            f'🏠 <b>Local Wi-Fi URL (Same Wi-Fi):</b>\n<code>http://192.168.0.106:5000/api/sms_receiver</code>\n\n'
                            f'🔑 <b>Secret Token:</b>\n<code>{SMS_SECRET_TOKEN}</code>\n'
                            f'━━━━━━━━━━━━━━━━━━━━━\n'
                            f'<tg-emoji emoji-id="5463289097336405244">📲</tg-emoji> <i>এই URL টি কপি করে আপনার <b>Notepad_AutoDeposit</b> অ্যাপে পেস্ট করে <b>SAVE & START SERVICE</b> এ চাপ দিন!</i>',
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                
            proc.wait()
            print("Auto Cloudflare Tunnel closed. Reconnecting in 5 seconds...")
            time.sleep(5)
        except Exception as e:
            print(f"Auto Cloudflare Tunnel loop error: {e}")
            time.sleep(10)

threading.Thread(target=start_auto_cloudflare_tunnel, daemon=True).start()

# --- CACHING SYSTEM ---
COUNTRY_CACHE = {}
LANG_CACHE = {}

def get_user_country(chat_id):
    if chat_id in COUNTRY_CACHE:
        return COUNTRY_CACHE[chat_id]
    u = users_collection.find_one({"chat_id": chat_id}, {"country": 1, "language": 1})
    if u and u.get("country"):
        c = u.get("country")
        COUNTRY_CACHE[chat_id] = c
        return c
    return None

def set_user_country(chat_id, country):
    COUNTRY_CACHE[chat_id] = country
    lang = "bn" if country == "bd" else "en"
    LANG_CACHE[chat_id] = lang
    users_collection.update_one(
        {"chat_id": chat_id},
        {"$set": {"country": country, "language": lang}},
        upsert=True
    )

def get_user_lang(chat_id):
    c = get_user_country(chat_id)
    if c == "bd":
        return "bn"
    elif c == "other":
        return "en"
    if chat_id in LANG_CACHE:
        return LANG_CACHE[chat_id]
    u = users_collection.find_one({"chat_id": chat_id}, {"language": 1})
    if u and u.get("language"):
        return u.get("language")
    return "bn"

# --- BANNED USERS CACHE & HELPER FUNCTIONS ---
BANNED_USERS_CACHE = set()

def load_banned_users_cache():
    global BANNED_USERS_CACHE
    try:
        banned_docs = users_collection.find({"is_banned": True}, {"chat_id": 1})
        BANNED_USERS_CACHE = set(int(doc["chat_id"]) for doc in banned_docs if doc.get("chat_id") is not None)
    except Exception as e:
        print(f"Error loading banned users cache: {e}")

try:
    load_banned_users_cache()
except Exception:
    pass

def is_user_banned(user_id):
    try:
        uid = int(user_id)
        if uid in ADMIN_IDS:
            return False
        if uid in BANNED_USERS_CACHE:
            return True
        u = users_collection.find_one({"chat_id": uid}, {"is_banned": 1})
        if u and u.get("is_banned"):
            BANNED_USERS_CACHE.add(uid)
            return True
        return False
    except Exception:
        return False

def get_user_ban_info(user_id):
    try:
        return users_collection.find_one({"chat_id": int(user_id)}, {"is_banned": 1, "ban_reason": 1, "banned_at": 1, "banned_by": 1})
    except Exception:
        return None

def ban_user(user_id, reason="Admin decision", banned_by=None):
    try:
        uid = int(user_id)
        if uid in ADMIN_IDS:
            return False, "You cannot ban an Admin!"
        users_collection.update_one(
            {"chat_id": uid},
            {"$set": {
                "is_banned": True,
                "ban_reason": reason,
                "banned_at": datetime.now(timezone.utc),
                "banned_by": banned_by
            }},
            upsert=True
        )
        BANNED_USERS_CACHE.add(uid)
        return True, "User banned successfully."
    except Exception as e:
        return False, str(e)

def unban_user(user_id):
    try:
        uid = int(user_id)
        users_collection.update_one(
            {"chat_id": uid},
            {"$set": {"is_banned": False}, "$unset": {"ban_reason": "", "banned_at": "", "banned_by": ""}}
        )
        BANNED_USERS_CACHE.discard(uid)
        return True, "User unbanned successfully."
    except Exception as e:
        return False, str(e)

def send_banned_notice(chat_id):
    ban_info = get_user_ban_info(chat_id) or {}
    reason = ban_info.get("ban_reason") or "নিয়ম লঙ্ঘনের কারণে"
    msg = (
        f'<tg-emoji emoji-id="5215642288071387368">🚫</tg-emoji> <b>আপনার অ্যাকাউন্টটি ব্যান করা হয়েছে!</b>\n'
        f'━━━━━━━━━━━━━━━━━━━━━\n'
        f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>কারণ:</b> {escape(str(reason))}\n\n'
        f'আপনি এই বটের কোনো সেবা ব্যবহার করতে পারবেন না। যদি আপনি মনে করেন এটি একটি ভুল, তাহলে আমাদের সাপোর্ট আইডিতে যোগাযোগ করুন:\n'
        f'<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> <b>সাপোর্ট:</b> @workstoresuport\n'
        f'━━━━━━━━━━━━━━━━━━━━━'
    )
    try:
        bot.send_message(chat_id, msg, parse_mode="HTML")
    except Exception:
        pass

def get_usdt_rate():
    conf = get_config()
    return float(conf.get("usdt_rate", 129.0))

def fmt_usdt(val):
    try:
        v = float(val or 0)
        if v == 0:
            return "0.00"
        if v < 0.1:
            return f"{v:.3f}"
        return f"{v:.2f}"
    except:
        return "0.00"

def fmt_bal(val):
    try:
        r = round(float(val or 0), 2)
        if r.is_integer():
            return int(r)
        return f"{r:.2f}"
    except:
        return 0

# --- TELEGRAM PREMIUM MINIMAL EMOJIS (DIGITS 0-9) ---
# Pack: https://t.me/addemoji/MinimalEmojis
MINIMAL_DIGIT_EMOJIS = {
    '0': '<tg-emoji emoji-id="5305698891750981822">0⃣</tg-emoji>',
    '1': '<tg-emoji emoji-id="5305538504787246900">1⃣</tg-emoji>',
    '2': '<tg-emoji emoji-id="5305551505653250543">2⃣</tg-emoji>',
    '3': '<tg-emoji emoji-id="5305599007991545064">3⃣</tg-emoji>',
    '4': '<tg-emoji emoji-id="5305464708659165869">4⃣</tg-emoji>',
    '5': '<tg-emoji emoji-id="5305617055444123539">5⃣</tg-emoji>',
    '6': '<tg-emoji emoji-id="5305255002585970184">6⃣</tg-emoji>',
    '7': '<tg-emoji emoji-id="5305534149690408930">7⃣</tg-emoji>',
    '8': '<tg-emoji emoji-id="5305754502987530005">8⃣</tg-emoji>',
    '9': '<tg-emoji emoji-id="5305298102582786227">9⃣</tg-emoji>',
}

def format_custom_emoji_number(val):
    s = str(val)
    res = []
    for ch in s:
        if ch in MINIMAL_DIGIT_EMOJIS:
            res.append(MINIMAL_DIGIT_EMOJIS[ch])
        else:
            res.append(ch)
    return "".join(res)

def get_user_buy_stats(user_id):
    try:
        bd_tz = timezone(timedelta(hours=6))
        today_start_bd = datetime.now(bd_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_bd.astimezone(timezone.utc)

        match_uids = [user_id, str(user_id)]
        try:
            match_uids.append(int(user_id))
        except Exception:
            pass

        pipeline = [
            {"$match": {"status": "sold", "buyer_id": {"$in": match_uids}}},
            {"$facet": {
                "total": [
                    {"$group": {
                        "_id": None,
                        "count": {"$sum": 1},
                        "spent": {"$sum": {"$ifNull": ["$price_paid", 0.0]}}
                    }}
                ],
                "today": [
                    {"$match": {"sold_at": {"$gte": today_start_utc}}},
                    {"$group": {
                        "_id": None,
                        "count": {"$sum": 1},
                        "spent": {"$sum": {"$ifNull": ["$price_paid", 0.0]}}
                    }}
                ]
            }}
        ]
        res = list(vpn_collection.aggregate(pipeline))
        if res:
            tot = res[0].get("total", [])
            tod = res[0].get("today", [])
            tot_count = tot[0]["count"] if tot else 0
            tot_spent = float(tot[0]["spent"]) if tot else 0.0
            tod_count = tod[0]["count"] if tod else 0
            tod_spent = float(tod[0]["spent"]) if tod else 0.0
            return tot_spent, tot_count, tod_spent, tod_count
    except Exception as e:
        logging.error(f"Error getting user buy stats: {e}")
    return 0.0, 0, 0.0, 0

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
        if "gemini_price" not in conf:
            config_collection.update_one({"_id": "payment_settings"}, {"$set": {"gemini_price": 70.0}})
            conf["gemini_price"] = 70.0
        
    CONFIG_CACHE = conf
    CONFIG_LAST_UPDATE = time.time()
    return conf

TOKEN = '8523237591:AAFjAsYJbAj3oY0dxdAWFGPoOEE2OyEeLjA'
bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=True, num_threads=100)

telebot.logger.setLevel(logging.INFO)

ADMIN_IDS = [6412225513, 8596783717]
texts = {'en': {'buy_btn': 'Buy',
        'deposit_btn': 'Deposit',
        'balance_btn': 'Balance',
        'price_btn': 'Price',
        'support_btn': 'Support',
        'mail_btn': 'Replacement',
        'repl_btn': 'Replacement',
        'welcome': '<tg-emoji emoji-id="5463297803235113601">✨</tg-emoji> Welcome {name}! <tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji>\n<tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji> Enjoy fast, secure service — use the menu below to get started!',
        'buy_text': 'What would you like to buy?',
        'deposit_text': 'Please select your preferred deposit method:',
        'balance_text': 'Your Balance is: {balance} USDT',
        'price_text': 'Here is the Price list.',
        'support_text': '<tg-emoji emoji-id="5237988788164107500">💬</tg-emoji> <b>Customer Support</b>\n\nIf you need any help or have inquiries, please contact our support team:\n<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> @workstoresuport',
        'mail_text': 'Please send your credentials in the following format:\n<code>mail|pass|refresh_token|client_id</code>',
        'unknown': 'Unknown command. Please use the menu below.',
        'lang_changed': 'Language changed to English!',
        'returning': 'Returning...',
        'dep_24h_limit': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> You can only send one deposit request per 24 hours. Please try again later.',
        'dep_ask_amount': 'How much do you want to deposit via {method}?',
        'dep_cancelled': 'Deposit cancelled.',
        'dep_min_err': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Minimum deposit is {min_dep}. Please enter a valid amount:',
        'dep_invalid_amt': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Invalid amount. Please enter a number:',
        'dep_submit_btn': 'Submit Transaction ID',
        'dep_instruct': '<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Deposit via {method}</b>\n\nAmount: <b>{amount}</b>\n\nPlease send exactly this amount to the following {method_str}:\n<code>{target_acc}</code>\n\nAfter sending, click the button below to submit your Transaction ID.',
        'dep_ask_trxid': 'Please enter your <b>10-character</b> Transaction ID (TrxID) below:',
        'dep_wrong_trxid': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Please send the money first, then provide the correct TrxID here.',
        'dep_used_trxid': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> This TrxID has already been used. Please provide a valid TrxID.',
        'dep_success': '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Your deposit request has been sent to the Admin. Please wait for approval.',
        'buy_vpn_btn': 'Nord VPN',
        'buy_hotmail_btn': 'Hotmail',
        'buy_outlook_btn': 'Outlook',
        'buy_outlook_fr_btn': 'Outlook.fr',
        'buy_proxy_btn': 'High-Speed Proxy',
        'buy_out_of_stock': 'Currently out of stock.',
        'buy_ask_vpn': '<tg-emoji emoji-id="5465154440287757794">🛡️</tg-emoji> Which VPN do you want?',
        'buy_ask_hotmail': '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> Store > Hotmail',
        'buy_ask_outlook': '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> Store > Outlook',
        'buy_ask_outlook_fr': '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> Store > Outlook.fr',
        'buy_ask_proxy': '<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> Store > Proxy',
        'buy_no_bal': 'Insufficient balance!',
        'buy_yes': 'Yes',
        'buy_no': 'No',
        'buy_confirm': 'Are you sure you want to buy <b>{cat}({dur})</b>?\nPrice: {price} USDT',
        'buy_cancelled': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Purchase cancelled.',
        'buy_sold_out': 'Out of stock! Please try again later.',
        'buy_success': '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Purchase Successful!</b>\n\n<b>Item:</b> {cat}({dur})\n<b>Price:</b> {price} USDT\n<b>New Balance:</b> {new_bal} USDT\n\n<b>Credentials:</b>\n<code>{creds}</code>',
        'mail_cancelled': 'Mail Inbox check cancelled.',
        'mail_invalid_fmt': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Invalid format. Please use:\n<code>mail|pass|refresh_token|client_id</code>',
        'mail_working': 'Working on it... Main menu restored.',
        'mail_start': '<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> Starting Mail Tracker for {email}...',
        'mail_token_err': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Failed to generate Access Token. Please check your refresh_token and client_id.',
        'mail_new': '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>New Email Received!</b>\n\n<b>Subject:</b> {subject}\n<b>Preview:</b> {preview}\n',
        'mail_code': '\n<b>Code:</b>\n<code>{code}</code>',
        'mail_no_code': '\n<i>(No code detected)</i>',
        'mail_token_exp': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Access token expired or invalid.',
        'mail_not_found': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> No mail found.',
        'dep_soon': 'Convert Balance feature is coming soon!',
        'dep_select_bot': 'Select the bot from which you want to convert balance:',
        'conv_db_err': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Unable to connect to {bot} database at this moment.',
        'conv_no_acc': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Account not found in {bot}.',
        'conv_no_bal': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Insufficient balance in {bot}.',
        'conv_ask_amt': 'Your balance in {bot} is: <b>{bal}</b>\nHow much do you want to bring here?',
        'conv_cancelled': 'Conversion cancelled.',
        'conv_zero_err': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Please enter an amount greater than 0:',
        'conv_max_err': "<tg-emoji emoji-id=\"5215642288071387368\">❌</tg-emoji> You don't have enough balance! (Max: {max_bal})\nPlease enter a valid amount:",
        'conv_success': '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Conversion Successful!</b>\n\nYou have converted <b>{amount}</b> from {bot}.\nYour current balance: <b>{new_bal}</b>'},
 'bn': {'buy_btn': 'পণ্য কিনুন',
        'deposit_btn': 'ডিপোজিট করুন',
        'balance_btn': 'আমার ব্যালেন্স',
        'price_btn': 'মূল্য তালিকা',
        'support_btn': 'কাস্টমার সাপোর্ট',
        'lang_btn': 'ভাষা পরিবর্তন',
        'mail_btn': 'রিপ্লেসমেন্ট',
        'repl_btn': 'রিপ্লেসমেন্ট',
        'welcome': '<tg-emoji emoji-id="5463297803235113601">✨</tg-emoji> স্বাগতম {name}! <tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji>\n<tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji> দ্রুত ও নিরাপদ সেবা পেতে নিচের মেনুটি ব্যবহার করুন!',
        'buy_text': 'আপনি কোন পণ্যটি কিনতে চান?',
        'deposit_text': 'আপনার পছন্দের ডিপোজিট মাধ্যম নির্বাচন করুন:',
        'balance_text': 'আপনার বর্তমান ব্যালেন্স: {balance} ৳',
        'price_text': 'এখানে সকল পণ্যের অফিসিয়াল মূল্য তালিকা দেওয়া হলো।',
        'support_text': '<tg-emoji emoji-id="5237988788164107500">💬</tg-emoji> <b>কাস্টমার সাপোর্ট</b>\n\nযেকোনো সাহায্য বা তথ্যের জন্য আমাদের সাপোর্ট টিমে যোগাযোগ করুন:\n<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> @workstoresuport',
        'lang_text': 'অনুগ্রহ করে আপনার ভাষা নির্বাচন করুন:',
        'mail_text': 'অনুগ্রহ করে আপনার ক্রেডেনশিয়াল নিচের ফরম্যাটে পাঠান:\n<code>mail|pass|refresh_token|client_id</code>',
        'unknown': 'অজানা কমান্ড। অনুগ্রহ করে নিচের মেনু বাটন ব্যবহার করুন।',
        'lang_changed': 'আপনার ভাষা বাংলায় পরিবর্তন করা হয়েছে!',
        'returning': 'ফিরে যাওয়া হচ্ছে...',
        'dep_24h_limit': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> আপনি ২৪ ঘণ্টার মধ্যে মাত্র একবার ডিপোজিট রিকোয়েস্ট পাঠাতে পারবেন। দয়া করে পরে আবার চেষ্টা করুন।',
        'dep_ask_amount': 'আপনি {method} এর মাধ্যমে কত টাকা ডিপোজিট করতে চান?',
        'dep_cancelled': 'ডিপোজিট বাতিল করা হয়েছে।',
        'dep_min_err': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> সর্বনিম্ন ডিপোজিট হলো {min_dep} ৳। দয়া করে সঠিক পরিমাণ লিখুন:',
        'dep_invalid_amt': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> ভুল পরিমাণ। দয়া করে সঠিক সংখ্যা লিখুন:',
        'dep_submit_btn': 'ট্রানজেকশন আইডি দিন',
        'dep_instruct': '<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>ডিপোজিট মাধ্যম: {method}</b>\n\nপরিমাণ: <b>{amount} ৳</b>\n\nদয়া করে নিচের {method_str} এ ঠিক এই পরিমাণ টাকা পাঠান:\n<code>{target_acc}</code>\n\nটাকা পাঠানোর পর নিচের বাটনে ক্লিক করে আপনার Transaction ID দিন।',
        'dep_ask_trxid': 'দয়া করে আপনার <b>১০ সংখ্যার</b> Transaction ID (TrxID) নিচে দিন:',
        'dep_wrong_trxid': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> দয়া করে আগে টাকা পাঠান, তারপর এখানে সঠিক TrxID দিন।',
        'dep_used_trxid': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> এই TrxID টি ইতিমধ্যে ব্যবহার করা হয়েছে। দয়া করে সঠিক TrxID দিন।',
        'dep_success': '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> আপনার ডিপোজিট রিকোয়েস্ট জমা হয়েছে। অনুগ্রহ করে অনুমোদনের জন্য অপেক্ষা করুন।',
        'buy_vpn_btn': 'নর্ড ভিপিএন',
        'buy_hotmail_btn': 'হটমেইল',
        'buy_outlook_btn': 'আউটলুক',
        'buy_outlook_fr_btn': 'আউটলুক.এফআর',
        'buy_proxy_btn': 'হাই-স্পিড প্রক্সি',
        'buy_out_of_stock': 'বর্তমানে কোনো স্টক নেই।',
        'buy_ask_vpn': '<tg-emoji emoji-id="5465154440287757794">🛡️</tg-emoji> আপনি কোন ভিপিএন চান?',
        'buy_ask_hotmail': '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> স্টোর > হটমেইল',
        'buy_ask_outlook': '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> স্টোর > আউটলুক',
        'buy_ask_outlook_fr': '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> স্টোর > আউটলুক.এফআর',
        'buy_ask_proxy': '<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> স্টোর > প্রক্সি',
        'buy_no_bal': 'আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই! দয়া করে ডিপোজিট করুন।',
        'buy_yes': 'হ্যাঁ',
        'buy_no': 'না',
        'buy_confirm': 'আপনি কি <b>{cat}({dur})</b> কিনতে চান?\nমূল্য: {price} ৳',
        'buy_cancelled': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> ক্রয় বাতিল করা হয়েছে।',
        'buy_sold_out': 'দুঃখিত, বর্তমানে এই পণ্যটি স্টক আউট! পরে চেষ্টা করুন।',
        'buy_success': '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>ক্রয় সফল হয়েছে!</b>\n\n<b>পণ্য:</b> {cat}({dur})\n<b>মূল্য:</b> {price} ৳\n<b>বর্তমান ব্যালেন্স:</b> {new_bal} ৳\n\n<b>ক্রেডেনশিয়াল:</b>\n<code>{creds}</code>',
        'mail_cancelled': 'মেইল ইনবক্স চেক বাতিল করা হয়েছে।',
        'mail_invalid_fmt': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> ভুল ফরম্যাট। দয়া করে নিচের ফরম্যাট ব্যবহার করুন:\n<code>mail|pass|refresh_token|client_id</code>',
        'mail_working': 'কাজ চলছে... মেইন মেনুতে ফিরিয়ে নেওয়া হচ্ছে।',
        'mail_start': '<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> {email} এর জন্য মেইল ট্র্যাকার চালু হচ্ছে...',
        'mail_token_err': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> এক্সেস টোকেন তৈরি ব্যর্থ হয়েছে। refresh_token এবং client_id চেক করুন।',
        'mail_new': '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>নতুন ইমেইল পাওয়া গেছে!</b>\n\n<b>বিষয়:</b> {subject}\n<b>প্রিভিউ:</b> {preview}\n',
        'mail_code': '\n<b>কোড:</b>\n<code>{code}</code>',
        'mail_no_code': '\n<i>(কোনো কোড পাওয়া যায়নি)</i>',
        'mail_token_exp': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> এক্সেস টোকেন মেয়াদোত্তীর্ণ বা অবৈধ।',
        'mail_not_found': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো নতুন মেইল পাওয়া যায়নি।',
        'dep_soon': 'কনভার্ট ব্যালেন্স ফিচারটি শীঘ্রই আসছে!',
        'dep_select_bot': 'যে বট থেকে ব্যালেন্স আনতে চান সেটি নির্বাচন করুন:',
        'conv_db_err': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> এই মুহূর্তে {bot} এর ডেটাবেজে সংযোগ করা যাচ্ছে না।',
        'conv_no_acc': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> {bot} এ আপনার অ্যাকাউন্ট পাওয়া যায়নি।',
        'conv_no_bal': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> {bot} এ পর্যাপ্ত ব্যালেন্স নেই।',
        'conv_ask_amt': '{bot} এ আপনার ব্যালেন্স: <b>{bal} ৳</b>\nকত টাকা এখানে নিয়ে আসতে চান?',
        'conv_cancelled': 'কনভার্সন বাতিল করা হয়েছে।',
        'conv_zero_err': '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> দয়া করে ০ এর বেশি পরিমাণ লিখুন:',
        'conv_max_err': "<tg-emoji emoji-id=\"5215642288071387368\">❌</tg-emoji> আপনার অ্যাকাউন্টে পর্যাপ্ত ব্যালেন্স নেই! (সর্বোচ্চ: {max_bal})\nদয়া করে সঠিক পরিমাণ লিখুন:",
        'conv_success': '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>কনভার্সন সফল হয়েছে!</b>\n\nআপনি {bot} থেকে <b>{amount} ৳</b> নিয়ে এসেছেন।\nআপনার বর্তমান ব্যালেন্স: <b>{new_bal} ৳</b>'
    }
}

# ====================================================
# LIVE GMAIL CHECKER SYSTEM (Mails.so & GmailCheckLive)
# ====================================================
GMAIL_CHECK_URL = "https://www.gmailchecklive.com/index.php"
GMAIL_CHECK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.gmailchecklive.com/",
    "Origin": "https://www.gmailchecklive.com",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest"
}

CHECKER_CONFIG_CACHE = {}
CHECKER_CONFIG_LAST_UPDATE = 0

def get_checker_config():
    global CHECKER_CONFIG_CACHE, CHECKER_CONFIG_LAST_UPDATE
    if time.time() - CHECKER_CONFIG_LAST_UPDATE < 10 and CHECKER_CONFIG_CACHE:
        return CHECKER_CONFIG_CACHE
        
    conf = config_collection.find_one({"_id": "gmail_checker_settings"})
    if not conf:
        conf = {
            "_id": "gmail_checker_settings",
            "active_checker": "mails_so",
            "mails_so_api_key": "7fe63f6c-0a3c-4b0f-83d3-caf9ea7f4f6a"
        }
        config_collection.insert_one(conf)
    else:
        changed = False
        if "active_checker" not in conf:
            conf["active_checker"] = "mails_so"
            changed = True
        if "mails_so_api_key" not in conf:
            conf["mails_so_api_key"] = "7fe63f6c-0a3c-4b0f-83d3-caf9ea7f4f6a"
            changed = True
        if changed:
            config_collection.update_one({"_id": "gmail_checker_settings"}, {"$set": {"active_checker": conf["active_checker"], "mails_so_api_key": conf["mails_so_api_key"]}})
            
    CHECKER_CONFIG_CACHE = conf
    CHECKER_CONFIG_LAST_UPDATE = time.time()
    return conf

def parse_gmail_address(raw_line):
    if not raw_line:
        return None
    line = raw_line.strip()
    match = re.search(r"([a-zA-Z0-9._%+-]+@gmail\.com)", line, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    for sep in ["|", ":", ";", "\t", " "]:
        if sep in line:
            clean_user = re.sub(r"[^a-zA-Z0-9._-]", "", line.split(sep, 1)[0].strip())
            if clean_user:
                return f"{clean_user.lower()}@gmail.com"
    clean_user = re.sub(r"[^a-zA-Z0-9._-]", "", line)
    if clean_user:
        return f"{clean_user.lower()}@gmail.com"
    return None

def check_gmail_batch_mails_so(email_list, api_key=None, max_workers=5):
    """
    Checks list of Gmail emails using Mails.so API (from easyincomex.py)
    URL: https://api.mails.so/v1/validate?email={email}
    Header: x-mails-api-key: {api_key}
    Returns dict: {email: True/False} or None if failed
    """
    if not email_list:
        return {}
        
    if not api_key:
        conf = get_checker_config()
        api_key = conf.get("mails_so_api_key", "7fe63f6c-0a3c-4b0f-83d3-caf9ea7f4f6a")
        
    headers = {
        'x-mails-api-key': api_key,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    session = requests.Session()

    def check_single(email):
        url = f'https://api.mails.so/v1/validate?email={email}'
        try:
            r = session.get(url, headers=headers, timeout=12)
            if r.status_code == 200:
                data = r.json()
                res = data.get('data', {}).get('result')
                if res == 'deliverable':
                    return email, True
                elif res == 'undeliverable':
                    return email, False
                else:
                    return email, True
            else:
                print(f"[Mails.so Error] status {r.status_code} for {email}: {r.text[:100]}")
                return email, None
        except Exception as e:
            print(f"[Mails.so Exception] {email}: {e}")
            return email, None

    results = {}
    workers = min(max_workers, len(email_list))
    if workers <= 1:
        for em in email_list:
            e, status = check_single(em)
            if status is None:
                return None
            results[e] = status
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for e, status in executor.map(check_single, email_list):
                if status is None:
                    return None
                results[e] = status
    return results

def check_gmail_batch_gmailchecklive(email_list, max_retries=2):
    """
    Checks list of Gmail emails using https://www.gmailchecklive.com/index.php
    Returns dict: {email: True/False} or None if failed
    """
    if not email_list:
        return {}
    
    payload = {"emails": "\n".join(email_list)}
    
    for attempt in range(max_retries):
        try:
            resp = requests.post(GMAIL_CHECK_URL, headers=GMAIL_CHECK_HEADERS, data=payload, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    res_map = data.get("results", {})
                    return {email: bool(res_map.get(email, False)) for email in email_list}
        except Exception as e:
            print(f"[GmailCheckLive Error] attempt {attempt+1}: {e}")
            time.sleep(1)
            
    return None

def check_gmail_batch_live(email_list, max_retries=2):
    """
    Checks list of Gmail emails using the currently active checker (configured in Admin Panel).
    Returns dict: {email: True/False} or None if checker fails
    True = Good/Live, False = Bad/Verify/Disabled
    """
    if not email_list:
        return {}
        
    conf = get_checker_config()
    active = conf.get("active_checker", "mails_so")
    api_key = conf.get("mails_so_api_key", "7fe63f6c-0a3c-4b0f-83d3-caf9ea7f4f6a")
    
    if active == "mails_so":
        res = check_gmail_batch_mails_so(email_list, api_key=api_key)
        if res is not None:
            return res
        print("[Checker Fallback] Mails.so failed. Trying GmailCheckLive fallback...")
        return check_gmail_batch_gmailchecklive(email_list, max_retries=1)
    else:
        res = check_gmail_batch_gmailchecklive(email_list, max_retries=max_retries)
        if res is not None:
            return res
        print("[Checker Fallback] GmailCheckLive failed. Trying Mails.so fallback...")
        return check_gmail_batch_mails_so(email_list, api_key=api_key)

def get_gmail_checker_markup():
    conf = get_checker_config()
    active = conf.get("active_checker", "mails_so")
    
    m_btn_text = "[ON] Mails.so API" if active == "mails_so" else "[OFF] Mails.so API"
    m_style = "success" if active == "mails_so" else "primary"
    m_icon = "5213406375341731253" if active == "mails_so" else "5431644246450908867"
    
    g_btn_text = "[ON] GmailCheckLive" if active == "gmailchecklive" else "[OFF] GmailCheckLive"
    g_style = "success" if active == "gmailchecklive" else "primary"
    g_icon = "5213406375341731253" if active == "gmailchecklive" else "5249288301659041068"
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(m_btn_text, callback_data="toggle_checker_mails_so", style=m_style, icon_custom_emoji_id=m_icon),
        InlineKeyboardButton(g_btn_text, callback_data="toggle_checker_gmailchecklive", style=g_style, icon_custom_emoji_id=g_icon),
        InlineKeyboardButton("Set Mails.so API Key", callback_data="prompt_set_mails_so_key", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("টেস্ট চেকার (Test Active Checker)", callback_data="test_active_gmail_checker", style="primary", icon_custom_emoji_id="5447410659077661506"),
        InlineKeyboardButton("Back to Store Stock", callback_data="close_checker_menu", style="danger", icon_custom_emoji_id="5220079633533250496")
    )
    return markup

def get_gmail_checker_panel_text():
    conf = get_checker_config()
    active = conf.get("active_checker", "mails_so")
    api_key = conf.get("mails_so_api_key", "7fe63f6c-0a3c-4b0f-83d3-caf9ea7f4f6a")
    
    masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else api_key
    active_name = '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> Mails.so API (easyincomex)' if active == "mails_so" else '<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> GmailCheckLive.com'
    
    return (
        f'<tg-emoji emoji-id="5463289097336405244">⚙️</tg-emoji> <b>Gmail Checker Control Panel / চেকার সেটিংস</b>\n'
        f'━━━━━━━━━━━━━━━━━━━━━\n'
        f'<tg-emoji emoji-id="5213406375341731253">🟢</tg-emoji> <b>বর্তমানে সক্রিয় চেকার:</b> <code>{active_name}</code>\n'
        f'<tg-emoji emoji-id="5463289097336405244">🔑</tg-emoji> <b>Mails.so API Key:</b> <code>{masked_key}</code>\n\n'
        f'<tg-emoji emoji-id="5463297803235113601">📌</tg-emoji> <i>আপনি নিচের যে বাটনে ক্লিক করে চালু (ON) রাখবেন, ইউজাররা জিমেইল কেনার সময় সেই সার্ভার দিয়ে স্বয়ংক্রিয়ভাবে লাইভ ভেরিফাই করা হবে।</i>'
    )

def get_main_menu(lang='en', chat_id=None):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    t = texts.get(lang, texts['en'])
    markup.add(
        KeyboardButton(t['buy_btn'], style="success", icon_custom_emoji_id="5395463407589672312"), KeyboardButton(t['deposit_btn'], style="primary", icon_custom_emoji_id="5332600543963522398"),
        KeyboardButton(t['balance_btn'], style="primary", icon_custom_emoji_id="6170011680831969294"), KeyboardButton(t['price_btn'], style="success", icon_custom_emoji_id="5445150711711018720"),
        KeyboardButton(t.get('support_btn', 'Support'), style="primary", icon_custom_emoji_id="5237988788164107500"), KeyboardButton(t.get('repl_btn', t.get('mail_btn', 'Replacement')), style="success", icon_custom_emoji_id="5212988801441344587")
    )
    if chat_id in ADMIN_IDS:
        markup.add(KeyboardButton('Admin Panel', style="danger", icon_custom_emoji_id="5431644246450908867"))
    return markup

def get_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton('Bot Statistics', style="primary", icon_custom_emoji_id="5348125953090403204"),
        KeyboardButton('Send Broadcast', style="primary", icon_custom_emoji_id="5249288301659041068"),
        KeyboardButton('Manage Balance', style="success", icon_custom_emoji_id="5373174941095050893"),
        KeyboardButton('Ban / Unban User', style="danger", icon_custom_emoji_id="5215642288071387368"),
        KeyboardButton('Payment Settings', style="primary", icon_custom_emoji_id="5463289097336405244"),
        KeyboardButton('Store Stock', style="success", icon_custom_emoji_id="5395463407589672312"),
        KeyboardButton('Replacement Panel', style="danger", icon_custom_emoji_id="5212988801441344587"),
        KeyboardButton('Bad Gmail', style="primary", icon_custom_emoji_id="5215642288071387368"),
        KeyboardButton('Gmail Checker', style="primary", icon_custom_emoji_id="5447410659077661506"),
        KeyboardButton('Get Webhook URL', style="success", icon_custom_emoji_id="5249288301659041068")
    )
    markup.add(KeyboardButton('Return to User Menu', style="primary", icon_custom_emoji_id="5220079633533250496"))
    return markup

def get_ban_management_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🚫 Ban User", callback_data="admin_ban_start", style="danger", icon_custom_emoji_id="5215642288071387368"),
        InlineKeyboardButton("🟢 Unban User", callback_data="admin_unban_start", style="success", icon_custom_emoji_id="5213406375341731253")
    )
    markup.add(
        InlineKeyboardButton("📋 Banned Users List", callback_data="admin_banned_list", style="primary", icon_custom_emoji_id="5348125953090403204"),
        InlineKeyboardButton("🔍 Check User Info", callback_data="admin_ban_check_start", style="primary", icon_custom_emoji_id="5447410659077661506")
    )
    markup.add(
        InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_ban_back", style="primary", icon_custom_emoji_id="5220079633533250496")
    )
    return markup

def send_ban_management_panel(chat_id, message_id=None):
    banned_count = len(BANNED_USERS_CACHE)
    panel_text = (
        f'<tg-emoji emoji-id="5215642288071387368">🚫</tg-emoji> <b>User Ban / Unban Management Panel</b> <tg-emoji emoji-id="5463289097336405244">👮</tg-emoji>\n'
        f'━━━━━━━━━━━━━━━━━━━━━\n'
        f'<tg-emoji emoji-id="5348125953090403204">📊</tg-emoji> <b>Currently Banned Users:</b> <code>{banned_count}</code>\n\n'
        f'Select an action from below, or use slash commands directly:\n'
        f'• <code>/ban &lt;user_id&gt; [reason]</code> - Ban a user instantly\n'
        f'• <code>/unban &lt;user_id&gt;</code> - Unban a user\n'
        f'• <code>/banned</code> - View all banned users\n'
        f'━━━━━━━━━━━━━━━━━━━━━'
    )
    markup = get_ban_management_markup()
    if message_id:
        try:
            bot.edit_message_text(panel_text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            pass
    bot.send_message(chat_id, panel_text, parse_mode="HTML", reply_markup=markup)

def send_banned_list_message(chat_id, message_id=None):
    banned_docs = list(users_collection.find({"is_banned": True}).limit(30))
    if not banned_docs:
        msg = (
            '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>No Banned Users!</b>\n\n'
            'Currently there are no banned users in the database.'
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Back to Ban Panel", callback_data="admin_ban_menu", style="primary", icon_custom_emoji_id="5220079633533250496"))
        if message_id:
            try:
                bot.edit_message_text(msg, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup)
                return
            except Exception:
                pass
        bot.send_message(chat_id, msg, parse_mode="HTML", reply_markup=markup)
        return

    lines = [
        f'<tg-emoji emoji-id="5215642288071387368">🚫</tg-emoji> <b>Banned Users List ({len(banned_docs)}):</b>',
        '━━━━━━━━━━━━━━━━━━━━━'
    ]
    markup = InlineKeyboardMarkup(row_width=1)
    for doc in banned_docs:
        uid = doc.get("chat_id")
        uname = doc.get("username")
        fname = doc.get("first_name", "User")
        reason = doc.get("ban_reason", "Admin decision")
        u_label = f"@{uname}" if uname else fname
        lines.append(f"• <b>{escape(str(fname))}</b> (<code>{uid}</code>) - <i>{escape(str(reason))}</i>")
        markup.add(InlineKeyboardButton(f"🟢 Unban {u_label} ({uid})", callback_data=f"unban_user_{uid}", style="success", icon_custom_emoji_id="5213406375341731253"))

    lines.append('━━━━━━━━━━━━━━━━━━━━━')
    markup.add(InlineKeyboardButton("🔙 Back to Ban Panel", callback_data="admin_ban_menu", style="primary", icon_custom_emoji_id="5220079633533250496"))

    full_text = "\n".join(lines)
    if message_id:
        try:
            bot.edit_message_text(full_text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            pass
    bot.send_message(chat_id, full_text, parse_mode="HTML", reply_markup=markup)

def get_store_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton('Add VPN Stock', style="primary", icon_custom_emoji_id="5465154440287757794"), KeyboardButton('Add Hotmail Stock', style="success", icon_custom_emoji_id="4970246557065544891"))
    markup.add(KeyboardButton('Add Outlook Stock', style="primary", icon_custom_emoji_id="4970246557065544891"), KeyboardButton('Add Outlook.fr Stock', style="success", icon_custom_emoji_id="4970246557065544891"))
    markup.add(KeyboardButton('Add Proxy Stock', style="danger", icon_custom_emoji_id="5249288301659041068"), KeyboardButton('Add Gmail Stock', style="success", icon_custom_emoji_id="4970246557065544891"))
    markup.add(KeyboardButton('Add Gemini Stock', style="primary", icon_custom_emoji_id="5431644246450908867"), KeyboardButton('View Stock', style="success", icon_custom_emoji_id="5348125953090403204"))
    markup.add(KeyboardButton('Replacement Panel', style="danger", icon_custom_emoji_id="5212988801441344587"), KeyboardButton('Bad Gmail', style="primary", icon_custom_emoji_id="5215642288071387368"))
    markup.add(KeyboardButton('Gmail Checker', style="primary", icon_custom_emoji_id="5447410659077661506"))
    markup.add(KeyboardButton('Clear Stock', style="danger", icon_custom_emoji_id="5212992409213872592"), KeyboardButton('Back to Admin', style="primary", icon_custom_emoji_id="5220079633533250496"))
    return markup

def get_payment_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton('Set bKash Number', style="primary", icon_custom_emoji_id="5213403875670765022"),
        KeyboardButton('Set Nagad Number', style="primary", icon_custom_emoji_id="5213403875670765022"),
        KeyboardButton('Set Binance ID', style="primary", icon_custom_emoji_id="5213403875670765022"),
        KeyboardButton('Set Min Deposit', style="success", icon_custom_emoji_id="5373174941095050893")
    )
    markup.add(
        KeyboardButton('Set VPN Price', style="primary", icon_custom_emoji_id="5465154440287757794"),
        KeyboardButton('Set Hotmail Price', style="success", icon_custom_emoji_id="4970246557065544891")
    )
    markup.add(
        KeyboardButton('Set Outlook Price', style="primary", icon_custom_emoji_id="4970246557065544891"),
        KeyboardButton('Set Outlook.fr Price', style="success", icon_custom_emoji_id="4970246557065544891")
    )
    markup.add(
        KeyboardButton('Set Proxy Price', style="danger", icon_custom_emoji_id="5249288301659041068"),
        KeyboardButton('Set Gmail Price', style="success", icon_custom_emoji_id="4970246557065544891")
    )
    markup.add(
        KeyboardButton('Set Gemini Price', style="primary", icon_custom_emoji_id="5431644246450908867"),
        KeyboardButton('Set USDT Address', style="success", icon_custom_emoji_id="5215391376081954505")
    )
    markup.add(
        KeyboardButton('Set USDT Rate', style="primary", icon_custom_emoji_id="5463289097336405244"),
        KeyboardButton('Set Bitget API Keys', style="primary", icon_custom_emoji_id="5463289097336405244")
    )
    markup.add(
        KeyboardButton('Bitget API Status', style="success", icon_custom_emoji_id="5447410659077661506"),
        KeyboardButton('Set Custom Webhook URL', style="primary", icon_custom_emoji_id="5249288301659041068")
    )
    markup.add(
        KeyboardButton('Return to Admin', style="primary", icon_custom_emoji_id="5220079633533250496")
    )
    return markup

def get_cancel_menu(lang='en'):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    cancel_text = 'বাতিল' if lang == 'bn' else 'Cancel'
    markup.add(KeyboardButton(cancel_text, style="danger", icon_custom_emoji_id="5215642288071387368"))
    return markup

def is_back_or_cancel_text(text):
    if not text:
        return False
    t = text.strip()
    return t in [
        '🔙 Back', '❌ Cancel', '↩️ Return to User Menu', '🔙 Back to Admin', '🔙 Return to Admin',
        '❌ বাতিল', '🔙 ফিরে যান', '🔙 ব্যাক', 'ফিরে যান', 'বাতিল', 'Go Back', 'Cancel', 'Back',
        'Return to User Menu', 'Back to Admin', 'Return to Admin', 'Back to Store', 'Return to Store',
        '🔙 Back to Store', '🔙 Return to Store', 'Ban / Unban User', '🚫 Ban / Unban User'
    ]

def prompt_country_selection(chat_id, message_id=None):
    prompt_text = '<tg-emoji emoji-id="5447410659077661506">🌍</tg-emoji> <b>Please Select Your Country</b>'
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Bangladesh", callback_data="select_country_bd", style="success", icon_custom_emoji_id="5222066820411829362"),
        InlineKeyboardButton("Other", callback_data="select_country_other", style="primary", icon_custom_emoji_id="5447410659077661506")
    )
    if message_id:
        try:
            bot.edit_message_text(prompt_text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=markup)
            return
        except Exception:
            pass
    bot.send_message(chat_id, prompt_text, parse_mode="HTML", reply_markup=markup)

def check_user_country_callback(call):
    user_id = call.from_user.id if call.from_user else call.message.chat.id
    if user_id in ADMIN_IDS:
        return True
    if is_user_banned(user_id):
        try:
            bot.answer_callback_query(call.id, "🚫 আপনার অ্যাকাউন্টটি ব্যান করা হয়েছে!", show_alert=True)
        except Exception:
            pass
        return False
    country = get_user_country(user_id)
    if not country:
        try:
            bot.answer_callback_query(call.id, "অনুগ্রহ করে আগে আপনার দেশ নির্বাচন করুন! / Please select your country first!", show_alert=True)
        except Exception:
            pass
        prompt_country_selection(call.message.chat.id)
        return False
    return True

@bot.callback_query_handler(func=lambda call: call.data in ['select_country_bd', 'select_country_other'])
def handle_country_selection(call):
    user_id = call.from_user.id if call.from_user else call.message.chat.id
    if user_id not in ADMIN_IDS and is_user_banned(user_id):
        try:
            bot.answer_callback_query(call.id, "🚫 আপনার অ্যাকাউন্টটি ব্যান করা হয়েছে!", show_alert=True)
        except Exception:
            pass
        return
    first_name = (call.from_user.first_name if call.from_user else None) or "User"
    selected = 'bd' if call.data == 'select_country_bd' else 'other'
    set_user_country(user_id, selected)
    
    bot.answer_callback_query(call.id, "দেশ সংরক্ষিত হয়েছে! / Country saved!")
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    user = users_collection.find_one({"chat_id": user_id})
    balance = (user.get("balance", 0.0) if user else 0.0) or 0.0

    if selected == 'bd':
        bal_str = fmt_bal(balance)
        bal_emojis = format_custom_emoji_number(bal_str)
        welcome_text = (
            f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>স্বাগতম, {first_name}!</b> <tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>আপনার ব্যালেন্স:</b> {bal_emojis} ৳\n'
            f'━━━━━━━━━━━━━━━━━━━━━'
        )
        bot.send_message(user_id, welcome_text, parse_mode="HTML", reply_markup=get_main_menu("bn", user_id))
    else:
        rate = get_usdt_rate()
        bal_usdt = fmt_usdt(balance / rate)
        bal_emojis = format_custom_emoji_number(bal_usdt)
        welcome_text = (
            f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>Welcome, {first_name}!</b> <tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Your Balance:</b> ${bal_emojis} USDT\n'
            f'━━━━━━━━━━━━━━━━━━━━━'
        )
        bot.send_message(user_id, welcome_text, parse_mode="HTML", reply_markup=get_main_menu("en", user_id))

# --- CUSTOM EMOJI ID DETECTOR (FOR ADMINS) ---
@bot.message_handler(func=lambda msg: msg.chat.id in ADMIN_IDS and msg.entities and any(e.type == 'custom_emoji' for e in msg.entities))
def detect_custom_emoji_id(message):
    ids = [e.custom_emoji_id for e in message.entities if e.type == 'custom_emoji']
    if ids:
        reply = '<tg-emoji emoji-id="5463297803235113601">✨</tg-emoji> <b>Custom Emoji IDs Found:</b>\n\n'
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
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Deposit Group Set Successfully!</b>\n\nChat ID: <code>{group_id}</code>', parse_mode="HTML")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    if user_id not in ADMIN_IDS and is_user_banned(user_id):
        send_banned_notice(user_id)
        return

    username = message.chat.username
    first_name = message.chat.first_name or "User"
    
    user = users_collection.find_one({"chat_id": user_id})
    if not user:
        users_collection.insert_one({
            "chat_id": user_id,
            "username": username,
            "first_name": first_name,
            "balance": 0.0,
            "country": None,
            "language": None
        })
        print(f"New user saved: {first_name} ({user_id})")

    country = get_user_country(user_id)
    if not country:
        prompt_country_selection(user_id)
        return

    balance = (user.get("balance", 0.0) if user else 0.0) or 0.0
    lang = "bn" if country == "bd" else "en"
    if country == "bd":
        bal_str = fmt_bal(balance)
        bal_emojis = format_custom_emoji_number(bal_str)
        welcome_text = (
            f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>স্বাগতম, {first_name}!</b> <tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>আপনার ব্যালেন্স:</b> {bal_emojis} ৳\n'
            f'━━━━━━━━━━━━━━━━━━━━━'
        )
    else:
        rate = get_usdt_rate()
        bal_usdt = fmt_usdt(balance / rate)
        bal_emojis = format_custom_emoji_number(bal_usdt)
        welcome_text = (
            f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>Welcome, {first_name}!</b> <tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Your Balance:</b> ${bal_emojis} USDT\n'
            f'━━━━━━━━━━━━━━━━━━━━━'
        )
    bot.send_message(message.chat.id, welcome_text, parse_mode="HTML", reply_markup=get_main_menu(lang, message.chat.id))

@bot.message_handler(commands=['country'])
def country_command(message):
    if message.chat.id not in ADMIN_IDS and is_user_banned(message.chat.id):
        send_banned_notice(message.chat.id)
        return
    prompt_country_selection(message.chat.id)

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.chat.id in ADMIN_IDS:
        bot.send_message(message.chat.id, '<tg-emoji emoji-id="5463289097336405244">⚙️</tg-emoji> <b>Welcome to the Admin Panel!</b>\nPlease choose an option:', parse_mode="HTML", reply_markup=get_admin_menu())
    else:
        lang = get_user_lang(message.chat.id)
        bot.send_message(message.chat.id, texts[lang]['unknown'])

@bot.message_handler(commands=['ban'])
def ban_command(message):
    if message.chat.id not in ADMIN_IDS:
        return
    parts = message.text.strip().split(maxsplit=2)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Usage: <code>/ban &lt;user_id&gt; [optional reason]</code>", parse_mode="HTML")
        return
    try:
        target_uid = int(parts[1])
        reason = parts[2] if len(parts) > 2 else "Admin decision"
        success, err = ban_user(target_uid, reason=reason, banned_by=message.chat.id)
        if success:
            bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>User <code>{target_uid}</code> has been banned!</b>\nReason: {escape(reason)}', parse_mode="HTML")
            send_banned_notice(target_uid)
        else:
            bot.send_message(message.chat.id, f"❌ Failed to ban: {err}")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid User ID. Must be numbers only.")

@bot.message_handler(commands=['unban'])
def unban_command(message):
    if message.chat.id not in ADMIN_IDS:
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Usage: <code>/unban &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    try:
        target_uid = int(parts[1])
        success, err = unban_user(target_uid)
        if success:
            bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>User <code>{target_uid}</code> has been unbanned!</b>', parse_mode="HTML")
            try:
                bot.send_message(target_uid, '<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>আপনার অ্যাকাউন্টটি সফলভাবে আনব্যান করা হয়েছে!</b>\nআপনি এখন বটের সকল সুবিধা ব্যবহার করতে পারবেন।', parse_mode="HTML")
            except Exception:
                pass
        else:
            bot.send_message(message.chat.id, f"❌ Failed to unban: {err}")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid User ID. Must be numbers only.")

@bot.message_handler(commands=['banned'])
def banned_command(message):
    if message.chat.id not in ADMIN_IDS:
        return
    send_banned_list_message(message.chat.id)

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
    elif cat_str in ["Gemini", "Gemini Pro", "Gemini Pro 18 Month"]:
        p = conf.get("gemini_price")
        return float(p) if (p is not None and float(p) > 0) else 70.0
    else:
        p = conf.get("vpn_price")
        return float(p) if (p is not None and float(p) > 0) else 15.0

# --- AVAILABLE PRODUCTS INLINE KEYBOARD MENU ---
def get_available_products_keyboard(country='bd'):
    conf = get_config()
    rate = get_usdt_rate()
    is_bd = (country == 'bd')

    vpn_p = get_product_price("Nord", conf)
    hm_p = get_product_price("Hotmail", conf)
    out_p = get_product_price("Outlook", conf)
    out_fr_p = get_product_price("Outlook.fr", conf)
    proxy_p = get_product_price("Proxy", conf)
    gmail_p = get_product_price("Gmail", conf)
    gemini_p = get_product_price("Gemini", conf)

    if is_bd:
        cat_map = [
            {"cat": "Nord", "name": "নর্ড ভিপিএন (৭ দিন)", "custom_emoji": "5990056785967321926", "price_str": f"{fmt_bal(vpn_p)} ৳", "dur": "7day"},
            {"cat": "Hotmail", "name": "হটমেইল (Hotmail)", "custom_emoji": "5285184156555306745", "price_str": f"{fmt_bal(hm_p)} ৳", "dur": "mail"},
            {"cat": "Outlook", "name": "আউটলুক (Outlook)", "custom_emoji": "5253742260054409879", "price_str": f"{fmt_bal(out_p)} ৳", "dur": "mail"},
            {"cat": "Outlook.fr", "name": "আউটলুক.এফআর", "custom_emoji": "5344008428073280881", "price_str": f"{fmt_bal(out_fr_p)} ৳", "dur": "mail"},
            {"cat": "Proxy", "name": "হাই-স্পিড প্রক্সি", "custom_emoji": "5848067868695991015", "price_str": f"{fmt_bal(proxy_p)} ৳", "dur": "line"},
            {"cat": "Gmail", "name": "জি-মেইল অ্যাকাউন্ট", "custom_emoji": "6118546560897781055", "price_str": f"{fmt_bal(gmail_p)} ৳", "dur": "acc"},
            {"cat": "Gemini", "name": "জেমিনি প্রো (১৮ মাস)", "custom_emoji": "5431644246450908867", "price_str": f"{fmt_bal(gemini_p)} ৳", "dur": "18m"}
        ]
    else:
        cat_map = [
            {"cat": "Nord", "name": "Nord VPN 7 Days", "custom_emoji": "5990056785967321926", "price_str": f"${fmt_usdt(vpn_p/rate)} USDT", "dur": "7day"},
            {"cat": "Hotmail", "name": "Hotmail Mail", "custom_emoji": "5285184156555306745", "price_str": f"${fmt_usdt(hm_p/rate)} USDT", "dur": "mail"},
            {"cat": "Outlook", "name": "Outlook Mail", "custom_emoji": "5253742260054409879", "price_str": f"${fmt_usdt(out_p/rate)} USDT", "dur": "mail"},
            {"cat": "Outlook.fr", "name": "Outlook.fr Mail", "custom_emoji": "5344008428073280881", "price_str": f"${fmt_usdt(out_fr_p/rate)} USDT", "dur": "mail"},
            {"cat": "Proxy", "name": "High-Speed Proxy", "custom_emoji": "5848067868695991015", "price_str": f"${fmt_usdt(proxy_p/rate)} USDT", "dur": "line"},
            {"cat": "Gmail", "name": "Gmail Account", "custom_emoji": "6118546560897781055", "price_str": f"${fmt_usdt(gmail_p/rate)} USDT", "dur": "acc"},
            {"cat": "Gemini", "name": "Gemini Pro 18 Months", "custom_emoji": "5431644246450908867", "price_str": f"${fmt_usdt(gemini_p/rate)} USDT", "dur": "18m"}
        ]

    markup = InlineKeyboardMarkup(row_width=1)
    for item in cat_map:
        cat = item["cat"]
        dur = item["dur"]
        stock = vpn_collection.count_documents(get_stock_query(cat, dur))
        style = "success" if stock > 0 else "danger"
        stock_label = f"স্টক: {stock} টি" if is_bd else f"Stock: {stock}"
        text = f"{item['name']} | {item['price_str']} | {stock_label}"
        cb = "gemini_details" if cat == "Gemini" else f"buy_item_{cat}_{dur}"
        btn_kwargs = {"style": style}
        if item.get("custom_emoji"):
            btn_kwargs["icon_custom_emoji_id"] = item["custom_emoji"]
        markup.add(InlineKeyboardButton(text, callback_data=cb, **btn_kwargs))

    back_text = "ফিরে যান" if is_bd else "Go Back"
    markup.add(InlineKeyboardButton(back_text, callback_data="buy_back_menu", style="primary", icon_custom_emoji_id="5416113713428057601"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data == 'buy_back_menu')
def handle_buy_back(call):
    if not check_user_country_callback(call):
        return
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass

def send_premium_price_list(user_id, lang=None):
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    conf = get_config()
    rate = get_usdt_rate()

    vpn_price = fmt_bal(get_product_price("Nord", conf))
    hm_price = fmt_bal(get_product_price("Hotmail", conf))
    out_price = fmt_bal(get_product_price("Outlook", conf))
    out_fr_price = fmt_bal(get_product_price("Outlook.fr", conf))
    proxy_price = fmt_bal(get_product_price("Proxy", conf))
    gmail_price = fmt_bal(get_product_price("Gmail", conf))
    gemini_price = fmt_bal(get_product_price("Gemini", conf))

    # Live stock counts
    vpn_stock = vpn_collection.count_documents({"category": "Nord", "status": "available"})
    hm_stock = vpn_collection.count_documents({"category": "Hotmail", "status": "available"})
    out_stock = vpn_collection.count_documents({"category": "Outlook", "status": "available"})
    out_fr_stock = vpn_collection.count_documents({"category": "Outlook.fr", "status": "available"})
    proxy_stock = vpn_collection.count_documents({"category": "Proxy", "status": "available"})
    gmail_stock = vpn_collection.count_documents({"category": "Gmail", "status": "available"})
    gemini_stock = vpn_collection.count_documents({"category": "Gemini", "status": "available"})

    if is_bd:
        price_msg = (
            f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>অফিসিয়াল স্টোর মূল্য তালিকা</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n\n'
            f'<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji> <b>নর্ড ভিপিএন (৭ দিন)</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>মূল্য:</b> <code>{vpn_price} ৳</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>স্টক:</b> <code>{vpn_stock} টি এভেইলেবল</code>\n\n'
            f'<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> <b>হটমেইল ক্লিন মেইল</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>মূল্য:</b> <code>{hm_price} ৳</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>স্টক:</b> <code>{hm_stock} টি এভেইলেবল</code>\n\n'
            f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <b>আউটলুক ক্লিন মেইল</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>মূল্য:</b> <code>{out_price} ৳</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>স্টক:</b> <code>{out_stock} টি এভেইলেবল</code>\n\n'
            f'<tg-emoji emoji-id="5344008428073280881">🌐</tg-emoji> <b>আউটলুক.এফআর মেইল</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>মূল্য:</b> <code>{out_fr_price} ৳</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>স্টক:</b> <code>{out_fr_stock} টি এভেইলেবল</code>\n\n'
            f'<tg-emoji emoji-id="5848067868695991015">🔌</tg-emoji> <b>হাই-স্পিড প্রক্সি</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>মূল্য:</b> <code>{proxy_price} ৳</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>স্টক:</b> <code>{proxy_stock} টি এভেইলেবল</code>\n\n'
            f'<tg-emoji emoji-id="6118546560897781055">📨</tg-emoji> <b>জি-মেইল অ্যাকাউন্ট</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>মূল্য:</b> <code>{gmail_price} ৳</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>স্টক:</b> <code>{gmail_stock} টি এভেইলেবল</code>\n\n'
            f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>জেমিনি প্রো (১৮ মাস)</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>মূল্য:</b> <code>{gemini_price} ৳</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>স্টক:</b> <code>{gemini_stock} টি এভেইলেবল</code>\n\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji> <i>অর্ডার করার সাথে সাথে ইনস্ট্যান্ট ডেলিভারি পেয়ে যাবেন!</i>'
        )
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("পণ্য কিনুন", callback_data="buy_cat_open_menu", style="success", icon_custom_emoji_id="5395463407589672312"),
            InlineKeyboardButton("ডিপোজিট করুন", callback_data="open_deposit_menu", style="primary", icon_custom_emoji_id="5332600543963522398")
        )
    else:
        vpn_usdt = fmt_usdt(float(get_product_price("Nord", conf)) / rate)
        hm_usdt = fmt_usdt(float(get_product_price("Hotmail", conf)) / rate)
        out_usdt = fmt_usdt(float(get_product_price("Outlook", conf)) / rate)
        out_fr_usdt = fmt_usdt(float(get_product_price("Outlook.fr", conf)) / rate)
        proxy_usdt = fmt_usdt(float(get_product_price("Proxy", conf)) / rate)
        gmail_usdt = fmt_usdt(float(get_product_price("Gmail", conf)) / rate)
        gemini_usdt = fmt_usdt(float(get_product_price("Gemini", conf)) / rate)

        price_msg = (
            f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>OFFICIAL STORE PRICE LIST</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n\n'
            f'<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji> <b>Nord VPN (7 Days)</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price:</b> <code>${vpn_usdt} USDT</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Stock:</b> <code>{vpn_stock} available</code>\n\n'
            f'<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> <b>Hotmail Clean Mail</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price:</b> <code>${hm_usdt} USDT</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Stock:</b> <code>{hm_stock} available</code>\n\n'
            f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <b>Outlook Clean Mail</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price:</b> <code>${out_usdt} USDT</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Stock:</b> <code>{out_stock} available</code>\n\n'
            f'<tg-emoji emoji-id="5344008428073280881">🌐</tg-emoji> <b>Outlook.fr Mail</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price:</b> <code>${out_fr_usdt} USDT</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Stock:</b> <code>{out_fr_stock} available</code>\n\n'
            f'<tg-emoji emoji-id="5848067868695991015">🔌</tg-emoji> <b>High-Speed Proxy</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price:</b> <code>${proxy_usdt} USDT</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Stock:</b> <code>{proxy_stock} available</code>\n\n'
            f'<tg-emoji emoji-id="6118546560897781055">📨</tg-emoji> <b>Gmail Account</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price:</b> <code>${gmail_usdt} USDT</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Stock:</b> <code>{gmail_stock} available</code>\n\n'
            f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>Gemini Pro 18 Months</b>\n'
            f'┣ <tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price:</b> <code>${gemini_usdt} USDT</code>\n'
            f'┗ <tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Stock:</b> <code>{gemini_stock} available</code>\n\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>Exchange Rate:</b> 1 USDT = {rate} BDT\n'
            f'<tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji> <i>Instant automatic delivery after purchase!</i>'
        )
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Buy Products", callback_data="buy_cat_open_menu", style="success", icon_custom_emoji_id="5395463407589672312"),
            InlineKeyboardButton("Deposit Money", callback_data="open_deposit_menu", style="primary", icon_custom_emoji_id="5332600543963522398")
        )
    bot.send_message(user_id, price_msg, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_open_menu')
def handle_price_buy_click(call):
    if not check_user_country_callback(call):
        return
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    msg_title = '<tg-emoji emoji-id="5395463407589672312">🛒</tg-emoji> <b>সকল পণ্যের তালিকা</b>\nদয়া করে যে পণ্যটি কিনতে চান সেটি নির্বাচন করুন:' if country == 'bd' else '<tg-emoji emoji-id="5395463407589672312">🛒</tg-emoji> <b>Available Products</b>\nPlease select a product to proceed:'
    bot.send_message(
        user_id,
        msg_title,
        reply_markup=get_available_products_keyboard(country),
        parse_mode="HTML"
    )

def get_user_pending_deposit_msg(user_id, is_bd=True):
    pending_tx = transactions_collection.find_one({"user_id": user_id, "status": "pending"})
    if not pending_tx:
        return None, None
        
    p_method = pending_tx.get("method", "Deposit")
    p_amount = pending_tx.get("amount", 0.0)
    p_trx = pending_tx.get("trx_id", "N/A")
    
    if is_bd:
        msg = (
            f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>আপনার পূর্বের ডিপোজিট রিকোয়েস্টটি এখনো পেন্ডিং রয়েছে!</b> <tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'একটি রিকোয়েস্ট পেন্ডিং থাকা অবস্থায় নতুন কোনো ডিপোজিট রিকোয়েস্ট পাঠানো যাবে না।\n\n'
            f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>পেন্ডিং মাধ্যম:</b> {p_method}\n'
            f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>পরিমাণ:</b> <code>{fmt_bal(p_amount)} ৳</code>\n'
            f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <b>TrxID:</b> <code>{p_trx}</code>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5463289097336405244">👮</tg-emoji> <i>এডমিন পূর্বের ট্রানজেকশনটি যাচাই করে অনুমোদন করার পর আপনি পুনরায় নতুন ডিপোজিট করতে পারবেন। দয়া করে অপেক্ষা করুন।</i>'
        )
    else:
        msg = (
            f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>Your previous deposit request is still pending!</b> <tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'You cannot submit a new deposit request while another request is currently pending.\n\n'
            f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Pending Method:</b> {p_method}\n'
            f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Amount:</b> <code>{fmt_bal(p_amount)} BDT</code>\n'
            f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <b>TrxID:</b> <code>{p_trx}</code>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5463289097336405244">👮</tg-emoji> <i>Please wait until your previous request is approved by the Admin before creating a new one.</i>'
        )
    return pending_tx, msg

def send_deposit_panel(user_id, lang='bn'):
    country = get_user_country(user_id) or "bd"
    is_bd = (country == "bd")
    rate = get_usdt_rate()

    pending_tx, p_msg = get_user_pending_deposit_msg(user_id, is_bd)
    if pending_tx:
        bot.send_message(user_id, p_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
        return

    if is_bd:
        dep_prompt = (
            '<tg-emoji emoji-id="5332600543963522398">💳</tg-emoji> <b>স্বয়ংক্রিয় ডিপোজিট / ব্যালেন্স রিচার্জ</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'নিচের যেকোনো মাধ্যম নির্বাচন করে স্বয়ংক্রিয়ভাবে ব্যালেন্স যোগ করুন:\n\n'
            '<tg-emoji emoji-id="6318916225594295727">🔴</tg-emoji> <b>বিকাশ পার্সোনাল</b> (অটো এসএমএস <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>)\n'
            '<tg-emoji emoji-id="6318600635692353874">🟠</tg-emoji> <b>নগদ পার্সোনাল</b> (অটো এসএমএস <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>)\n'
            '<tg-emoji emoji-id="5206584567116352967">🟢</tg-emoji> <b>বিটগেট USDT BEP20</b> (অটো ব্লকচেইন <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>)\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            '<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> <i>পছন্দের মাধ্যমে ক্লিক করুন:</i>'
        )
        inline_markup = InlineKeyboardMarkup(row_width=1)
        inline_markup.add(
            InlineKeyboardButton("বিকাশ পার্সোনাল (অটো এসএমএস)", callback_data="depmethod_bKash", style="success", icon_custom_emoji_id="6318916225594295727"),
            InlineKeyboardButton("নগদ পার্সোনাল (অটো এসএমএস)", callback_data="depmethod_Nagad", style="success", icon_custom_emoji_id="6318600635692353874"),
            InlineKeyboardButton("বিটগেট USDT BEP20 (অটো)", callback_data="depmethod_USDT_BEP20", style="success", icon_custom_emoji_id="5206584567116352967")
        )
    else:
        dep_prompt = (
            f'<tg-emoji emoji-id="5332600543963522398">💳</tg-emoji> <b>INSTANT DEPOSIT / ADD FUNDS</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'Select a payment method below to top up your balance:\n\n'
            f'<tg-emoji emoji-id="5206584567116352967">🟢</tg-emoji> <b>Bitget USDT BEP20</b> (Auto Blockchain <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>)\n'
            f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>Exchange Rate:</b> 1 USDT = {rate} BDT\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> <i>Click an option below to proceed:</i>'
        )
        inline_markup = InlineKeyboardMarkup(row_width=1)
        inline_markup.add(
            InlineKeyboardButton("Bitget USDT BEP20 (Auto Deposit)", callback_data="depmethod_USDT_BEP20", style="success", icon_custom_emoji_id="5206584567116352967"),
            InlineKeyboardButton("bKash Personal", callback_data="depmethod_bKash", style="primary", icon_custom_emoji_id="6318916225594295727"),
            InlineKeyboardButton("Nagad Personal", callback_data="depmethod_Nagad", style="primary", icon_custom_emoji_id="6318600635692353874")
        )
    bot.send_message(user_id, dep_prompt, parse_mode="HTML", reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'open_deposit_menu')
def handle_price_dep_click(call):
    if not check_user_country_callback(call):
        return
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    send_deposit_panel(user_id)

@bot.message_handler(func=lambda message: True)
def handle_menu(message):
    user_id = message.chat.id
    text = (message.text or "").strip()

    # Block banned users immediately from executing any bot commands or messages
    if user_id not in ADMIN_IDS and is_user_banned(user_id):
        send_banned_notice(user_id)
        return

    country = get_user_country(user_id)

    # If non-admin user has not chosen country, block and prompt country selection on ANY button or message!
    if user_id not in ADMIN_IDS and not country:
        prompt_country_selection(user_id)
        return

    lang = get_user_lang(user_id)
    is_bd = (country == 'bd')

    # --- ADMIN PANEL HANDLERS ---
    if user_id in ADMIN_IDS:
        if text in ['Bot Statistics', '📊 Bot Statistics', '📈 Bot Statistics']:
            total_users = users_collection.estimated_document_count() 
            bot.send_message(user_id, f'<tg-emoji emoji-id="5348125953090403204">📊</tg-emoji> <b>Bot Statistics</b>\n\nTotal Users: <b>{total_users}</b>', parse_mode="HTML")
            return
            
        elif text in ['Send Broadcast', '📢 Send Broadcast', '🚀 Send Broadcast']:
            msg = bot.send_message(user_id, "Please send the message you want to broadcast to all users:")
            bot.register_next_step_handler(msg, process_broadcast)
            return

        elif text in ['Get Webhook URL', '🌐 Get Webhook URL', '/url', '/webhook', '🌐 Webhook URL']:
            conf = get_config()
            webhook_url = CURRENT_CLOUDFLARE_URL or conf.get("custom_webhook_url") or "https://sacred-gently-bulk-citizen.trycloudflare.com/api/sms_receiver"
            local_wifi_url = "http://192.168.0.106:5000/api/sms_receiver"

            msg = (
                f'<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> <b>অটো ডিপোজিট Webhook URL & Secret</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'☁️ <b>Cloudflare Webhook (Mobile Data):</b>\n<code>{webhook_url}</code>\n\n'
                f'🏠 <b>Local Wi-Fi Webhook (Same Wi-Fi):</b>\n<code>{local_wifi_url}</code>\n\n'
                f'🔑 <b>Secret Token:</b>\n<code>{SMS_SECRET_TOKEN}</code>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5463289097336405244">📲</tg-emoji> <i><b>পরামর্শ:</b> ফোন এবং পিসি একই ওয়াইফাই রাউটারে থাকলে <b>Local Wi-Fi URL</b> সিলেক্ট করে <b>SAVE & START SERVICE</b> দিলে কখনো ডিসকানেক্ট হবে না! আর বাইরে থাকলে বা সিমের ডাটা ব্যবহার করলে <b>Cloudflare URL</b> ব্যবহার করুন।</i>'
            )
            bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=get_admin_menu())
            return
            
        elif text in ['Ban / Unban User', '🚫 Ban / Unban User', 'Ban/Unban User', '🚫 Ban/Unban User', 'Ban User', 'Unban User']:
            send_ban_management_panel(user_id)
            return
            
        elif text in ['Manage Balance', '💵 Manage Balance', '💎 Manage Balance']:
            msg = bot.send_message(user_id, "Please send the **Chat ID** of the user you want to manage:")
            bot.register_next_step_handler(msg, process_manage_balance_id)
            return
            
        elif text in ['Payment Settings', '⚙️ Payment Settings', '🛠️ Payment Settings']:
            bot.send_message(user_id, '<tg-emoji emoji-id="5463289097336405244">⚙️</tg-emoji> <b>Payment Settings</b>\nSelect an option to configure:', parse_mode="HTML", reply_markup=get_payment_admin_menu())
            return
            
        elif text in ['Return to User Menu', '↩️ Return to User Menu', 'User Menu', 'ইউজার মেনু'] or ('user menu' in text.lower() and 'admin' not in text.lower()):
            bot.send_message(user_id, "Returning to User Menu...", reply_markup=get_main_menu(lang, user_id))
            return
            
        elif text in ['Back to Admin', 'Return to Admin', '🔙 Back to Admin', '🔙 Return to Admin', 'Admin Panel', 'admin panel'] or text.lower().strip() in ['return to admin', 'back to admin']:
            bot.send_message(user_id, "Returning to Admin Panel...", reply_markup=get_admin_menu())
            return
            
        elif text in ['Set bKash Number', '📱 Set bKash Number']:
            msg = bot.send_message(user_id, "Please enter the new bKash Number:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_bkash)
            return

        elif text in ['Set Nagad Number', '🍊 Set Nagad Number']:
            msg = bot.send_message(user_id, "Please enter the new Nagad Number:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_nagad)
            return
            
        elif text in ['Set Binance ID', '🔶 Set Binance ID']:
            msg = bot.send_message(user_id, "Please enter the new Binance ID:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_binance)
            return
            
        elif text in ['Set Min Deposit', '💵 Set Min Deposit']:
            msg = bot.send_message(user_id, "Please enter the new Minimum Deposit amount (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_min_deposit)
            return
            
        elif text in ['Set VPN Price', '🛡️ Set VPN Price']:
            msg = bot.send_message(user_id, "Please enter the new Nord VPN (7day) Price (e.g. 15.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_vpn_price)
            return
            
        elif text in ['Set Hotmail Price', '📧 Set Hotmail Price']:
            msg = bot.send_message(user_id, "Please enter the new Hotmail Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_hotmail_price)
            return
            
        elif text in ['Set Outlook Price', '📧 Set Outlook Price']:
            msg = bot.send_message(user_id, "Please enter the new Outlook Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_outlook_price)
            return
            
        elif text in ['Set Outlook.fr Price', '📧 Set Outlook.fr Price']:
            msg = bot.send_message(user_id, "Please enter the new Outlook.fr Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_outlook_fr_price)
            return
            
        elif text in ['Set Proxy Price', '🔌 Set Proxy Price']:
            msg = bot.send_message(user_id, "Please enter the new Proxy Price (e.g. 10.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_proxy_price)
            return

        elif text in ['Set Gmail Price', '📧 Set Gmail Price']:
            msg = bot.send_message(user_id, "Please enter the new Gmail Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_gmail_price)
            return

        elif text in ['Set Gemini Price', '💎 Set Gemini Price']:
            msg = bot.send_message(user_id, "Please enter the new Gemini Pro Price (e.g. 70.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_gemini_price)
            return

        elif text in ['Set Custom Webhook URL', '🌐 Set Custom Webhook URL']:
            msg = bot.send_message(user_id, "Please enter your custom Webhook URL (e.g. `http://187.124.6.8:5000/api/sms_receiver`):", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_custom_webhook_url)
            return

        elif text in ['Set USDT Address', '💎 Set USDT Address']:
            msg = bot.send_message(user_id, "Please enter your USDT (BEP20) Deposit Address:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_usdt_address)
            return

        elif text in ['Set USDT Rate', '📈 Set USDT Rate']:
            msg = bot.send_message(user_id, "Please enter the USDT to BDT rate (e.g. 120.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_usdt_rate)
            return

        elif text in ['Set Bitget API Keys', '🔑 Set Bitget API Keys']:
            msg = bot.send_message(user_id, "Please enter your Bitget API credentials in format:\n`API_KEY|API_SECRET|PASSPHRASE`", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
            bot.register_next_step_handler(msg, process_set_bitget_api_keys)
            return

        elif text in ['Bitget API Status', '🔍 Bitget API Status', '/bitget']:
            conf = get_config()
            status_res = check_bitget_api_status(conf)
            bot.send_message(user_id, status_res["message"], parse_mode="HTML", reply_markup=get_payment_admin_menu())
            return
            
        elif text in ['📦 Store Stock', 'Store Stock', ' Store Stock', '🏪 Store Stock', '📦 Stock', 'Stock', 'Manage Stock', '📦 Manage Stock', '/stock']:
            bot.send_message(user_id, '<tg-emoji emoji-id="5395463407589672312">🏪</tg-emoji> <b>Store Management</b>\nSelect an option:', reply_markup=get_store_admin_menu(), parse_mode="HTML")
            return
            
        elif text in ['View Stock', '📊 View Stock', '👁️ View Stock', 'Stock List']:
            pipeline = [
                {"$match": {"status": "available"}},
                {"$group": {"_id": "$category", "count": {"$sum": 1}}}
            ]
            results = list(vpn_collection.aggregate(pipeline))
            
            if not results:
                bot.send_message(user_id, '<tg-emoji emoji-id="5348125953090403204">📊</tg-emoji> <b>Current Available Stock:</b>\n\nAll stocks are currently empty (0).', reply_markup=get_store_admin_menu(), parse_mode="HTML")
                return
                
            msg = '<tg-emoji emoji-id="5348125953090403204">📊</tg-emoji> <b>Current Available Stock:</b>\n\n'
            for res in results:
                cat = res['_id']
                count = res['count']
                msg += f'<tg-emoji emoji-id="5463289097336405244">🔹</tg-emoji> <b>{cat}:</b> {count} accounts\n'
            bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=get_store_admin_menu())
            return
            
        elif text in ['Clear Stock', '🗑 Clear Stock', '🗑️ Clear Stock']:
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton('Clear VPN', style="danger", icon_custom_emoji_id="5212992409213872592"), KeyboardButton('Clear Hotmail', style="danger", icon_custom_emoji_id="5212992409213872592"))
            markup.add(KeyboardButton('Clear Outlook', style="danger", icon_custom_emoji_id="5212992409213872592"), KeyboardButton('Clear Outlook.fr', style="danger", icon_custom_emoji_id="5212992409213872592"))
            markup.add(KeyboardButton('Clear Proxy', style="danger", icon_custom_emoji_id="5212992409213872592"), KeyboardButton('Clear Gmail', style="danger", icon_custom_emoji_id="5212992409213872592"))
            markup.add(KeyboardButton('Clear Gemini', style="danger", icon_custom_emoji_id="5212992409213872592"), KeyboardButton('Back to Store', style="primary", icon_custom_emoji_id="5220079633533250496"))
            bot.send_message(user_id, '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>Which stock do you want to delete?</b>\n<i>(Only available unsold stock will be deleted!)</i>', reply_markup=markup, parse_mode="HTML")
            return
            
        elif text in ['Clear VPN', '🗑 Clear VPN']:
            res = vpn_collection.delete_many({"category": "Nord", "status": "available"})
            bot.send_message(user_id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Successfully deleted <b>{res.deleted_count}</b> available VPN accounts.', parse_mode="HTML", reply_markup=get_store_admin_menu())
            return
            
        elif text in ['Clear Hotmail', '🗑 Clear Hotmail']:
            res = vpn_collection.delete_many({"category": "Hotmail", "status": "available"})
            bot.send_message(user_id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Successfully deleted <b>{res.deleted_count}</b> available Hotmail accounts.', parse_mode="HTML", reply_markup=get_store_admin_menu())
            return
            
        elif text in ['Clear Outlook', '🗑 Clear Outlook']:
            res = vpn_collection.delete_many({"category": "Outlook", "status": "available"})
            bot.send_message(user_id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Successfully deleted <b>{res.deleted_count}</b> available Outlook accounts.', parse_mode="HTML", reply_markup=get_store_admin_menu())
            return
            
        elif text in ['Clear Outlook.fr', '🗑 Clear Outlook.fr']:
            res = vpn_collection.delete_many({"category": "Outlook.fr", "status": "available"})
            bot.send_message(user_id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Successfully deleted <b>{res.deleted_count}</b> available Outlook.fr accounts.', parse_mode="HTML", reply_markup=get_store_admin_menu())
            return
            
        elif text in ['Clear Proxy', '🗑 Clear Proxy']:
            res = vpn_collection.delete_many({"category": "Proxy", "status": "available"})
            bot.send_message(user_id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Successfully deleted <b>{res.deleted_count}</b> available Proxy accounts.', parse_mode="HTML", reply_markup=get_store_admin_menu())
            return

        elif text in ['Clear Gmail', '🗑 Clear Gmail']:
            res = vpn_collection.delete_many({"category": "Gmail", "status": "available"})
            bot.send_message(user_id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Successfully deleted <b>{res.deleted_count}</b> available Gmail accounts.', parse_mode="HTML", reply_markup=get_store_admin_menu())
            return

        elif text in ['Clear Gemini', '🗑 Clear Gemini']:
            res = vpn_collection.delete_many({"category": "Gemini", "status": "available"})
            bot.send_message(user_id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Successfully deleted <b>{res.deleted_count}</b> available Gemini Pro links.', parse_mode="HTML", reply_markup=get_store_admin_menu())
            return

        elif text in ['🔍 Gmail Checker', 'Gmail Checker', '⚙️ Gmail Checker', 'Gmail Checker Settings', '/checker', 'চেকার']:
            bot.send_message(user_id, get_gmail_checker_panel_text(), reply_markup=get_gmail_checker_markup(), parse_mode="HTML")
            return
            
        elif text in ['Replacement Panel', '🔄 Replacement Panel', 'রিপ্লেসমেন্ট প্যানেল', '🔄 রিপ্লেসমেন্ট প্যানেল', 'Replacement Requests', 'রিপ্লেসমেন্ট রিকোয়েস্ট']:
            send_admin_replacement_panel(user_id)
            return

        elif text in ['❌ Bad Gmail', 'Bad Gmail', '❌ ব্যাড জিমেইল', 'ব্যাড জিমেইল', '/badgmail']:
            count = bad_gmail_collection.count_documents({})
            pending_count = replacement_requests_collection.count_documents({"status": "pending"})
            markup = InlineKeyboardMarkup(row_width=2)
            markup.add(
                InlineKeyboardButton(f"রিভিউ ({pending_count} টি)", callback_data="badgmail_repl_0", icon_custom_emoji_id="5212988801441344587"),
                InlineKeyboardButton(f"ফাইল ({pending_count} টি)", callback_data="repl_file_menu", icon_custom_emoji_id="4967656361073574498")
            )
            markup.row(
                InlineKeyboardButton("ব্যাড জিমেইল ফাইল", callback_data="badgmail_file", icon_custom_emoji_id="4967656361073574498"),
                InlineKeyboardButton("ক্লিয়ার (Clear)", callback_data="badgmail_clear_prompt", icon_custom_emoji_id="5212992409213872592")
            )
            msg = (
                f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>Bad Gmail & Replacement Management / ব্যাড জিমেইল ও রিপ্লেসমেন্ট</b>\n'
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <b>পেন্ডিং রিপ্লেসমেন্ট দাবি:</b> <b>{pending_count}</b> টি\n'
                f'<tg-emoji emoji-id="5395463407589672312">📦</tg-emoji> <b>মোট ব্যাড জিমেইল জমা:</b> {count:,} টি\n\n'
                f'<tg-emoji emoji-id="5463289097336405244">👇</tg-emoji> নিচের যে কোনো একটি অপশন নির্বাচন করুন:'
            )
            bot.send_message(user_id, msg, reply_markup=markup, parse_mode="HTML")
            return
            
        elif text in ['Back to Store', 'Return to Store', '🔙 Back to Store', '🔙 Return to Store']:
            bot.send_message(user_id, '<tg-emoji emoji-id="5395463407589672312">🏪</tg-emoji> <b>Store Management</b>\nSelect an option:', reply_markup=get_store_admin_menu(), parse_mode="HTML")
            return
            
        elif text in ['Add VPN Stock', '➕ Add VPN Stock']:
            msg_instruct = "Please paste the VPN accounts.\n\n<b>Format 1:</b> <code>mail|pass</code> or <code>mail pass</code> (one per line)\n<b>Format 2:</b>\n<tg-emoji emoji-id=\"4967656361073574498\">📧</tg-emoji> email@domain.com\n<tg-emoji emoji-id=\"5215642288071387368\">🔒</tg-emoji> password"
            msg = bot.send_message(user_id, msg_instruct, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Back to Store'))
            bot.register_next_step_handler(msg, process_add_vpn_stock)
            return

        elif text in ['Add Hotmail Stock', '➕ Add Hotmail Stock']:
            msg_text = "Please send a `.txt` file or paste the Hotmail accounts (one per line).\n\n*(Any format you paste here will be exactly delivered to the user!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Back to Store'))
            bot.register_next_step_handler(msg, process_add_hotmail_stock)
            return
            
        elif text in ['Add Outlook Stock', '➕ Add Outlook Stock']:
            msg_text = "Please send a `.txt` file or paste the Outlook accounts (one per line).\n\n*(Any format you paste here will be exactly delivered to the user!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Back to Store'))
            bot.register_next_step_handler(msg, process_add_outlook_stock)
            return
            
        elif text in ['Add Outlook.fr Stock', '➕ Add Outlook.fr Stock']:
            msg_text = "Please send a `.txt` file or paste the Outlook.fr accounts (one per line).\n\n*(Any format you paste here will be exactly delivered to the user!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Back to Store'))
            bot.register_next_step_handler(msg, process_add_outlook_fr_stock)
            return
            
        elif text in ['Add Proxy Stock', '🔌 Add Proxy Stock', '➕ Add Proxy Stock']:
            msg_text = "Please send a `.txt` file or paste the Proxy lines (one per line).\n\n**Example:**\n`proxy.h143.xyz:8080:704325f5674a36e9100c56b6d360c99b-country-US:c2a15f0dd2d21376`\n\n*(Any format you paste here will be stored and delivered exactly as-is without modification)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Back to Store'))
            bot.register_next_step_handler(msg, process_add_proxy_stock)
            return

        elif text in ['Add Gmail Stock', '📧 Add Gmail Stock', '➕ Add Gmail Stock']:
            msg_text = "Please send a `.txt` or `.xlsx` file, or paste Gmail accounts (one per line).\n\n**Formats Supported:**\n1. `gmail|password` (Text / TXT file)\n2. `.xlsx` file (Col A = Gmail, Col B = Password)"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Back to Store'))
            bot.register_next_step_handler(msg, process_add_gmail_stock)
            return

        elif text in ['Add Gemini Stock', '💎 Add Gemini Stock', '➕ Add Gemini Stock']:
            msg_text = (
                "Please send a `.txt` or `.xlsx` file, or paste Gemini Pro redeem links (one per line).\n\n"
                "**Formats Supported:**\n"
                "1. Direct text or `.txt` file (1 link per line)\n"
                "   Example: `https://serviceactivation.google.com/subscription/new/...`\n"
                "2. `.xlsx` Excel file (Column A = Redeem Link, 1 link per row)"
            )
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Back to Store'))
            bot.register_next_step_handler(msg, process_add_gemini_stock)
            return
            
        elif text in ['Admin Panel', '⚙️ Admin Panel', '⚡ Admin Panel', '👑 Admin Panel', '/admin', 'admin', 'Admin', 'এডমিন প্যানেল'] or text.lower().strip() in ['admin panel', 'admin', '/admin']:
            bot.send_message(user_id, 'Welcome to the Admin Panel! <tg-emoji emoji-id="5463289097336405244">⚙️</tg-emoji>\nPlease choose an option:', reply_markup=get_admin_menu(), parse_mode="HTML")
            return

    # --- REGULAR USER HANDLERS ---
    if text in ['🔙 Back', '🔙 ফিরে যান', '❌ Cancel', '❌ বাতিল', '🔙 ব্যাক'] or is_back_or_cancel_text(text):
        bot.send_message(user_id, texts[lang]['returning'], reply_markup=get_main_menu(lang, user_id))
        return

    # Handle main menu commands
    elif text in [texts['en']['buy_btn'], texts['bn']['buy_btn'], 'Buy', '🛒 Buy Product', '🛒 Buy Products', '🛍️ পণ্য কিনুন', 'পণ্য কিনুন']:
        msg_title = '<tg-emoji emoji-id="5395463407589672312">🛍️</tg-emoji> <b>সকল পণ্যের তালিকা</b>\nদয়া করে যে পণ্যটি কিনতে চান সেটি নির্বাচন করুন:' if is_bd else '<tg-emoji emoji-id="5395463407589672312">🛍️</tg-emoji> <b>Available Products</b>\nPlease select a product to proceed:'
        bot.send_message(
            user_id,
            msg_title,
            reply_markup=get_available_products_keyboard(country or "bd"),
            parse_mode="HTML"
        )
        return
        
    elif text in [texts['en']['deposit_btn'], texts['bn']['deposit_btn'], 'Deposit', '💳 Deposit Money', '💳 ডিপোজিট করুন', 'ডিপোজিট করুন']:
        send_deposit_panel(user_id, lang)
        return
        
    elif text in [texts['en']['balance_btn'], texts['bn']['balance_btn'], 'Balance', '💎 My Balance', '💰 আমার ব্যালেন্স', 'আমার ব্যালেন্স']:
        user = users_collection.find_one({"chat_id": user_id}, {"balance": 1})
        balance = user.get("balance", 0.0) if user else 0.0
        tot_spent, tot_count, tod_spent, tod_count = get_user_buy_stats(user_id)
        if is_bd:
            bal_msg = (
                f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>অ্যাকাউন্ট ব্যালেন্স / ওয়ালেট</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4967667085606912536">👤</tg-emoji> <b>ইউজার আইডি:</b> <code>{user_id}</code>\n'
                f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>বর্তমান ব্যালেন্স:</b> <code>{fmt_bal(balance)} ৳</code>\n'
                f'<tg-emoji emoji-id="5395463407589672312">🛒</tg-emoji> <b>টোটাল বাই:</b> <code>{fmt_bal(tot_spent)} ৳</code> ({tot_count} টি)\n'
                f'<tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>আজকের বাই:</b> <code>{fmt_bal(tod_spent)} ৳</code> ({tod_count} টি)\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <i>ব্যালেন্স রিচার্জ করতে নিচের ডিপোজিট বাটনে ক্লিক করুন!</i>'
            )
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("ডিপোজিট করুন", callback_data="open_deposit_menu", style="primary", icon_custom_emoji_id="5332600543963522398"),
                InlineKeyboardButton("পণ্য কিনুন", callback_data="buy_cat_open_menu", style="success", icon_custom_emoji_id="5395463407589672312")
            )
        else:
            rate = get_usdt_rate()
            bal_usdt = fmt_usdt(balance / rate)
            tot_usdt = fmt_usdt(tot_spent / rate)
            tod_usdt = fmt_usdt(tod_spent / rate)
            bal_msg = (
                f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>USER WALLET / BALANCE</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4967667085606912536">👤</tg-emoji> <b>User ID:</b> <code>{user_id}</code>\n'
                f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>Current Balance:</b> <code>${bal_usdt} USDT</code>\n'
                f'<tg-emoji emoji-id="5395463407589672312">🛒</tg-emoji> <b>Total Buy:</b> <code>${tot_usdt} USDT</code> ({tot_count} items)\n'
                f'<tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Today Buy:</b> <code>${tod_usdt} USDT</code> ({tod_count} items)\n'
                f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>Exchange Rate:</b> 1 USDT = {rate} BDT\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <i>Click Deposit below to top up your balance!</i>'
            )
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("Deposit Money", callback_data="open_deposit_menu", style="primary", icon_custom_emoji_id="5332600543963522398"),
                InlineKeyboardButton("Buy Products", callback_data="buy_cat_open_menu", style="success", icon_custom_emoji_id="5395463407589672312")
            )
        bot.send_message(user_id, bal_msg, parse_mode="HTML", reply_markup=markup)
        return
        
    elif text in [texts['en']['price_btn'], texts['bn']['price_btn'], 'Price', '🏷️ Price List', '💵 Price List', '💵 মূল্য তালিকা', 'মূল্য তালিকা', 'Price List']:
        send_premium_price_list(user_id, lang)
        return
        
    elif text in [texts['en'].get('support_btn', 'Support'), texts['bn'].get('support_btn', 'Support'), 'Support', '🎧 Support', '💬 Support', '💬 Customer Support', '💬 কাস্টমার সাপোর্ট', 'কাস্টমার সাপোর্ট']:
        if is_bd:
            sup_msg = (
                '<tg-emoji emoji-id="5237988788164107500">💬</tg-emoji> <b>২৪/৭ কাস্টমার সাপোর্ট</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                '━━━━━━━━━━━━━━━━━━━━━\n'
                'যেকোনো পণ্য ক্রয়, ডিপোজিট বা সমস্যার জন্য আমাদের অফিসিয়াল সাপোর্ট টিমে যোগাযোগ করুন:\n\n'
                '<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> <b>টেলিগ্রাম সাপোর্ট:</b> @workstoresuport\n'
                '<tg-emoji emoji-id="5213349767672769194">⏰</tg-emoji> <b>সার্ভিস সময়:</b> ২৪/৭ সার্বক্ষণিক একটিভ\n'
                '━━━━━━━━━━━━━━━━━━━━━\n'
                '<tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji> <i>আমরা সবসময় আপনার সেবায় প্রস্তুত!</i>'
            )
            markup = InlineKeyboardMarkup()
            markup.add(
                InlineKeyboardButton("কাস্টমার সাপোর্টে যোগাযোগ", url="https://t.me/workstoresuport", style="primary", icon_custom_emoji_id="5237988788164107500")
            )
        else:
            sup_msg = (
                '<tg-emoji emoji-id="5237988788164107500">💬</tg-emoji> <b>24/7 CUSTOMER SUPPORT</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                '━━━━━━━━━━━━━━━━━━━━━\n'
                'For any product purchase, deposit issues, or questions, contact our support team:\n\n'
                '<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> <b>Telegram Support:</b> @workstoresuport\n'
                '<tg-emoji emoji-id="5213349767672769194">⏰</tg-emoji> <b>Service Time:</b> 24/7 Active\n'
                '━━━━━━━━━━━━━━━━━━━━━\n'
                '<tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji> <i>We are always here to help you!</i>'
            )
            markup = InlineKeyboardMarkup()
            markup.add(
                InlineKeyboardButton("Contact Support", url="https://t.me/workstoresuport", style="primary", icon_custom_emoji_id="5237988788164107500")
            )
        bot.send_message(user_id, sup_msg, parse_mode="HTML", reply_markup=markup)
        return
        
    elif text in [texts['en'].get('repl_btn', ''), texts['bn'].get('repl_btn', ''), texts['en'].get('mail_btn', ''), texts['bn'].get('mail_btn', ''), 'রিপ্লেসমেন্ট', 'Replacement', '🔄 রিপ্লেসমেন্ট', '🔄 Replacement', 'Mail Inbox', 'মেইল ইনবক্স']:
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("Gmail", callback_data="claim_repl_gmail", style="primary", icon_custom_emoji_id="6118546560897781055")
        )
        if user_id in ADMIN_IDS:
            markup.add(
                InlineKeyboardButton("⚙️ এডমিন রিপ্লেসমেন্ট প্যানেল" if is_bd else "⚙️ Admin Replacement Panel", callback_data="repl_panel", style="danger", icon_custom_emoji_id="5212988801441344587")
            )
        if is_bd:
            repl_prompt = (
                '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <b>অ্যাকাউন্ট রিপ্লেসমেন্ট সার্ভিস</b>\n'
                '━━━━━━━━━━━━━━━━━━━━━\n'
                'আমাদের বট থেকে ক্রয়কৃত কোনো জিমেইল অ্যাকাউন্টে সমস্যা থাকলে বা নষ্ট/ভেরিফাই হলে নিচে <b>Gmail</b> বাটনে ক্লিক করে রিপ্লেসমেন্ট রিকোয়েস্ট পাঠান:'
            )
        else:
            repl_prompt = (
                '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <b>Account Replacement Service</b>\n'
                '━━━━━━━━━━━━━━━━━━━━━\n'
                'If any Gmail purchased from our store is dead or verified, click the <b>Gmail</b> button below to submit a replacement claim:'
            )
        bot.send_message(user_id, repl_prompt, parse_mode="HTML", reply_markup=markup)
        return

    elif text in ['Admin Panel', '⚙️ Admin Panel', '⚡ Admin Panel', '👑 Admin Panel', '/admin', 'admin', 'Admin', 'এডমিন প্যানেল'] or text.lower().strip() in ['admin panel', 'admin', '/admin']:
        if user_id in ADMIN_IDS:
            bot.send_message(user_id, 'Welcome to the Admin Panel! <tg-emoji emoji-id="5463289097336405244">⚙️</tg-emoji>\nPlease choose an option:', reply_markup=get_admin_menu(), parse_mode="HTML")
        else:
            bot.send_message(user_id, '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> You are not authorized to access the Admin Panel.', parse_mode="HTML")
        return
        
    else:
        bot.send_message(user_id, texts[lang]['unknown'])

# --- ADMIN CONFIG FUNCTIONS ---
def process_set_bkash(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"bkash": message.text}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0 
    bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> bKash number updated to: <b>{escape(message.text)}</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())

def process_set_nagad(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"nagad": message.text}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Nagad number updated to: <b>{escape(message.text)}</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())

def process_set_binance(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"binance": message.text}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Binance ID updated to: <b>{escape(message.text)}</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
 
def process_set_min_deposit(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"min_deposit": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Min deposit updated to: <b>{val}</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_vpn_price(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"vpn_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> VPN Price updated to: <b>{val} ৳</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())
 
def process_set_hotmail_price(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"hotmail_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Hotmail Price updated to: <b>{val} ৳</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())
 
def process_set_outlook_price(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"outlook_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Outlook Price updated to: <b>{val} ৳</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())
 
def process_set_outlook_fr_price(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"outlook_fr_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Outlook.fr Price updated to: <b>{val} ৳</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_proxy_price(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"proxy_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Proxy Price updated to: <b>{val} ৳</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_gmail_price(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"gmail_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Gmail Price updated to: <b>{val} ৳</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_gemini_price(message):
    if message.text in ['🔙 Return to Admin', '❌ Cancel', '🔙 Back'] or is_back_or_cancel_text(message.text):
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text.strip())
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"gemini_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> Gemini Pro Price updated to: <b>{val} ৳</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_custom_webhook_url(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    url_val = message.text.strip()
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"custom_webhook_url": url_val}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Custom Webhook URL Saved!</b>\n\n<code>{escape(url_val)}</code>', parse_mode="HTML", reply_markup=get_payment_admin_menu())

def process_set_usdt_address(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    addr = message.text.strip()
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"usdt_address": addr}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>USDT (BEP20) Address Updated:</b>\n<code>{escape(addr)}</code>', parse_mode="HTML", reply_markup=get_payment_admin_menu())

def process_set_usdt_rate(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        rate = float(message.text.strip())
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"usdt_rate": rate}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>USDT Rate Updated:</b> 1 USDT = <b>{rate} ৳</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
    except:
        bot.send_message(message.chat.id, '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>Invalid rate.</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())

def process_set_bitget_api_keys(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    parts = message.text.strip().split('|')
    if len(parts) != 3:
        bot.send_message(message.chat.id, '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Invalid format. Please send <code>API_KEY|API_SECRET|PASSPHRASE</code>', parse_mode="HTML", reply_markup=get_payment_admin_menu())
        return
    api_key, api_secret, passphrase = [p.strip() for p in parts]
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {
        "bitget_api_key": api_key,
        "bitget_api_secret": api_secret,
        "bitget_passphrase": passphrase
    }}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Bitget API Credentials Saved Successfully!</b>', parse_mode="HTML", reply_markup=get_payment_admin_menu())

def broadcast_restock_notification(category, added_count):
    """
    Broadcasts rich restock notifications with Telegram Premium custom emojis and a 1-click Buy button
    to all registered members in users_collection when new stock is added.
    """
    if not added_count or added_count <= 0:
        return

    try:
        conf = get_config()
        rate = get_usdt_rate()
        price_bdt = get_product_price(category, conf)
        price_usd = fmt_usdt(price_bdt / rate)

        # Count current available stock for this category
        available_stock = vpn_collection.count_documents({"category": category, "status": "available"})

        # Product metadata definitions
        prod_meta = {
            "Gemini": {
                "title": "Gemini AI Pro 18M",
                "unit": "links",
                "btn_label": "Gemini",
                "callback": "gemini_buy_now",
                "details_callback": "gemini_details"
            },
            "Gmail": {
                "title": "Gmail Accounts",
                "unit": "accounts",
                "btn_label": "Gmail",
                "callback": "buy_item_Gmail_acc",
                "details_callback": None
            },
            "Nord": {
                "title": "Nord VPN 7 Days",
                "unit": "accounts",
                "btn_label": "Nord VPN",
                "callback": "buy_item_Nord_7day",
                "details_callback": None
            },
            "Hotmail": {
                "title": "Hotmail Mail",
                "unit": "mails",
                "btn_label": "Hotmail",
                "callback": "buy_item_Hotmail_mail",
                "details_callback": None
            },
            "Outlook": {
                "title": "Outlook Mail",
                "unit": "mails",
                "btn_label": "Outlook",
                "callback": "buy_item_Outlook_mail",
                "details_callback": None
            },
            "Outlook.fr": {
                "title": "Outlook.fr Mail",
                "unit": "mails",
                "btn_label": "Outlook.fr",
                "callback": "buy_item_Outlook.fr_mail",
                "details_callback": None
            },
            "Proxy": {
                "title": "High-Speed Proxy",
                "unit": "proxies",
                "btn_label": "Proxy",
                "callback": "buy_item_Proxy_line",
                "details_callback": None
            }
        }

        meta = prod_meta.get(category, {
            "title": f"{category} Stock",
            "unit": "items",
            "btn_label": category,
            "callback": "buy_cat_open_menu",
            "details_callback": None
        })

        prod_title = meta["title"]
        unit_name = meta["unit"]
        btn_label = meta["btn_label"]
        buy_cb = meta["callback"]
        details_cb = meta["details_callback"]

        # Exact layout requested by user with Telegram Premium custom emojis
        msg_text = (
            f'<tg-emoji emoji-id="5463289097336405244">➕</tg-emoji> <b>{added_count} Restocked</b> <tg-emoji emoji-id="6170283689700760510">🚀</tg-emoji> <b>{prod_title}</b>\n'
            f'━━━━━━━━━━━━━━\n'
            f'<tg-emoji emoji-id="5463297803235113601">💥</tg-emoji> <b>Fresh batch:</b> {added_count} {unit_name}\n'
            f'<tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Available:</b> {available_stock} {unit_name}\n'
            f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price:</b> ${price_usd} ({fmt_bal(price_bdt)} ৳)\n'
            f'<tg-emoji emoji-id="5463289097336405244">🌟</tg-emoji> <b>Delivery:</b> Instant automatic delivery\n\n'
            f'<tg-emoji emoji-id="5282835135861892082">❗️</tg-emoji> <i>Fresh stock is live now at AIX Store.</i> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>'
        )

        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton(f"Buy {btn_label}", callback_data=buy_cb, style="success", icon_custom_emoji_id="5395463407589672312"))
        if details_cb:
            markup.add(InlineKeyboardButton("Details & Warranty", callback_data=details_cb, style="primary", icon_custom_emoji_id="5431644246450908867"))

        users = list(users_collection.find({}, {"chat_id": 1}))
        success_count = 0
        failed_count = 0

        for u in users:
            cid = u.get("chat_id")
            if not cid:
                continue
            try:
                bot.send_message(cid, msg_text, parse_mode="HTML", reply_markup=markup)
                success_count += 1
                time.sleep(0.04)
            except telebot.apihelper.ApiTelegramException as te:
                failed_count += 1
                if te.result_json and te.result_json.get("parameters", {}).get("retry_after"):
                    time.sleep(te.result_json["parameters"]["retry_after"] + 0.5)
            except Exception:
                failed_count += 1

        group_id = conf.get("deposit_group_id") or -1002720523475
        if group_id:
            try:
                bot.send_message(group_id, msg_text, parse_mode="HTML", reply_markup=markup)
            except Exception:
                pass

        add_app_log(f"📢 RESTOCK BROADCAST: {added_count} {category} ({success_count} sent, {failed_count} failed)")
    except Exception as e:
        print(f"Error in broadcast_restock_notification: {e}")

def prompt_stock_news_broadcast(chat_id, category, added_count, duplicate_count=0, custom_label=None):
    display_name = custom_label or category
    dup_text = f"\n<tg-emoji emoji-id=\"5213134259098761044\">⚠️</tg-emoji> স্কিপ করা ডুপ্লিকেট: <b>{duplicate_count}</b> টি\n" if duplicate_count > 0 else ""

    text = (
        f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>আপনার <code>{added_count}</code> টি {display_name} সফলভাবে স্টকে যুক্ত হয়েছে!</b>\n'
        f'{dup_text}'
        f'━━━━━━━━━━━━━━━━━━━━━\n'
        f'<tg-emoji emoji-id="5249288301659041068">📢</tg-emoji> <b>আপনি কি এটার রিস্টক নিউজ (Broadcast) করতে চান?</b>\n'
        f'━━━━━━━━━━━━━━━━━━━━━'
    )

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Yes (হ্যাঁ)", callback_data=f"stknews_yes:{category}:{added_count}", style="success", icon_custom_emoji_id="5213406375341731253"),
        InlineKeyboardButton("❌ No (না)", callback_data=f"stknews_no:{category}:{added_count}", style="danger", icon_custom_emoji_id="5215642288071387368")
    )
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith(('stknews_yes:', 'stknews_no:')))
def handle_stock_news_choice(call):
    if call.message.chat.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return

    try:
        parts = call.data.split(':', 2)
        action = parts[0]
        category = parts[1]
        count_str = parts[2]
        added_count = int(count_str) if count_str.isdigit() else 0
    except Exception:
        bot.answer_callback_query(call.id, "Invalid data")
        return

    chat_id = call.message.chat.id
    message_id = call.message.message_id

    prod_names = {
        "Nord": "Nord VPN",
        "Hotmail": "Hotmail",
        "Outlook": "Outlook",
        "Outlook.fr": "Outlook.fr",
        "Proxy": "Proxy",
        "Gmail": "Gmail",
        "Gemini": "Gemini Pro"
    }
    p_name = prod_names.get(category, category)

    if action == "stknews_no":
        bot.answer_callback_query(call.id, "নিউজ পাঠানো বাতিল করা হয়েছে।")
        try:
            bot.edit_message_text(
                f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>রিস্টক নিউজ পাঠানো হয়নি।</b>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'স্টকে <b>{added_count}</b> টি <b>{p_name}</b> যুক্ত করা হয়েছে, কিন্তু কোনো ব্রডকাস্ট নিউজ পাঠানো হয়নি।',
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="HTML"
            )
        except Exception:
            pass

    elif action == "stknews_yes":
        bot.answer_callback_query(call.id, "নিউজ ব্রডকাস্ট শুরু হয়েছে!")
        try:
            bot.edit_message_text(
                f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>রিস্টক নিউজ পাঠানো হচ্ছে!</b>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5463297803235113601">🚀</tg-emoji> <b>{added_count}</b> টি <b>{p_name}</b> এর রিস্টক নিউজ সকল ইউজার এবং চ্যানেলে পৌঁছে দেওয়া হচ্ছে...',
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="HTML"
            )
        except Exception:
            pass

        if added_count > 0:
            threading.Thread(target=broadcast_restock_notification, args=(category, added_count), daemon=True).start()

def process_add_proxy_stock(message):
    if message.text in ['🔙 Return to Store', '🔙 Back to Store', '❌ Cancel', '🔙 Back'] or is_back_or_cancel_text(message.text):
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_store_admin_menu())
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
            bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Error reading file: {escape(str(e))}', reply_markup=get_store_admin_menu(), parse_mode="HTML")
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    duplicate_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', line).strip()
        if not line:
            continue
        
        # Skip if already exists in available stock
        if vpn_collection.find_one({"credentials": line, "status": "available"}):
            duplicate_count += 1
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

    if added_count > 0:
        prompt_stock_news_broadcast(message.chat.id, "Proxy", added_count, duplicate_count, "Proxy")
    else:
        reply_msg = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো নতুন Proxy স্টকে যুক্ত হয়নি।'
        if duplicate_count > 0:
            reply_msg += f"\n<tg-emoji emoji-id=\"5213134259098761044\">⚠️</tg-emoji> স্কিপ করা ডুপ্লিকেট: <b>{duplicate_count}</b> টি"
        bot.send_message(message.chat.id, reply_msg, parse_mode="HTML", reply_markup=get_store_admin_menu())

def process_add_vpn_stock(message):
    if message.text in ['🔙 Return to Store', '🔙 Back to Store', '❌ Cancel', '🔙 Back'] or is_back_or_cancel_text(message.text):
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_store_admin_menu())
        return
        
    raw_text = (message.text or "").strip()
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

    if added_count > 0:
        prompt_stock_news_broadcast(message.chat.id, "Nord", added_count, 0, "Nord VPN (7day)")
    else:
        bot.send_message(message.chat.id, '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো VPN অ্যাকাউন্ট স্টকে যুক্ত হয়নি। সঠিক ফরম্যাটে দিন।', parse_mode="HTML", reply_markup=get_store_admin_menu())

def decode_file_content(downloaded_file):
    if downloaded_file.startswith(b'PK\x03\x04'):
        xlsx_lines = parse_xlsx_to_lines(downloaded_file)
        if xlsx_lines:
            return "\n".join(xlsx_lines)
    for encoding in ['utf-8-sig', 'utf-16', 'utf-8', 'cp1252']:
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
            if not row:
                continue
            cells = [str(c).strip() for c in row if c is not None and str(c).strip() != ""]
            if not cells:
                continue
            first_val = cells[0].lower()
            if first_val in ["gmail", "email", "accounts", "account", "অ্যাকাউন্ট", "link", "links", "redeem link", "url", "credentials", "ইমেইল", "রিডিম লিংক", "#", "sl"]:
                continue
            if len(cells) == 1:
                lines.append(cells[0])
            else:
                lines.append("|".join(cells))
        return lines
    except Exception as e:
        print(f"Error parsing xlsx: {e}")
        return []

def process_add_hotmail_stock(message):
    if message.text in ['🔙 Return to Store', '🔙 Back to Store', '❌ Cancel', '🔙 Back'] or is_back_or_cancel_text(message.text):
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_store_admin_menu())
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
            bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Error reading file: {escape(str(e))}', reply_markup=get_store_admin_menu(), parse_mode="HTML")
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    duplicate_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if vpn_collection.find_one({"credentials": line, "status": "available"}):
            duplicate_count += 1
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

    if added_count > 0:
        prompt_stock_news_broadcast(message.chat.id, "Hotmail", added_count, duplicate_count, "Hotmail")
    else:
        reply_msg = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো নতুন Hotmail স্টকে যুক্ত হয়নি।'
        if duplicate_count > 0:
            reply_msg += f"\n<tg-emoji emoji-id=\"5213134259098761044\">⚠️</tg-emoji> স্কিপ করা ডুপ্লিকেট: <b>{duplicate_count}</b> টি"
        bot.send_message(message.chat.id, reply_msg, parse_mode="HTML", reply_markup=get_store_admin_menu())

def process_add_outlook_stock(message):
    if message.text in ['🔙 Return to Store', '🔙 Back to Store', '❌ Cancel', '🔙 Back'] or is_back_or_cancel_text(message.text):
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_store_admin_menu())
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
            bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Error reading file: {escape(str(e))}', reply_markup=get_store_admin_menu(), parse_mode="HTML")
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    duplicate_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if vpn_collection.find_one({"credentials": line, "status": "available"}):
            duplicate_count += 1
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

    if added_count > 0:
        prompt_stock_news_broadcast(message.chat.id, "Outlook", added_count, duplicate_count, "Outlook")
    else:
        reply_msg = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো নতুন Outlook স্টকে যুক্ত হয়নি।'
        if duplicate_count > 0:
            reply_msg += f"\n<tg-emoji emoji-id=\"5213134259098761044\">⚠️</tg-emoji> স্কিপ করা ডুপ্লিকেট: <b>{duplicate_count}</b> টি"
        bot.send_message(message.chat.id, reply_msg, parse_mode="HTML", reply_markup=get_store_admin_menu())

def process_add_outlook_fr_stock(message):
    if message.text in ['🔙 Return to Store', '🔙 Back to Store', '❌ Cancel', '🔙 Back'] or is_back_or_cancel_text(message.text):
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_store_admin_menu())
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
            bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Error reading file: {escape(str(e))}', reply_markup=get_store_admin_menu(), parse_mode="HTML")
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    duplicate_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if vpn_collection.find_one({"credentials": line, "status": "available"}):
            duplicate_count += 1
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

    if added_count > 0:
        prompt_stock_news_broadcast(message.chat.id, "Outlook.fr", added_count, duplicate_count, "Outlook.fr")
    else:
        reply_msg = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো নতুন Outlook.fr স্টকে যুক্ত হয়নি।'
        if duplicate_count > 0:
            reply_msg += f"\n<tg-emoji emoji-id=\"5213134259098761044\">⚠️</tg-emoji> স্কিপ করা ডুপ্লিকেট: <b>{duplicate_count}</b> টি"
        bot.send_message(message.chat.id, reply_msg, parse_mode="HTML", reply_markup=get_store_admin_menu())

def process_add_gmail_stock(message):
    if message.text in ['🔙 Return to Store', '🔙 Back to Store', '❌ Cancel', '🔙 Back'] or is_back_or_cancel_text(message.text):
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_store_admin_menu())
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
            bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Error reading file: {escape(str(e))}', reply_markup=get_store_admin_menu(), parse_mode="HTML")
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    duplicate_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if vpn_collection.find_one({"credentials": line, "status": "available"}):
            duplicate_count += 1
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

    if added_count > 0:
        prompt_stock_news_broadcast(message.chat.id, "Gmail", added_count, duplicate_count, "Gmail")
    else:
        reply_msg = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো নতুন Gmail স্টকে যুক্ত হয়নি।'
        if duplicate_count > 0:
            reply_msg += f"\n<tg-emoji emoji-id=\"5213134259098761044\">⚠️</tg-emoji> স্কিপ করা ডুপ্লিকেট: <b>{duplicate_count}</b> টি"
        bot.send_message(message.chat.id, reply_msg, parse_mode="HTML", reply_markup=get_store_admin_menu())

def process_add_gemini_stock(message):
    if message.text in ['🔙 Return to Store', '🔙 Back to Store', '❌ Cancel', '🔙 Back'] or is_back_or_cancel_text(message.text):
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_store_admin_menu())
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
            bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Error reading file: {escape(str(e))}', reply_markup=get_store_admin_menu(), parse_mode="HTML")
            return
    elif message.text:
        lines = message.text.strip().split('\n')

    added_count = 0
    duplicate_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if vpn_collection.find_one({"credentials": line, "status": "available"}):
            duplicate_count += 1
            continue
        
        docs_to_insert.append({
            "category": "Gemini",
            "duration": "18m",
            "credentials": line,
            "status": "available",
            "timestamp": datetime.now(timezone.utc)
        })
        added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)

    if added_count > 0:
        prompt_stock_news_broadcast(message.chat.id, "Gemini", added_count, duplicate_count, "Gemini AI Pro 18M")
    else:
        reply_msg = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো নতুন Gemini লিংক স্টকে যুক্ত হয়নি।'
        if duplicate_count > 0:
            reply_msg += f"\n<tg-emoji emoji-id=\"5213134259098761044\">⚠️</tg-emoji> স্কিপ করা ডুপ্লিকেট: <b>{duplicate_count}</b> টি"
        bot.send_message(message.chat.id, reply_msg, parse_mode="HTML", reply_markup=get_store_admin_menu())

# --- ADMIN MULTI-STEP FUNCTIONS ---
def process_broadcast(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to User Menu', '↩️ Return to User Menu', 'Bot Statistics', '📊 Bot Statistics', 'Send Broadcast', '📢 Send Broadcast', 'Manage Balance', '💵 Manage Balance', 'Payment Settings', '⚙️ Payment Settings', 'Store Stock', '📦 Store Stock', 'Bad Gmail', 'Gmail Checker']:
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
            
    bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Broadcast Completed!</b>\n\nSuccess: <b>{success}</b>\nFailed: <b>{failed}</b>', parse_mode="HTML", reply_markup=get_admin_menu())

def process_manage_balance_id(message):
    if is_back_or_cancel_text(message.text) or message.text in ['Return to User Menu', '↩️ Return to User Menu', 'Bot Statistics', '📊 Bot Statistics', 'Send Broadcast', '📢 Send Broadcast', 'Manage Balance', '💵 Manage Balance', 'Payment Settings', '⚙️ Payment Settings', 'Store Stock', '📦 Store Stock', 'Bad Gmail', 'Gmail Checker', 'Ban / Unban User', '🚫 Ban / Unban User']:
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
    if is_back_or_cancel_text(message.text) or message.text in ['Return to User Menu', '↩️ Return to User Menu', 'Bot Statistics', '📊 Bot Statistics', 'Send Broadcast', '📢 Send Broadcast', 'Manage Balance', '💵 Manage Balance', 'Payment Settings', '⚙️ Payment Settings', 'Store Stock', '📦 Store Stock', 'Bad Gmail', 'Gmail Checker', 'Ban / Unban User', '🚫 Ban / Unban User']:
        bot.send_message(message.chat.id, "Balance management cancelled.")
        handle_menu(message)
        return

    try:
        amount = float(message.text)
        new_balance = round(current_balance + amount, 2)
        
        users_collection.update_one({"chat_id": target_id}, {"$set": {"balance": new_balance}})
        bot.send_message(message.chat.id, f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Successfully updated balance!</b>\n\nNew Balance: <b>{fmt_bal(new_balance)}</b>', parse_mode="HTML", reply_markup=get_admin_menu())
        
        try:
            bot.send_message(target_id, f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Your balance has been updated by the Admin.</b>\nAmount changed: <b>{"+" if amount > 0 else ""}{amount}</b>\nNew Balance: <b>{fmt_bal(new_balance)}</b>', parse_mode="HTML")
        except:
            pass 
    except ValueError:
        bot.send_message(message.chat.id, " Invalid amount. Must be a number. Cancelled.", reply_markup=get_admin_menu())

# --- BAN / UNBAN ADMIN HANDLERS ---
@bot.callback_query_handler(func=lambda call: call.data.startswith(('admin_ban_', 'unban_user_')))
def handle_ban_admin_callbacks(call):
    if call.message.chat.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Unauthorized", show_alert=True)
        return

    bot.answer_callback_query(call.id)
    chat_id = call.message.chat.id
    data = call.data

    if data == "admin_ban_menu":
        send_ban_management_panel(chat_id, message_id=call.message.message_id)
    elif data == "admin_ban_back":
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(chat_id, "Returning to Admin Panel...", reply_markup=get_admin_menu())
    elif data == "admin_ban_start":
        msg = bot.send_message(
            chat_id,
            '<tg-emoji emoji-id="5215642288071387368">🚫</tg-emoji> <b>Ban User:</b>\n\n'
            'যে ইউজারকে ব্যান করতে চান তার <b>User ID (Chat ID)</b> পাঠান:\n'
            '<i>(বাতিল করতে "Return to Admin" চাপুন)</i>',
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin')
        )
        bot.register_next_step_handler(msg, process_admin_ban_id)
    elif data == "admin_unban_start":
        msg = bot.send_message(
            chat_id,
            '<tg-emoji emoji-id="5213406375341731253">🟢</tg-emoji> <b>Unban User:</b>\n\n'
            'যে ইউজারকে আনব্যান করতে চান তার <b>User ID (Chat ID)</b> পাঠান:\n'
            '<i>(বাতিল করতে "Return to Admin" চাপুন)</i>',
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin')
        )
        bot.register_next_step_handler(msg, process_admin_unban_id)
    elif data == "admin_banned_list":
        send_banned_list_message(chat_id, message_id=call.message.message_id)
    elif data == "admin_ban_check_start":
        msg = bot.send_message(
            chat_id,
            '<tg-emoji emoji-id="5447410659077661506">🔍</tg-emoji> <b>Check User Status:</b>\n\n'
            'যে ইউজারের স্ট্যাটাস দেখতে চান তার <b>User ID (Chat ID)</b> পাঠান:\n'
            '<i>(বাতিল করতে "Return to Admin" চাপুন)</i>',
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin')
        )
        bot.register_next_step_handler(msg, process_admin_check_user_id)
    elif data.startswith("admin_ban_quick_"):
        target_uid_str = data.replace("admin_ban_quick_", "")
        try:
            target_uid = int(target_uid_str)
            msg = bot.send_message(
                chat_id,
                f'<tg-emoji emoji-id="5215642288071387368">🚫</tg-emoji> <b>Banning User <code>{target_uid}</code></b>\n\n'
                f'দয়া করে <b>ব্যান করার কারণ (Ban Reason)</b> লিখুন:\n'
                f'<i>(অথবা "Skip Reason" চাপুন)</i>',
                parse_mode="HTML",
                reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Skip Reason', 'Return to Admin')
            )
            bot.register_next_step_handler(msg, process_admin_ban_reason, target_uid)
        except ValueError:
            bot.answer_callback_query(call.id, "Invalid User ID", show_alert=True)
    elif data.startswith("unban_user_"):
        target_uid_str = data.replace("unban_user_", "")
        try:
            target_uid = int(target_uid_str)
            success, err = unban_user(target_uid)
            if success:
                bot.answer_callback_query(call.id, f"User {target_uid} unbanned!", show_alert=True)
                try:
                    bot.send_message(target_uid, '<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>আপনার অ্যাকাউন্টটি সফলভাবে আনব্যান করা হয়েছে!</b>\nআপনি এখন বটের সকল সুবিধা ব্যবহার করতে পারবেন।', parse_mode="HTML")
                except Exception:
                    pass
                send_banned_list_message(chat_id, message_id=call.message.message_id)
            else:
                bot.answer_callback_query(call.id, f"Error: {err}", show_alert=True)
        except ValueError:
            bot.answer_callback_query(call.id, "Invalid User ID", show_alert=True)

def process_admin_ban_id(message):
    text = (message.text or "").strip()
    if is_back_or_cancel_text(text) or text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu', 'Ban / Unban User', '🚫 Ban / Unban User']:
        bot.send_message(message.chat.id, "Ban operation cancelled.", reply_markup=get_admin_menu())
        return

    try:
        target_uid = int(text)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid User ID. Must be numeric only.", reply_markup=get_admin_menu())
        return

    if target_uid in ADMIN_IDS:
        bot.send_message(message.chat.id, "❌ You cannot ban an Admin!", reply_markup=get_admin_menu())
        return

    target_user = users_collection.find_one({"chat_id": target_uid})
    user_info_str = f"{target_user.get('first_name', 'User')} (@{target_user.get('username', 'N/A')})" if target_user else "Not found in DB (will ban ID anyway)"

    msg = bot.send_message(
        message.chat.id,
        f'<tg-emoji emoji-id="5215642288071387368">🚫</tg-emoji> <b>Target:</b> <code>{target_uid}</code> ({escape(user_info_str)})\n\n'
        f'দয়া করে <b>ব্যান করার কারণ (Ban Reason)</b> লিখুন:\n'
        f'<i>(অথবা "Skip Reason" চাপুন)</i>',
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Skip Reason', 'Return to Admin')
    )
    bot.register_next_step_handler(msg, process_admin_ban_reason, target_uid)

def process_admin_ban_reason(message, target_uid):
    text = (message.text or "").strip()
    if is_back_or_cancel_text(text) or text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel']:
        bot.send_message(message.chat.id, "Ban operation cancelled.", reply_markup=get_admin_menu())
        return

    reason = "Admin decision"
    if text.lower() not in ['skip', 'skip reason', 'ডিফল্ট']:
        reason = text

    success, err = ban_user(target_uid, reason=reason, banned_by=message.chat.id)
    if success:
        bot.send_message(
            message.chat.id,
            f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>User <code>{target_uid}</code> has been banned!</b>\n'
            f'━━━━━━━━━━━━━━━━━━━━━\n'
            f'<b>Reason:</b> {escape(reason)}\n'
            f'━━━━━━━━━━━━━━━━━━━━━',
            parse_mode="HTML",
            reply_markup=get_admin_menu()
        )
        send_banned_notice(target_uid)
    else:
        bot.send_message(message.chat.id, f"❌ Failed to ban: {err}", reply_markup=get_admin_menu())

def process_admin_unban_id(message):
    text = (message.text or "").strip()
    if is_back_or_cancel_text(text) or text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu', 'Ban / Unban User', '🚫 Ban / Unban User']:
        bot.send_message(message.chat.id, "Unban operation cancelled.", reply_markup=get_admin_menu())
        return

    try:
        target_uid = int(text)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid User ID. Must be numeric only.", reply_markup=get_admin_menu())
        return

    success, err = unban_user(target_uid)
    if success:
        bot.send_message(
            message.chat.id,
            f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>User <code>{target_uid}</code> has been unbanned!</b>',
            parse_mode="HTML",
            reply_markup=get_admin_menu()
        )
        try:
            bot.send_message(target_uid, '<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>আপনার অ্যাকাউন্টটি সফলভাবে আনব্যান করা হয়েছে!</b>\nআপনি এখন বটের সকল সুবিধা ব্যবহার করতে পারবেন।', parse_mode="HTML")
        except Exception:
            pass
    else:
        bot.send_message(message.chat.id, f"❌ Failed to unban: {err}", reply_markup=get_admin_menu())

def process_admin_check_user_id(message):
    text = (message.text or "").strip()
    if is_back_or_cancel_text(text) or text in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', '🔙 Back to Admin', 'Cancel', '❌ Cancel', 'Return to User Menu', 'Ban / Unban User', '🚫 Ban / Unban User']:
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    try:
        target_uid = int(text)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid User ID. Must be numeric only.", reply_markup=get_admin_menu())
        return

    user_doc = users_collection.find_one({"chat_id": target_uid})
    is_banned = is_user_banned(target_uid)

    status_str = "🚫 <b>BANNED</b>" if is_banned else "🟢 <b>ACTIVE / NOT BANNED</b>"
    ban_details = ""
    if is_banned and user_doc:
        reason = user_doc.get("ban_reason", "N/A")
        banned_at = user_doc.get("banned_at", "N/A")
        banned_by = user_doc.get("banned_by", "N/A")
        ban_details = f"\n• <b>Ban Reason:</b> {escape(str(reason))}\n• <b>Banned At:</b> {banned_at}\n• <b>Banned By:</b> <code>{banned_by}</code>\n"

    user_details = ""
    if user_doc:
        uname = user_doc.get("username", "N/A")
        fname = user_doc.get("first_name", "N/A")
        bal = user_doc.get("balance", 0.0)
        country = user_doc.get("country", "N/A")
        user_details = (
            f"• <b>Name:</b> {escape(str(fname))}\n"
            f"• <b>Username:</b> @{uname}\n"
            f"• <b>Balance:</b> {fmt_bal(bal)}\n"
            f"• <b>Country:</b> {country}\n"
        )
    else:
        user_details = "<i>User not found in bot database</i>\n"

    markup = InlineKeyboardMarkup(row_width=2)
    if is_banned:
        markup.add(InlineKeyboardButton("🟢 Unban User", callback_data=f"unban_user_{target_uid}", style="success", icon_custom_emoji_id="5213406375341731253"))
    else:
        markup.add(InlineKeyboardButton("🚫 Ban This User", callback_data=f"admin_ban_quick_{target_uid}", style="danger", icon_custom_emoji_id="5215642288071387368"))
    markup.add(InlineKeyboardButton("🔙 Back to Ban Panel", callback_data="admin_ban_menu", style="primary", icon_custom_emoji_id="5220079633533250496"))

    msg_text = (
        f'<tg-emoji emoji-id="5447410659077661506">🔍</tg-emoji> <b>User Check Result:</b> <code>{target_uid}</code>\n'
        f'━━━━━━━━━━━━━━━━━━━━━\n'
        f'• <b>Status:</b> {status_str}\n'
        f'{user_details}'
        f'{ban_details}'
        f'━━━━━━━━━━━━━━━━━━━━━'
    )
    bot.send_message(message.chat.id, msg_text, parse_mode="HTML", reply_markup=markup)


# --- DEPOSIT FLOW FUNCTIONS ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('depmethod_'))
def handle_deposit_method(call):
    if not check_user_country_callback(call):
        return
    bot.answer_callback_query(call.id)
    method = call.data.replace('depmethod_', '')
    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == "bd")
    lang = get_user_lang(user_id)
    
    bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        
    pending_tx, p_msg = get_user_pending_deposit_msg(user_id, is_bd)
    if pending_tx:
        bot.send_message(user_id, p_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
        return
            
    if method == 'USDT_BEP20':
        conf = get_config()
        usdt_rate = conf.get("usdt_rate", 129.0)
        if is_bd:
            ask_amt_msg = (
                f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>USDT (BEP20) ডিপোজিট</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
                f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>এক্সচেঞ্জ রেট:</b> ১ USDT = {usdt_rate} ৳\n'
                f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>সর্বনিম্ন ডিপোজিট:</b> 0.10 USDT ($0.10)\n\n'
                f'কত <b>ইউএসডিটি (USDT)</b> ডিপোজিট করতে চান লিখুন (যেমন: <code>1</code>, <code>5</code>, <code>0.50</code>, <code>10</code>):'
            )
        else:
            ask_amt_msg = (
                f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>USDT (BEP20) Deposit</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
                f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>Exchange Rate:</b> 1 USDT = {usdt_rate} BDT\n'
                f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Minimum Deposit:</b> 0.10 USDT ($0.10)\n\n'
                f'Please enter the amount in <b>USDT ($)</b> you want to deposit (e.g. <code>1</code>, <code>5</code>, <code>0.50</code>, <code>10</code>):'
            )
        msg = bot.send_message(user_id, ask_amt_msg, parse_mode="HTML", reply_markup=get_cancel_menu(lang))
    else:
        msg = bot.send_message(user_id, texts[lang]['dep_ask_amount'].format(method=method), reply_markup=get_cancel_menu(lang))
    bot.register_next_step_handler(msg, process_deposit_amount, method)

def process_deposit_amount(message, method):
    user_id = message.chat.id
    text = message.text.strip()
    country = get_user_country(user_id) or "bd"
    is_bd = (country == "bd")
    lang = get_user_lang(user_id)

    pending_tx, p_msg = get_user_pending_deposit_msg(user_id, is_bd)
    if pending_tx:
        bot.send_message(user_id, p_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
        return
    
    if text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 ব্যাক', '❌ বাতিল', '🔙 ফিরে যান'] or text in texts['en'].values() or text in texts['bn'].values() or is_back_or_cancel_text(text):
        bot.send_message(user_id, texts[lang]['dep_cancelled'], reply_markup=get_main_menu(lang, user_id))
        return
        
    try:
        amount = float(text)
        conf = get_config()
        min_dep = conf.get("min_deposit", 5.0)
        
        if method == 'USDT_BEP20':
            if amount < 0.1:
                err = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> সর্বনিম্ন ডিপোজিট 0.10 USDT।' if is_bd else '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Minimum deposit is 0.10 USDT.'
                bot.send_message(user_id, err, reply_markup=get_main_menu(lang, user_id), parse_mode="HTML")
                return
        else:
            if amount < min_dep:
                bot.send_message(user_id, texts[lang]['dep_min_err'].format(min_dep=min_dep), reply_markup=get_main_menu(lang, user_id))
                return
    except ValueError:
        bot.send_message(user_id, texts[lang]['dep_invalid_amt'], reply_markup=get_main_menu(lang, user_id))
        return

    conf = get_config()
    target_acc = "Not set"
    method_str = "Number"
    
    if method == 'bKash':
        target_acc = conf.get("bkash", "Not set")
        method_str = "bKash Personal Number"
    elif method == 'Nagad':
        target_acc = conf.get("nagad", conf.get("bkash", "Not set"))
        method_str = "Nagad Personal Number"
    elif method == 'Rocket':
        target_acc = conf.get("rocket", conf.get("bkash", "Not set"))
        method_str = "Rocket Personal Number"
    elif method == 'USDT_BEP20':
        target_acc = conf.get("usdt_address", "0x5Dc3A9BfAd702A8f804fECbe7B53c95A91F5283e")
        method_str = "BEP20 Wallet Address"
        
    if method == 'USDT_BEP20':
        usdt_rate = conf.get("usdt_rate", 129.0)
        bdt_equiv = round(amount * usdt_rate, 2)
        if is_bd:
            instruct_msg = (
                f'<tg-emoji emoji-id="5206584567116352967">🟢</tg-emoji> <b>USDT (BEP20) ক্রিপ্টো ডিপোজিট</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>পরিমাণ:</b> <code>{amount} USDT</code> (≈ <b>{bdt_equiv} ৳</b>)\n'
                f'<tg-emoji emoji-id="5463289097336405244">📈</tg-emoji> <b>এক্সচেঞ্জ রেট:</b> ১ USDT = {usdt_rate} ৳\n\n'
                f'দয়া করে আপনার ট্রাস্ট ওয়ালেট বা এক্সচেঞ্জ থেকে নিচের <b>BEP20</b> এড্রেসে ঠিক <code>{amount}</code> USDT পাঠান:\n\n'
                f'<code>{target_acc}</code>\n\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <i>টাকা পাঠানো সম্পন্ন হলে নিচে আপনার Transaction Hash (TxHash) টি পাঠান:</i>'
            )
            ask_msg = "দয়া করে নিচে আপনার Transaction Hash (TxHash) টি লিখুন:"
        else:
            instruct_msg = (
                f'<tg-emoji emoji-id="5206584567116352967">🟢</tg-emoji> <b>USDT (BEP20) CRYPTO DEPOSIT</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Amount:</b> <code>${amount} USDT</code>\n'
                f'<tg-emoji emoji-id="5463289097336405244">📈</tg-emoji> <b>Exchange Rate:</b> 1 USDT = {usdt_rate} BDT\n\n'
                f'Please transfer exactly <code>{amount}</code> USDT (BEP20 network) to the address below:\n\n'
                f'<code>{target_acc}</code>\n\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <i>After sending, please submit your Transaction Hash (TxHash) below:</i>'
            )
            ask_msg = "Please enter your Transaction Hash (TxHash) below:"
    else:
        method_icon = '<tg-emoji emoji-id="6318916225594295727">🔴</tg-emoji>' if method == 'bKash' else '<tg-emoji emoji-id="6318600635692353874">🟠</tg-emoji>'
        if is_bd:
            instruct_msg = (
                f'{method_icon} <b>{method} স্বয়ংক্রিয় ডিপোজিট</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>পরিমাণ:</b> <code>{amount} ৳</code>\n'
                f'<tg-emoji emoji-id="5213403875670765022">📱</tg-emoji> <b>{method} পার্সোনাল নম্বর:</b>\n'
                f'<code>{target_acc}</code>\n\n'
                f'দয়া করে ওপরের নম্বরে সেন্ড মানি করে নিচে আপনার <b>১০ সংখ্যার TrxID</b> দিন।\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <i>টাকা পাওয়ার সাথে সাথে স্বয়ংক্রিয়ভাবে ব্যালেন্স যোগ হয়ে যাবে!</i>'
            )
            ask_msg = f"দয়া করে আপনার {method} এর <b>১০ সংখ্যার TrxID</b> টি নিচে লিখুন:"
        else:
            usdt_rate = conf.get("usdt_rate", 129.0)
            usdt_eq = fmt_usdt(amount / usdt_rate)
            instruct_msg = (
                f'{method_icon} <b>{method} AUTOMATIC DEPOSIT</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Amount:</b> <code>{amount} BDT</code> (≈ ${usdt_eq} USDT)\n'
                f'<tg-emoji emoji-id="5213403875670765022">📱</tg-emoji> <b>{method_str}:</b>\n'
                f'<code>{target_acc}</code>\n\n'
                f'Please Send Money to the number above and enter your <b>10-character TrxID</b> below.\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <i>Your balance will be updated automatically upon SMS verification!</i>'
            )
            ask_msg = f"Please enter your {method} <b>TrxID</b> below:"

    bot.send_message(user_id, instruct_msg, parse_mode="HTML")
    msg = bot.send_message(user_id, ask_msg, parse_mode="HTML", reply_markup=get_cancel_menu(lang))
    bot.register_next_step_handler(msg, process_deposit_final_trxid, method, amount)

def process_deposit_final_trxid(message, method, amount):
    user_id = message.chat.id
    raw_text = message.text.strip()
    text = raw_text.upper() if method != 'USDT_BEP20' else raw_text
    country = get_user_country(user_id) or "bd"
    is_bd = (country == "bd")
    lang = get_user_lang(user_id)

    pending_tx, p_msg = get_user_pending_deposit_msg(user_id, is_bd)
    if pending_tx:
        bot.send_message(user_id, p_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
        return
    
    if raw_text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 ব্যাক', '❌ বাতিল', '🔙 ফিরে যান'] or raw_text in texts['en'].values() or raw_text in texts['bn'].values() or is_back_or_cancel_text(raw_text):
        bot.send_message(user_id, texts[lang]['dep_cancelled'], reply_markup=get_main_menu(lang, user_id))
        return
        
    if method in ['bKash', 'Nagad', 'Rocket'] and len(text) < 8:
        bot.send_message(user_id, texts[lang]['dep_wrong_trxid'], reply_markup=get_main_menu(lang, user_id))
        return
    elif method == 'USDT_BEP20' and len(raw_text) < 10:
        err = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> অবৈধ TxHash দৈর্ঘ্য।' if is_bd else '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Invalid TxHash length.'
        bot.send_message(user_id, err, reply_markup=get_main_menu(lang, user_id))
        return
        
    with TRX_LOCK_GATE.acquire(text):
        # Block ONLY if ALREADY APPROVED
        existing_approved = transactions_collection.find_one({"trx_id": text, "status": "approved"})
        if existing_approved:
            bot.send_message(user_id, texts[lang]['dep_used_trxid'], reply_markup=get_main_menu(lang, user_id))
            return

        conf = get_config()

        # --- BITGET / BSC USDT AUTO VERIFICATION ---
        if method == 'USDT_BEP20':
            ver_res = verify_bitget_usdt_deposit(raw_text, conf)
            if ver_res and ver_res.get("status") == "approved":
                usdt_amt = ver_res.get("usdt_amount") or float(amount)
                usdt_rate = conf.get("usdt_rate", 129.0)
                actual_bdt = round(usdt_amt * usdt_rate, 2)

                transactions_collection.update_one(
                    {"trx_id": raw_text},
                    {"$set": {
                        "trx_id": raw_text,
                        "user_id": user_id,
                        "amount": actual_bdt,
                        "usdt_amount": usdt_amt,
                        "usdt_rate": usdt_rate,
                        "requested_amount": float(amount),
                        "method": "USDT (BEP20)",
                        "status": "approved",
                        "timestamp": datetime.now(timezone.utc),
                        "verified_at": datetime.now(timezone.utc)
                    }},
                    upsert=True
                )

                user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": actual_bdt}}, return_document=True)
                new_balance = user.get("balance", actual_bdt) if user else actual_bdt

                if is_bd:
                    success_msg = (
                        f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>স্বয়ংক্রিয় USDT ডিপোজিট সফল ও অনুমোদিত হয়েছে!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
                        f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> মাধ্যম: <b>বিটগেট USDT (BEP20)</b>\n'
                        f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> জমা দেওয়া USDT: <b>{usdt_amt} USDT</b>\n'
                        f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> এক্সচেঞ্জ রেট: <b>১ USDT = {usdt_rate} ৳</b>\n'
                        f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> যুক্ত হওয়া ব্যালেন্স: <b>{fmt_bal(actual_bdt)} ৳</b>\n'
                        f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> TxHash: <code>{raw_text}</code>\n'
                        f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> বর্তমান ব্যালেন্স: <b>{fmt_bal(new_balance)} ৳</b>'
                    )
                else:
                    new_bal_usdt = fmt_usdt(new_balance / usdt_rate)
                    success_msg = (
                        f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>Auto USDT Deposit Verified & Approved!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
                        f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> Method: <b>Bitget USDT (BEP20)</b>\n'
                        f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> USDT Deposited: <b>${usdt_amt} USDT</b>\n'
                        f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> Exchange Rate: <b>1 USDT = {usdt_rate} BDT</b>\n'
                        f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> Added to Balance: <b>${usdt_amt} USDT</b> (≈ {fmt_bal(actual_bdt)} ৳)\n'
                        f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> TxHash: <code>{raw_text}</code>\n'
                        f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> New Balance: <b>${new_bal_usdt} USDT</b>'
                    )
                bot.send_message(user_id, success_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))

                try:
                    send_group_deposit_notification(user_id, "USDT (BEP20)", actual_bdt, raw_text, is_auto=True)
                except:
                    pass
                add_app_log(f"✅ AUTO BITGET VERIFIED: {usdt_amt} USDT ({fmt_bal(actual_bdt)} ৳) by User {user_id}")
                return
            else:
                target_acc = conf.get("usdt_address", "0x5Dc3A9BfAd702A8f804fECbe7B53c95A91F5283e")
                if is_bd:
                    reject_msg = (
                        f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>অবৈধ বা অনিশ্চিত TxHash!</b> <tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji>\n\n'
                        f'প্রদত্ত ট্রানজেকশন আইডি (TxHash) বিএসসি ব্লকচেইনে পাওয়া যায়নি বা আমাদের ঠিকানায় সফল ডিপোজিট পাওয়া যায়নি।\n\n'
                        f'<tg-emoji emoji-id="5215391376081954505">📍</tg-emoji> <b>আমাদের BEP20 অ্যাড্রেস:</b>\n<code>{target_acc}</code>\n\n'
                        f'দয়া করে আপনার ট্রানজেকশন চেক করে সঠিক TxHash টি দিন।'
                    )
                else:
                    reject_msg = (
                        f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>Invalid or Unverified TxHash!</b> <tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji>\n\n'
                        f'The submitted Transaction ID (TxHash) was not found on the BSC Blockchain or does not match a successful deposit to our address.\n\n'
                        f'<tg-emoji emoji-id="5215391376081954505">📍</tg-emoji> <b>Our BEP20 Address:</b>\n<code>{target_acc}</code>\n\n'
                        f'Please check your transaction on BscScan/Bitget and submit the correct TxHash again.'
                    )
                bot.send_message(user_id, reject_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
                return

        # Check if SMS already claimed
        already_claimed = received_sms_collection.find_one({"trx_id": text, "status": "claimed"})
        if already_claimed:
            bot.send_message(user_id, texts[lang]['dep_used_trxid'], reply_markup=get_main_menu(lang, user_id))
            return

        # ATOMIC CLAIM from received_sms_collection
        # Only the thread that transitions from 'unclaimed' to 'claimed' can proceed!
        claimed_sms = received_sms_collection.find_one_and_update(
            {"trx_id": text, "status": "unclaimed"},
            {"$set": {"status": "claimed", "claimed_by": user_id, "claimed_at": datetime.now(timezone.utc)}},
            return_document=True
        )
        if claimed_sms:
            actual_amount = float(claimed_sms.get("amount", amount))
            sms_method = claimed_sms.get("method", method)
            req_amount = float(amount)

            transactions_collection.update_one(
                {"trx_id": text},
                {"$set": {
                    "trx_id": text,
                    "user_id": user_id,
                    "amount": actual_amount,
                    "requested_amount": req_amount,
                    "method": sms_method,
                    "status": "approved",
                    "timestamp": datetime.now(timezone.utc),
                    "verified_at": datetime.now(timezone.utc)
                }},
                upsert=True
            )

            user = users_collection.find_one_and_update({"chat_id": user_id}, {"$inc": {"balance": actual_amount}}, return_document=True)
            new_balance = user.get("balance", actual_amount) if user else actual_amount

            if is_bd:
                note_str = f"\nঅনুরোধকৃত পরিমাণ: <b>{fmt_bal(req_amount)} ৳</b>" if req_amount != actual_amount else ""
                success_msg = (
                    f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>স্বয়ংক্রিয় ডিপোজিট সফল ও অনুমোদিত হয়েছে!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
                    f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> মাধ্যম: <b>{sms_method}</b>{note_str}\n'
                    f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> যুক্ত হওয়া ব্যালেন্স: <b>{fmt_bal(actual_amount)} ৳</b>\n'
                    f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> TrxID: <code>{text}</code>\n'
                    f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> বর্তমান ব্যালেন্স: <b>{fmt_bal(new_balance)} ৳</b>'
                )
            else:
                usdt_rate = conf.get("usdt_rate", 129.0)
                added_usdt = fmt_usdt(actual_amount / usdt_rate)
                new_bal_usdt = fmt_usdt(new_balance / usdt_rate)
                note_str = f"\nRequested Amount: <b>{fmt_bal(req_amount)} BDT</b>" if req_amount != actual_amount else ""
                success_msg = (
                    f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>Auto Deposit Verified & Approved!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
                    f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> Method: <b>{sms_method}</b>{note_str}\n'
                    f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> Actual Amount Added: <b>${added_usdt} USDT</b> (≈ {fmt_bal(actual_amount)} ৳)\n'
                    f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> TrxID: <code>{text}</code>\n'
                    f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> New Balance: <b>${new_bal_usdt} USDT</b>'
                )
            bot.send_message(user_id, success_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))

            try:
                send_group_deposit_notification(user_id, sms_method, actual_amount, text, is_auto=True)
            except:
                pass
            add_app_log(f"✅ VERIFIED & CLAIMED: {sms_method} {fmt_bal(actual_amount)} ৳ (TrxID: {text}) by User {user_id}")
            return

        # If SMS is delayed or not received yet:
        # Check if already in transactions
        existing_tx = transactions_collection.find_one({"trx_id": text})
        if existing_tx:
            if existing_tx.get("status") == "approved":
                bot.send_message(user_id, texts[lang]['dep_used_trxid'], reply_markup=get_main_menu(lang, user_id))
                return
            elif existing_tx.get("status") == "pending":
                # Already submitted as pending, don't duplicate notifications
                pending_tx, p_msg = get_user_pending_deposit_msg(user_id, is_bd)
                if p_msg:
                    bot.send_message(user_id, p_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
                return

        transactions_collection.update_one(
            {"trx_id": text},
            {"$set": {
                "trx_id": text,
                "user_id": user_id,
                "amount": float(amount),
                "method": method,
                "status": "pending",
                "timestamp": datetime.now(timezone.utc)
            }},
            upsert=True
        )
        add_app_log(f"⏳ PENDING CLAIM: TrxID {text} by User {user_id} (Waiting for SMS)")

        conf = get_config()
        if method == 'bKash':
            target_acc = conf.get("bkash", "Not set")
        elif method == 'Nagad':
            target_acc = conf.get("nagad", conf.get("bkash", "Not set"))
        elif method == 'Rocket':
            target_acc = conf.get("rocket", conf.get("bkash", "Not set"))
        else:
            target_acc = conf.get("usdt_address", "BEP20")

        if is_bd:
            pending_msg = (
                f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>অটো ডিপোজিটে সাময়িক সমস্যা হয়েছে!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>আপনার ডিপোজিট রিকোয়েস্টটি গ্রহণ করা হয়েছে এবং বর্তমানে পেন্ডিং রয়েছে।</b>\n\n'
                f'<tg-emoji emoji-id="5463289097336405244">👮</tg-emoji> <b>এডমিন রিভিউ:</b>\n'
                f'অটো ভেরিফিকেশনে বিলম্ব হওয়ায় এডমিন ম্যানুয়ালি আপনার ট্রানজেকশনটি যাচাই করে <b>৬ ঘণ্টার মধ্যে</b> ব্যালেন্স যোগ করে দেবে।\n\n'
                f'<tg-emoji emoji-id="5282835135861892082">❗️</tg-emoji> <i>রিকোয়েস্টটি ইতিমধ্যে এডমিনের কাছে চলে গেছে, তাই <b>সাপোর্ট আইডিতে মেসেজ বা এসএমএস করার প্রয়োজন নেই</b>। দয়া করে ধৈর্য ধরে অপেক্ষা করুন।</i>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>ডিপোজিট মাধ্যম:</b> {method}\n'
                f'<tg-emoji emoji-id="5215391376081954505">📱</tg-emoji> <b>প্রেরিত নম্বর:</b> <code>{target_acc}</code>\n'
                f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>ডিপোজিট পরিমাণ:</b> <code>{fmt_bal(amount)} ৳</code>\n'
                f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <b>TrxID:</b> <code>{text}</code>\n'
                f'<tg-emoji emoji-id="5215327832040811010">⏱️</tg-emoji> <b>স্ট্যাটাস:</b> <code>ম্যানুয়াল রিভিউ পেন্ডিং (Pending)</code>\n'
                f'━━━━━━━━━━━━━━━━━━━━━'
            )
        else:
            pending_msg = (
                f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>Auto-Deposit Temporary Delay!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>Your deposit request has been received and is currently pending.</b>\n\n'
                f'<tg-emoji emoji-id="5463289097336405244">👮</tg-emoji> <b>Admin Review:</b>\n'
                f'Due to a temporary auto-verification delay, our Admin will review your transaction manually and credit your wallet within <b>6 hours</b>.\n\n'
                f'<tg-emoji emoji-id="5282835135861892082">❗️</tg-emoji> <i>Your request has already reached the Admin team, so <b>there is NO need to message support ID</b>. Please wait patiently.</i>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Deposit Method:</b> {method}\n'
                f'<tg-emoji emoji-id="5215391376081954505">📱</tg-emoji> <b>Sent To:</b> <code>{target_acc}</code>\n'
                f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Deposit Amount:</b> <code>{fmt_bal(amount)} BDT</code>\n'
                f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <b>TrxID:</b> <code>{text}</code>\n'
                f'<tg-emoji emoji-id="5215327832040811010">⏱️</tg-emoji> <b>Status:</b> <code>Manual Review Pending</code>\n'
                f'━━━━━━━━━━━━━━━━━━━━━'
            )
        bot.send_message(user_id, pending_msg, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))

        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("Approve", callback_data=f"gdep_app_{text}", icon_custom_emoji_id="5213406375341731253"),
            InlineKeyboardButton("Reject", callback_data=f"gdep_rej_{text}", icon_custom_emoji_id="5215642288071387368")
        )

        admin_pending_log = (
            f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>NEW DEPOSIT REQUEST (Pending Verification)</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
            f'<tg-emoji emoji-id="4967667085606912536">👤</tg-emoji> <b>User ID:</b> <code>{user_id}</code>\n'
            f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Method:</b> <b>{method}</b>\n'
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Amount:</b> <b>{fmt_bal(amount)} ৳</b>\n'
            f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <b>TrxID:</b> <code>{text}</code>'
        )
        try:
            bot.send_message(-1002720523475, admin_pending_log, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            print(f"Error sending group pending notification: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('gdep_'))
def handle_group_deposit_approval(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split('_')
    if len(parts) < 3:
        return
    action = parts[1]
    trx_id = str(parts[2]).strip().upper()
    admin_name = call.from_user.first_name or "Admin"
    
    with TRX_LOCK_GATE.acquire(trx_id):
        if action == 'app':
            # ATOMIC COMPARE-AND-SET: Only transitions if currently 'pending'
            tx = transactions_collection.find_one_and_update(
                {"trx_id": trx_id, "status": "pending"},
                {"$set": {"status": "approved", "verified_at": datetime.now(timezone.utc), "approved_by": admin_name}},
                return_document=False
            )
            if not tx:
                bot.edit_message_text('<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> This transaction was already approved or processed.', chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
                return
                
            target_id = tx['user_id']
            amount = float(tx.get('amount', 0.0))
            method = tx.get('method', 'bKash')
            
            received_sms_collection.update_one({"trx_id": trx_id}, {"$set": {"status": "claimed", "claimed_by": target_id, "claimed_at": datetime.now(timezone.utc)}})
            
            user = users_collection.find_one_and_update({"chat_id": target_id}, {"$inc": {"balance": amount}}, return_document=True)
            new_balance = user.get("balance", amount) if user else amount
            
            approved_msg = (
                f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>DEPOSIT APPROVED BY ADMIN!</b> <tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>\n\n'
                f'<tg-emoji emoji-id="4967667085606912536">👤</tg-emoji> <b>User:</b> <a href="tg://user?id={target_id}">{target_id}</a> (<code>{target_id}</code>)\n'
                f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Method:</b> <b>{method}</b>\n'
                f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Amount Added:</b> <b>{fmt_bal(amount)} ৳</b>\n'
                f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <b>TrxID:</b> <code>{trx_id}</code>\n'
                f'<tg-emoji emoji-id="5463289097336405244">👮</tg-emoji> <b>Approved By:</b> {admin_name}'
            )
            bot.edit_message_text(approved_msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
            
            try:
                bot.send_message(
                    target_id,
                    f'<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>Deposit Approved!</b>\n\n'
                    f'Method: <b>{method}</b>\n'
                    f'Amount Added: <b>{fmt_bal(amount)} ৳</b>\n'
                    f'TrxID: <code>{trx_id}</code>\n'
                    f'New Balance: <b>{fmt_bal(new_balance)} ৳</b>',
                    parse_mode="HTML"
                )
            except:
                pass
                
        elif action == 'rej':
            tx = transactions_collection.find_one_and_update(
                {"trx_id": trx_id, "status": "pending"},
                {"$set": {"status": "rejected", "rejected_at": datetime.now(timezone.utc), "rejected_by": admin_name}},
                return_document=False
            )
            if not tx:
                bot.edit_message_text('<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> This transaction was already processed.', chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
                return
                
            target_id = tx['user_id']
            amount = float(tx.get('amount', 0.0))
            method = tx.get('method', 'bKash')
            
            rejected_msg = (
                f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>DEPOSIT REJECTED BY ADMIN</b> <tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji>\n\n'
                f'<tg-emoji emoji-id="4967667085606912536">👤</tg-emoji> <b>User:</b> <a href="tg://user?id={target_id}">{target_id}</a> (<code>{target_id}</code>)\n'
                f'<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>Method:</b> <b>{method}</b>\n'
                f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Amount:</b> <b>{fmt_bal(amount)} ৳</b>\n'
                f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <b>TrxID:</b> <code>{trx_id}</code>\n'
                f'<tg-emoji emoji-id="5463289097336405244">👮</tg-emoji> <b>Rejected By:</b> {admin_name}'
            )
            bot.edit_message_text(rejected_msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
            
            try:
                bot.send_message(target_id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>Deposit Request Rejected!</b>\n\nTrxID: <code>{trx_id}</code>\nআপনার ট্রানজেকশন রিকোয়েস্টটি বাতিল করা হয়েছে।', parse_mode="HTML")
            except:
                pass

# --- STORE & BUY FLOW FUNCTIONS ---
@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_vpn')
def handle_buy_vpn_list(call):
    if not check_user_country_callback(call):
        return
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
        
        btn_text = f"{cat} ({dur}) | {vpn_price} ৳ | Stock: {stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data, icon_custom_emoji_id="5465154440287757794"))
        
    bot.edit_message_text(texts[lang]['buy_ask_vpn'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_hotmail')
def handle_buy_hotmail_list(call):
    if not check_user_country_callback(call):
        return
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
        
        btn_text = f"{cat} | {hm_price} ৳ | Stock: {stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data, icon_custom_emoji_id="4967656361073574498"))
        
    bot.edit_message_text(texts[lang]['buy_ask_hotmail'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_outlook')
def handle_buy_outlook_list(call):
    if not check_user_country_callback(call):
        return
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
        
        btn_text = f"{cat} | {out_price} ৳ | Stock: {stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data, icon_custom_emoji_id="4967656361073574498"))
        
    bot.edit_message_text(texts[lang]['buy_ask_outlook'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_outlookfr')
def handle_buy_outlookfr_list(call):
    if not check_user_country_callback(call):
        return
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
        
        btn_text = f"{cat} | {out_fr_price} ৳ | Stock: {stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data, icon_custom_emoji_id="4967656361073574498"))
        
    bot.edit_message_text(texts[lang]['buy_ask_outlook_fr'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

@bot.callback_query_handler(func=lambda call: call.data == 'buy_cat_proxy')
def handle_buy_proxy_list(call):
    if not check_user_country_callback(call):
        return
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
    for prx in available_proxies:
        cat = prx['_id']['category']
        dur = prx['_id']['duration']
        stock = prx['stock']
        
        btn_text = f"{cat} | {proxy_price} ৳ | Stock: {stock}"
        cb_data = f"buy_item_{cat}_{dur}"
        inline_markup.add(InlineKeyboardButton(btn_text, callback_data=cb_data, icon_custom_emoji_id="5249288301659041068"))
        
    bot.edit_message_text(texts[lang]['buy_ask_proxy'], chat_id=user_id, message_id=call.message.message_id, reply_markup=inline_markup)

def get_stock_query(cat, dur=None):
    query = {"category": cat, "status": "available"}
    if cat == "Nord" and dur:
        query["duration"] = dur
    return query

def send_order_confirmation(user_id, cat, dur, qty=1, message_id=None):
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    conf = get_config()
    rate = get_usdt_rate()
    price = get_product_price(cat, conf)
    total_price = price * qty

    stock = vpn_collection.count_documents(get_stock_query(cat, dur))
    user = users_collection.find_one({"chat_id": user_id}, {"balance": 1})
    user_bal = user.get("balance", 0.0) if user else 0.0

    cat_emojis = {
        "Nord": '<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji> ',
        "Hotmail": '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> ',
        "Outlook": '<tg-emoji emoji-id="5253742260054409879">⚡</tg-emoji> ',
        "Outlook.fr": '<tg-emoji emoji-id="5344008428073280881">⚡</tg-emoji> ',
        "Proxy": '<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji> ',
        "Gmail": '<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji> ',
        "Gemini": '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> '
    }

    if is_bd:
        cat_names = {
            "Nord": "নর্ড ভিপিএন (৭ দিন)",
            "Hotmail": "হটমেইল ক্লিন মেইল",
            "Outlook": "আউটলুক ক্লিন মেইল",
            "Outlook.fr": "আউটলুক.এফআর মেইল",
            "Proxy": "হাই-স্পিড প্রক্সি",
            "Gmail": "জি-মেইল অ্যাকাউন্ট",
            "Gemini": "জেমিনি প্রো (১৮ মাস)"
        }
        prod_name = cat_names.get(cat, f"{cat} {dur}")
        prod_emoji = cat_emojis.get(cat, "")

        msg_html = (
            f'<tg-emoji emoji-id="6170123066513824625">✅</tg-emoji> <b>অর্ডার নিশ্চিতকরণ</b>\n\n'
            f"<b>পণ্য:</b> {prod_emoji}{prod_name}\n"
            f"<b>পরিমাণ:</b> {qty} টি\n"
            f"<b>মোট বিল:</b> {fmt_bal(total_price)} ৳\n\n"
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>ওয়ালেট ব্যালেন্স:</b> {fmt_bal(user_bal)} ৳\n'
            f'<tg-emoji emoji-id="5249118096400070189">📦</tg-emoji> <b>বর্তমান স্টক:</b> {stock} টি'
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("অর্ডার নিশ্চিত করুন", callback_data=f"placeord_{cat}_{dur}_{qty}", style="success", icon_custom_emoji_id="5409048419211682843"),
            InlineKeyboardButton("অর্ডার বাতিল", callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5240241223632954241")
        )
    else:
        cat_names = {
            "Nord": "Nord VPN 7 Days",
            "Hotmail": "Hotmail Mail",
            "Outlook": "Outlook Mail",
            "Outlook.fr": "Outlook.fr Mail",
            "Proxy": "High-Speed Proxy",
            "Gmail": "Gmail Account",
            "Gemini": "Gemini Pro 18 Months"
        }
        prod_name = cat_names.get(cat, f"{cat} {dur}")
        prod_emoji = cat_emojis.get(cat, "")
        total_usdt = fmt_usdt(total_price / rate)
        bal_usdt = fmt_usdt(user_bal / rate)

        msg_html = (
            f'<tg-emoji emoji-id="6170123066513824625">✅</tg-emoji> <b>Order Confirmation</b>\n\n'
            f"<b>Product:</b> {prod_emoji}{prod_name}\n"
            f"<b>Quantity:</b> {qty}\n"
            f"<b>Total Bill:</b> ${total_usdt} USDT (≈ {fmt_bal(total_price)} ৳)\n\n"
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Wallet Balance:</b> ${bal_usdt} USDT\n'
            f'<tg-emoji emoji-id="5249118096400070189">📦</tg-emoji> <b>Available Stock:</b> {stock}\n'
            f'<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>Exchange Rate:</b> 1 USDT = {rate} BDT'
        )
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("Place Order", callback_data=f"placeord_{cat}_{dur}_{qty}", style="success", icon_custom_emoji_id="5409048419211682843"),
            InlineKeyboardButton("Cancel Order", callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5240241223632954241")
        )

    if message_id:
        try:
            bot.edit_message_text(msg_html, chat_id=user_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(user_id, msg_html, reply_markup=markup, parse_mode="HTML")

def send_gemini_details(call, user_id, country, is_bd):
    if is_bd:
        gemini_details_text = (
            '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>১৮ মাসের প্ল্যান</b>\n'
            '<tg-emoji emoji-id="5274002879215067737">☁️</tg-emoji> <b>৫টিবি ক্লাউড স্টোরেজ অন্তর্ভুক্ত</b>\n'
            '<tg-emoji emoji-id="5453957997418004470">👥</tg-emoji> <b>আপনি ৫ জন সদস্য যুক্ত করতে পারবেন</b>\n'
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>শেয়ার্ড নয় (No Shared)</b>\n'
            '<tg-emoji emoji-id="5030643469513655027">🔒</tg-emoji> <b>১০০% প্রাইভেট অ্যাকাউন্ট</b>\n'
            '<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>কোনো কার্ডের প্রয়োজন নেই</b>\n'
            '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>নন-ওয়ারেন্টি প্রোডাক্ট</b>\n'
            '<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> <b>যেকোনো দেশে কোনো ভেরিফিকেশন ছাড়া চলবে</b>\n'
            '<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>লিংকের মেয়াদ ৪-৬ দিন (প্ল্যানের নয়, লিংকের)</b>\n\n'
            '<tg-emoji emoji-id="5282835135861892082">❗</tg-emoji> <b>জরুরি নোট:</b>\n'
            'অর্ডার পাওয়ার ২৪ ঘণ্টার মধ্যে রিডিম লিংক ব্যবহার করতে হবে। লিংকে কোনো সমস্যা থাকলে অবশ্যই ২৪ ঘণ্টার মধ্যে জানাতে হবে। ২৪ ঘণ্টা পর কোনো রিপ্লেসমেন্ট বা নতুন লিংক দেওয়া হবে না।\n'
            '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> ১০০% জেনুইন Gemini AI Pro সাবস্ক্রিপশন আপনার নিজস্ব জিমেইলে এক্টিভ হবে।\n\n'
            '<tg-emoji emoji-id="5431505596316665041">👑</tg-emoji> <b>ফুল ফ্যামিলি অ্যাকাউন্ট (এটি কোনো ইনভাইটেশন নয়)</b>\n'
            '<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> প্রাপ্ত রিডিম লিংকটি আপনার ব্রাউজারে পেস্ট করে "Activate Offer" এ ক্লিক করুন। আপনার সাবস্ক্রিপশন সফলভাবে সক্রিয় হয়ে যাবে।'
        )
    else:
        gemini_details_text = (
            '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>18 Months Plan</b>\n'
            '<tg-emoji emoji-id="5274002879215067737">☁️</tg-emoji> <b>5TB cloud storage included</b>\n'
            '<tg-emoji emoji-id="5453957997418004470">👥</tg-emoji> <b>You can add 5 users</b>\n'
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>No Shared</b>\n'
            '<tg-emoji emoji-id="5030643469513655027">🔒</tg-emoji> <b>100% Private</b>\n'
            '<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>NO NEED ANY CARD</b>\n'
            '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>Non Warranty</b>\n'
            '<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> <b>Works in any country no verification</b>\n'
            '<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>Links Expiry in 4-6days ( Not Plan I mean Link )</b>\n\n'
            '<tg-emoji emoji-id="5282835135861892082">❗</tg-emoji> <b>Important Note:</b>\n'
            'The redeem link must be used within 24 hours of receiving the order. If you face any issue with the link, you must report it to us within 24 hours. After 24 hours, no replacement or reissue will be provided.\n'
            '<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> 100% genuine Gemini AI Pro subscription activated on your own Gmail.\n\n'
            "<tg-emoji emoji-id=\"5431505596316665041\">👑</tg-emoji> <b>FULL FAMILY ACCOUNT. IT'S NOT AN INVITE</b>\n"
            '<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> Paste the received redeem link into your browser and click on “Activate Offer”. Your subscription/offer will then be activated successfully.'
        )

    markup = InlineKeyboardMarkup(row_width=1)
    buy_now_text = "Buy Now" if not is_bd else "এখনই কিনুন"
    how_to_use_text = "How To Use"
    back_text = "Back" if not is_bd else "ফিরে যান"

    markup.add(
        InlineKeyboardButton(buy_now_text, callback_data="gemini_buy_now", style="success", icon_custom_emoji_id="5395463407589672312"),
        InlineKeyboardButton(how_to_use_text, callback_data="gemini_how_to_use", style="primary", icon_custom_emoji_id="5436113877181941026"),
        InlineKeyboardButton(back_text, callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5220079633533250496")
    )

    try:
        bot.edit_message_text(gemini_details_text, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(user_id, gemini_details_text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == 'gemini_details')
def handle_gemini_details_cb(call):
    if not check_user_country_callback(call):
        return
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    send_gemini_details(call, user_id, country, is_bd)

@bot.callback_query_handler(func=lambda call: call.data == 'gemini_how_to_use')
def handle_gemini_how_to_use(call):
    if not check_user_country_callback(call):
        return
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')

    if is_bd:
        how_text = (
            '<tg-emoji emoji-id="5465154440287757794">🛡️</tg-emoji> <b>লিংক হোল্ড করার জন্য ৬ ঘণ্টার ওয়ারেন্টি। আপনার নিজস্ব অ্যাকাউন্টে সাথে সাথে এক্টিভ হবে।</b>\n'
            '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>এক্টিভেশনের পর বর্তমানে কোনো শপ ওয়ারেন্টি নেই।</b>\n'
            '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>শুধুমাত্র এক্টিভেশন ওয়ারেন্টি।</b>\n\n'
            '<tg-emoji emoji-id="5285071241865077373">🆘</tg-emoji> <b>যেহেতু এই প্ল্যানটি Jio দ্বারা পরিচালিত, তাই Jio সিম প্ল্যান নিষ্ক্রিয় বা বন্ধ হলে Google AI Pro সাবস্ক্রিপশনও বন্ধ হয়ে যাবে।</b>\n'
            'এই কারণেই এই প্রোডাক্টের সাথে কোনো প্রকার ওয়ারেন্টি থাকে না। এক্টিভেশনের পর ভাগ্য ভালো থাকলে আপনি ১৮ মাস পর্যন্ত এটি ব্যবহার করতে পারবেন। তবে Jio সিম প্ল্যান পরিবর্তন বা বন্ধ হয়ে গেলে Google AI Pro সাবস্ক্রিপশনও শেষ হয়ে যাবে।\n'
            'মনে রাখবেন, আপনি এই অফারটি একবারই ক্লেইম করতে পারবেন।\n\n'
            '<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>কোনো কার্ডের প্রয়োজন নেই</b> '
            '<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>অফিসিয়াল এক্টিভেশন</b> '
            '<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> <b>কান্ট্রি কোনো সমস্যা নেই</b> '
            '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>ইনস্ট্যান্ট এক্টিভেশন</b> '
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>অব্যবহৃত লিংকের ওয়ারেন্টি নেই</b> '
            '<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>৬ ঘণ্টা হোল্ডিং ওয়ারেন্টি</b> '
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>এক্টিভ করার পর কোনো ওয়ারেন্টি প্রযোজ্য নয়</b>\n\n'
            '<tg-emoji emoji-id="5282835135861892082">❗</tg-emoji> <b>আপনার অ্যাকাউন্টে সাথে সাথে এক্টিভ করুন। এক্টিভেশনের পর কোনো শপ ওয়ারেন্টি নেই।</b>'
        )
    else:
        how_text = (
            '<tg-emoji emoji-id="5465154440287757794">🛡️</tg-emoji> <b>6 Hours warranty for Holding the link. Active on your account. Active Immediately</b>\n'
            '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>Currently No shop warranty after activation.</b>\n'
            '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Only activation warranty</b>\n\n'
            '<tg-emoji emoji-id="5285071241865077373">🆘</tg-emoji> <b>Since this plan is managed by Jio, the Google AI Pro subscription will end if the Jio SIM plan becomes inactive.</b>\n'
            'This is why the product comes with absolute zero warranty. After activation, if you are lucky, you may be able to use it for up to 18 months. However, if the Jio SIM plan is changed or becomes inactive, the Google AI Pro subscription will also end.\n'
            'Remember, you can claim this offer only once.\n\n'
            '<tg-emoji emoji-id="5213403875670765022">💳</tg-emoji> <b>No Card Required</b> '
            '<tg-emoji emoji-id="5463289097336405244">⭐</tg-emoji> <b>Official Activation</b> '
            '<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> <b>No Country Issue</b> '
            '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>Instant activation</b> '
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>No warranty for unused links</b> '
            '<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>6 Hours holding warranty</b> '
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>No Warranty Available After Activated Link</b>\n\n'
            '<tg-emoji emoji-id="5282835135861892082">❗</tg-emoji> <b>Active immediately on your account. No shop warranty after activation.</b>'
        )

    markup = InlineKeyboardMarkup(row_width=1)
    buy_now_text = "Buy Now" if not is_bd else "এখনই কিনুন"
    back_text = "Back" if not is_bd else "ফিরে যান"
    markup.add(InlineKeyboardButton(buy_now_text, callback_data="gemini_buy_now", style="success", icon_custom_emoji_id="5395463407589672312"))
    markup.add(InlineKeyboardButton(back_text, callback_data="gemini_details", style="danger", icon_custom_emoji_id="5220079633533250496"))
    try:
        bot.edit_message_text(how_text, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(user_id, how_text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == 'gemini_buy_now')
def handle_gemini_buy_now(call):
    if not check_user_country_callback(call):
        return
    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    lang = get_user_lang(user_id)
    cat = "Gemini"
    dur = "18m"

    stock = vpn_collection.count_documents(get_stock_query(cat, dur))
    if stock == 0:
        bot.answer_callback_query(call.id, texts[lang]['buy_sold_out'], show_alert=True)
        return

    bot.answer_callback_query(call.id)

    conf = get_config()
    rate = get_usdt_rate()
    price = get_product_price(cat, conf)
    prod_emoji = '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> '

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("1", callback_data=f"setqty_{cat}_{dur}_1", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("5", callback_data=f"setqty_{cat}_{dur}_5", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("10", callback_data=f"setqty_{cat}_{dur}_10", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("20", callback_data=f"setqty_{cat}_{dur}_20", style="primary", icon_custom_emoji_id="5463289097336405244")
    )

    if is_bd:
        prod_name = "জেমিনি প্রো (১৮ মাস)"
        markup.add(InlineKeyboardButton("অন্যান্য পরিমাণ", callback_data=f"setqty_{cat}_{dur}_custom", style="success", icon_custom_emoji_id="5192825506239616944"))
        markup.add(InlineKeyboardButton("ফিরে যান", callback_data="gemini_details", style="danger", icon_custom_emoji_id="5220079633533250496"))

        msg_text = (
            f'<tg-emoji emoji-id="4967518033061872209">🛒</tg-emoji> <b>পরিমাণ নির্বাচন করুন</b>\n\n'
            f"<b>পণ্য:</b> {prod_emoji}<b>{prod_name}</b>\n"
            f'<tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>বর্তমান স্টক:</b> {stock} টি\n'
            f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>প্রতিটির মূল্য:</b> {fmt_bal(price)} ৳\n\n'
            f"নিচের বাটনে চাপ দিন বা <b>অন্যান্য পরিমাণ</b> বাটনে ক্লিক করুন:"
        )
    else:
        prod_name = "Gemini Pro 18 Months"
        usdt_unit = fmt_usdt(price / rate)
        markup.add(InlineKeyboardButton("Custom Amount", callback_data=f"setqty_{cat}_{dur}_custom", style="success", icon_custom_emoji_id="5192825506239616944"))
        markup.add(InlineKeyboardButton("Go Back", callback_data="gemini_details", style="danger", icon_custom_emoji_id="5220079633533250496"))

        msg_text = (
            f'<tg-emoji emoji-id="4967518033061872209">🛒</tg-emoji> <b>Select Quantity</b>\n\n'
            f"<b>Item:</b> {prod_emoji}<b>{prod_name}</b>\n"
            f'<tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Available Stock:</b> {stock}\n'
            f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price per item:</b> ${usdt_unit} USDT (≈ {fmt_bal(price)} ৳)\n\n'
            f"Please select quantity or click <b>Custom Amount</b>:"
        )

    try:
        bot.edit_message_text(msg_text, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        bot.send_message(user_id, msg_text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_item_'))
def handle_buy_item(call):
    if not check_user_country_callback(call):
        return
    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    lang = get_user_lang(user_id)
    parts = call.data.split('_')
    cat = parts[2]
    dur = parts[3]

    if cat == "Gemini":
        bot.answer_callback_query(call.id)
        return send_gemini_details(call, user_id, country, is_bd)

    stock = vpn_collection.count_documents(get_stock_query(cat, dur))
    if stock == 0:
        bot.answer_callback_query(call.id, texts[lang]['buy_sold_out'], show_alert=True)
        return

    bot.answer_callback_query(call.id)

    conf = get_config()
    rate = get_usdt_rate()
    price = get_product_price(cat, conf)

    cat_emojis = {
        "Nord": '<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji> ',
        "Hotmail": '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> ',
        "Outlook": '<tg-emoji emoji-id="5253742260054409879">⚡</tg-emoji> ',
        "Outlook.fr": '<tg-emoji emoji-id="5344008428073280881">⚡</tg-emoji> ',
        "Proxy": '<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji> ',
        "Gmail": '<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji> '
    }
    prod_emoji = cat_emojis.get(cat, "")

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("1", callback_data=f"setqty_{cat}_{dur}_1", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("5", callback_data=f"setqty_{cat}_{dur}_5", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("10", callback_data=f"setqty_{cat}_{dur}_10", style="primary", icon_custom_emoji_id="5463289097336405244"),
        InlineKeyboardButton("20", callback_data=f"setqty_{cat}_{dur}_20", style="primary", icon_custom_emoji_id="5463289097336405244")
    )

    if is_bd:
        cat_names = {
            "Nord": "নর্ড ভিপিএন (৭ দিন)",
            "Hotmail": "হটমেইল ক্লিন মেইল",
            "Outlook": "আউটলুক ক্লিন মেইল",
            "Outlook.fr": "আউটলুক.এফআর মেইল",
            "Proxy": "হাই-স্পিড প্রক্সি",
            "Gmail": "জি-মেইল অ্যাকাউন্ট"
        }
        prod_name = cat_names.get(cat, f"{cat} {dur}")
        markup.add(InlineKeyboardButton("অন্যান্য পরিমাণ", callback_data=f"setqty_{cat}_{dur}_custom", style="success", icon_custom_emoji_id="5192825506239616944"))
        markup.add(InlineKeyboardButton("ফিরে যান", callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5220079633533250496"))

        msg_text = (
            f'<tg-emoji emoji-id="4967518033061872209">🛒</tg-emoji> <b>পরিমাণ নির্বাচন করুন</b>\n\n'
            f"<b>পণ্য:</b> {prod_emoji}<b>{prod_name}</b>\n"
            f'<tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>বর্তমান স্টক:</b> {stock} টি\n'
            f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>প্রতিটির মূল্য:</b> {fmt_bal(price)} ৳\n\n'
            f"নিচের বাটনে চাপ দিন বা <b>অন্যান্য পরিমাণ</b> বাটনে ক্লিক করুন:"
        )
    else:
        cat_names = {
            "Nord": "Nord VPN 7 Days",
            "Hotmail": "Hotmail Mail",
            "Outlook": "Outlook Mail",
            "Outlook.fr": "Outlook.fr Mail",
            "Proxy": "High-Speed Proxy",
            "Gmail": "Gmail Account"
        }
        prod_name = cat_names.get(cat, f"{cat} {dur}")
        usdt_unit = fmt_usdt(price / rate)
        markup.add(InlineKeyboardButton("Custom Amount", callback_data=f"setqty_{cat}_{dur}_custom", style="success", icon_custom_emoji_id="5192825506239616944"))
        markup.add(InlineKeyboardButton("Go Back", callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5220079633533250496"))

        msg_text = (
            f'<tg-emoji emoji-id="4967518033061872209">🛒</tg-emoji> <b>Select Quantity</b>\n\n'
            f"<b>Item:</b> {prod_emoji}<b>{prod_name}</b>\n"
            f'<tg-emoji emoji-id="5210956306952758910">📦</tg-emoji> <b>Available Stock:</b> {stock}\n'
            f'<tg-emoji emoji-id="5368503210677914201">💵</tg-emoji> <b>Price per item:</b> ${usdt_unit} USDT (≈ {fmt_bal(price)} ৳)\n\n'
            f"Please select quantity or click <b>Custom Amount</b>:"
        )

    try:
        bot.edit_message_text(msg_text, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except:
        bot.send_message(user_id, msg_text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith('setqty_'))
def handle_set_qty(call):
    if not check_user_country_callback(call):
        return
    parts = call.data.split('_')
    cat = parts[1]
    dur = parts[2]
    val = parts[3]

    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    lang = get_user_lang(user_id)

    stock = vpn_collection.count_documents(get_stock_query(cat, dur))

    if val == "custom":
        bot.answer_callback_query(call.id)
        if is_bd:
            prompt = f'<tg-emoji emoji-id="5463289097336405244">✏️</tg-emoji> <b>পরিমাণ লিখুন</b>\n\nআপনি কয়টি <b>{cat}</b> অ্যাকাউন্ট কিনতে চান সংখ্যাটি লিখুন (১ থেকে {stock} এর মধ্যে):'
        else:
            prompt = f'<tg-emoji emoji-id="5463289097336405244">✏️</tg-emoji> <b>Enter Quantity</b>\n\nPlease type the number of <b>{cat}</b> accounts you want to buy (1 to {stock}):'
        msg = bot.send_message(user_id, prompt, reply_markup=get_cancel_menu(lang), parse_mode="HTML")
        bot.register_next_step_handler(msg, process_custom_qty_input, cat, dur, stock)
        return

    qty = int(val)
    if qty > stock:
        err = f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> পর্যাপ্ত স্টক নেই! বর্তমানে মাত্র {stock} টি আছে।' if is_bd else f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Not enough stock! Only {stock} available.'
        bot.answer_callback_query(call.id, err, show_alert=True)
        return

    bot.answer_callback_query(call.id)
    send_order_confirmation(user_id, cat, dur, qty=qty, message_id=call.message.message_id)

def process_custom_qty_input(message, cat, dur, stock):
    user_id = message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    lang = get_user_lang(user_id)

    if message.text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 ফিরে যান', '❌ বাতিল', '🔙 ব্যাক'] or is_back_or_cancel_text(message.text):
        bot.send_message(user_id, texts[lang]['buy_cancelled'], reply_markup=get_main_menu(lang, user_id))
        return

    try:
        qty = int(message.text.strip())
        if qty <= 0:
            raise ValueError
    except:
        err = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> সঠিক সংখ্যা লিখুন।' if is_bd else '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Invalid quantity. Please enter a valid number.'
        bot.send_message(user_id, err, reply_markup=get_main_menu(lang, user_id))
        return

    if qty > stock:
        err = f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> পর্যাপ্ত স্টক নেই! আপনি চেয়েছেন {qty} টি, কিন্তু মাত্র {stock} টি রয়েছে।' if is_bd else f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Not enough stock! You requested {qty}, but only {stock} are available.'
        bot.send_message(user_id, err, reply_markup=get_main_menu(lang, user_id))
        return

    send_order_confirmation(user_id, cat, dur, qty=qty)

@bot.callback_query_handler(func=lambda call: call.data == 'cancel_to_catalog')
def handle_cancel_to_catalog(call):
    if not check_user_country_callback(call):
        return
    bot.answer_callback_query(call.id)
    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    msg_title = '<tg-emoji emoji-id="5395463407589672312">🛍️</tg-emoji> <b>সকল পণ্যের তালিকা</b>\nদয়া করে যে পণ্যটি কিনতে চান সেটি নির্বাচন করুন:' if country == 'bd' else '<tg-emoji emoji-id="5395463407589672312">🛍️</tg-emoji> <b>Available Products</b>\nPlease select a product to proceed:'
    try:
        bot.edit_message_text(
            msg_title,
            chat_id=user_id,
            message_id=call.message.message_id,
            reply_markup=get_available_products_keyboard(country),
            parse_mode="HTML"
        )
    except:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('placeord_'))
def handle_place_order(call):
    if not check_user_country_callback(call):
        return
    parts = call.data.split('_')
    cat = parts[1]
    dur = parts[2]
    qty = int(parts[3])

    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    lang = get_user_lang(user_id)

    conf = get_config()
    rate = get_usdt_rate()
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

    if is_bd:
        short_names = {
            "Nord": "নর্ড ভিপিএন (৭ দিন)",
            "Hotmail": "হটমেইল ক্লিন মেইল",
            "Outlook": "আউটলুক ক্লিন মেইল",
            "Outlook.fr": "আউটলুক.এফআর মেইল",
            "Proxy": "হাই-স্পিড প্রক্সি",
            "Gmail": "জি-মেইল অ্যাকাউন্ট",
            "Gemini": "জেমিনি প্রো (১৮ মাস)"
        }
    else:
        short_names = {
            "Nord": "Nord — 7D",
            "Hotmail": "Hotmail Mail",
            "Outlook": "Outlook Mail",
            "Outlook.fr": "Outlook.fr Mail",
            "Proxy": "High-Speed Proxy",
            "Gmail": "Gmail Account",
            "Gemini": "Gemini Pro 18 Months"
        }
    prod_display = short_names.get(cat, f"{cat} — {dur}")

    if qty > 1:
        markup = InlineKeyboardMarkup(row_width=1)
        if is_bd:
            markup.add(
                InlineKeyboardButton("টেক্সট ফাইল (.txt)", callback_data=f"dlfile_{cat}_{dur}_{qty}_txt", style="primary", icon_custom_emoji_id="4967656361073574498"),
                InlineKeyboardButton("এক্সেল ফাইল (.xlsx)", callback_data=f"dlfile_{cat}_{dur}_{qty}_xlsx", style="success", icon_custom_emoji_id="5348125953090403204")
            )
            if qty <= 20:
                markup.add(
                    InlineKeyboardButton("মেসেজে সরাসরি নিন (Direct Text)", callback_data=f"dlfile_{cat}_{dur}_{qty}_msg", style="primary", icon_custom_emoji_id="5212988801441344587")
                )
            markup.add(
                InlineKeyboardButton("ফিরে যান", callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5220079633533250496")
            )
            msg_html = (
                f'<tg-emoji emoji-id="5348125953090403204">📥</tg-emoji> <b>ডেলিভারি মাধ্যম নির্বাচন করুন</b>\n\n'
                f"পণ্য: <b>{prod_display} ({qty} টি)</b>\n"
                f"মোট বিল: <b>{fmt_bal(total_price)} ৳</b>\n\n"
                f"অ্যাকাউন্টগুলো পেতে নিচের যেকোনো একটি অপশন নির্বাচন করুন:"
            )
        else:
            total_usdt = fmt_usdt(total_price / rate)
            markup.add(
                InlineKeyboardButton("TXT File (.txt)", callback_data=f"dlfile_{cat}_{dur}_{qty}_txt", style="primary", icon_custom_emoji_id="4967656361073574498"),
                InlineKeyboardButton("Excel File (.xlsx)", callback_data=f"dlfile_{cat}_{dur}_{qty}_xlsx", style="success", icon_custom_emoji_id="5348125953090403204")
            )
            if qty <= 20:
                markup.add(
                    InlineKeyboardButton("Direct In-Chat Message", callback_data=f"dlfile_{cat}_{dur}_{qty}_msg", style="primary", icon_custom_emoji_id="5212988801441344587")
                )
            markup.add(
                InlineKeyboardButton("Go Back", callback_data="cancel_to_catalog", style="danger", icon_custom_emoji_id="5220079633533250496")
            )
            msg_html = (
                f'<tg-emoji emoji-id="5348125953090403204">📥</tg-emoji> <b>Select Delivery Method</b>\n\n'
                f"Product: <b>{prod_display} (x{qty})</b>\n"
                f"Total Bill: <b>${total_usdt} USDT (≈ {fmt_bal(total_price)} ৳)</b>\n\n"
                f"Please select how you want to receive your accounts:"
            )
        bot.edit_message_text(msg_html, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        return

    # Deliver 1 item directly via text
    is_live_checked = False
    if cat == "Gmail":
        check_msg = (
            '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> <b>অ্যাকাউন্ট ভেরিফিকেশন চলছে...</b>\n\n'
            '<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <i>আপনার জিমেইল অ্যাকাউন্টটি লাইভ সার্ভার থেকে যাচাই (Live Check) করা হচ্ছে...</i>\n'
            '<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <i>অনুগ্রহ করে একটু অপেক্ষা করুন...</i>'
            if is_bd else
            '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> <b>Account Verification in Progress...</b>\n\n'
            '<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <i>Verifying your Gmail account with live server...</i>\n'
            '<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <i>Please wait a moment...</i>'
        )
        try:
            bot.edit_message_text(check_msg, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
            time.sleep(1.2)
        except:
            pass

        selected_item = None
        while True:
            candidate = vpn_collection.find_one({"category": "Gmail", "status": "available"})
            if not candidate:
                break
            
            c_cred = candidate.get("credentials", "").strip()
            c_email = parse_gmail_address(c_cred)
            if not c_email:
                vpn_collection.update_one({"_id": candidate["_id"]}, {"$set": {"status": "bad_gmail"}})
                bad_gmail_collection.insert_one({
                    "email": "Invalid Format",
                    "credentials": c_cred,
                    "failed_at": datetime.now(timezone.utc),
                    "reason": "invalid_format"
                })
                continue
            
            chk_res = check_gmail_batch_live([c_email])
            if chk_res is None:
                # ⚠️ Checker issue / server error / timeout -> Deliver directly from stock without checking
                print(f"[Gmail Checker] Checker service unavailable. Delivering {c_email} directly from stock as Non-Checked.")
                selected_item = candidate
                is_live_checked = False
                break

            if chk_res.get(c_email) is True:
                selected_item = candidate
                is_live_checked = True
                break
            else:
                vpn_collection.update_one({"_id": candidate["_id"]}, {"$set": {"status": "bad_gmail"}})
                bad_gmail_collection.insert_one({
                    "email": c_email,
                    "credentials": c_cred,
                    "failed_at": datetime.now(timezone.utc),
                    "reason": "verify_or_dead"
                })
                continue

        if not selected_item:
            fail_text = (
                '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> দুঃখিত! স্টকে থাকা জিমেইল অ্যাকাউন্টগুলো যাচাইয়ে সমস্যা পাওয়া গেছে এবং স্টকে কোনো সচল অ্যাকাউন্ট নেই।\nআপনার কোনো ব্যালেন্স কাটা হয়নি।'
                if is_bd else
                '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> Sorry! Available Gmail accounts failed verification and no active accounts remain. No balance was deducted.'
            )
            try:
                bot.edit_message_text(fail_text, chat_id=user_id, message_id=call.message.message_id)
            except:
                bot.send_message(user_id, fail_text)
            return

        items = [selected_item]

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
            formatted_creds.append(f'<code>{escape(cred_str)}</code>')
        elif cat == "Proxy":
            lbl_proxy = "হাই-স্পিড প্রক্সি" if is_bd else "High-Speed Proxy"
            formatted_creds.append(
                f'<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji> <b>{lbl_proxy}:</b>\n<code>{escape(cred_str)}</code>'
            )
        elif cat == "Nord":
            if '|' in cred_str:
                c_parts = [escape(p.strip()) for p in cred_str.split('|')]
            elif ':' in cred_str:
                c_parts = [escape(p.strip()) for p in cred_str.split(':')]
            elif ' ' in cred_str:
                c_parts = [escape(p.strip()) for p in cred_str.split(' ')]
            else:
                c_parts = [escape(cred_str)]
            lbl_u = "ইউজারনেম/মেইল" if is_bd else "Username/Mail"
            lbl_p = "পাসওয়ার্ড" if is_bd else "Password"
            if len(c_parts) >= 2:
                formatted_creds.append(
                    f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <b>{lbl_u}:</b> <code>{c_parts[0]}</code>\n'
                    f'<tg-emoji emoji-id="5870972873450984431">🔒</tg-emoji> <b>{lbl_p}:</b> <code>{c_parts[1]}</code>'
                )
            else:
                formatted_creds.append(f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{c_parts[0]}</code>')
        elif '|' in cred_str:
            c_parts = [escape(p.strip()) for p in cred_str.split('|')]
            if len(c_parts) == 2:
                email = c_parts[0]
                password = c_parts[1]
                formatted_creds.append(
                    f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{email}</code>\n'
                    f'<tg-emoji emoji-id="5870972873450984431">🔒</tg-emoji> <code>{password}</code>'
                )
            elif len(c_parts) >= 3:
                email = c_parts[0]
                password = c_parts[1]
                rec = c_parts[2]
                rec_lbl = "রিকভারি মেইল" if is_bd else "Recovery Mail"
                formatted_creds.append(
                    f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{email}</code>\n'
                    f'<tg-emoji emoji-id="5870972873450984431">🔒</tg-emoji> <code>{password}</code>\n'
                    f'<tg-emoji emoji-id="5447410659077661506">🛡️</tg-emoji> <b>{rec_lbl}:</b> <code>{rec}</code>'
                )
            else:
                formatted_creds.append(f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{escape(cred_str)}</code>')
        elif ' ' in cred_str:
            c_parts = [escape(p.strip()) for p in cred_str.split(' ')]
            if len(c_parts) == 2:
                email = c_parts[0]
                password = c_parts[1]
                formatted_creds.append(
                    f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{email}</code>\n'
                    f'<tg-emoji emoji-id="5870972873450984431">🔒</tg-emoji> <code>{password}</code>'
                )
            else:
                formatted_creds.append(f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{escape(cred_str)}</code>')
        elif cat == "Gemini":
            formatted_creds.append(
                f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>Gemini Pro Redeem Link:</b>\n<code>{escape(cred_str)}</code>'
            )
        else:
            formatted_creds.append(f'<tg-emoji emoji-id="5253742260054409879">📧</tg-emoji> <code>{escape(cred_str)}</code>')

    raw_creds_text = "\n\n".join(formatted_creds)

    cat_emojis = {
        "Nord": '<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji>',
        "Hotmail": '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji>',
        "Outlook": '<tg-emoji emoji-id="5253742260054409879">⚡</tg-emoji>',
        "Outlook.fr": '<tg-emoji emoji-id="5344008428073280881">⚡</tg-emoji>',
        "Proxy": '<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji>',
        "Gmail": '<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji>',
        "Gemini": '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>'
    }
    prod_emoji = cat_emojis.get(cat, "")

    type_badge = ""
    type_label = ""
    if cat == "Gmail":
        if is_live_checked:
            type_label = "চেক করা জিমেইল" if is_bd else "Checked Gmail"
            type_badge = f'\n<tg-emoji emoji-id="5447410659077661506">🔍</tg-emoji> <b>স্ট্যাটাস:</b> <code>{type_label}</code> <tg-emoji emoji-id="5213406375341731253">✅</tg-emoji>'
        else:
            type_label = "নন চেক" if is_bd else "Non-checked"
            type_badge = f'\n<tg-emoji emoji-id="5447410659077661506">🔍</tg-emoji> <b>স্ট্যাটাস:</b> <code>{type_label}</code> <tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji>'

    gmail_notice_bd = (
        '\n\n━━━━━━━━━━━━━━━━━━━━━\n'
        '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <i>এই জিমেইলগুলোতে কোনো সমস্যা থাকলে আপনি ক্রয়ের ১ ঘণ্টার (1 hour) মধ্যে রিপ্লেসমেন্ট করে নিতে পারবেন।</i>'
    ) if cat == "Gmail" else ""

    gmail_notice_en = (
        '\n\n━━━━━━━━━━━━━━━━━━━━━\n'
        '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <i>If there are any issues with these Gmail accounts, you can claim a replacement within 1 hour of purchase.</i>'
    ) if cat == "Gmail" else ""

    gemini_notice_bd = (
        '\n\n━━━━━━━━━━━━━━━━━━━━━\n'
        '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>ব্যবহার নির্দেশিকা:</b> রিডিম লিংকটি কপি করে আপনার ব্রাউজারে পেস্ট করুন এবং “Activate Offer” এ ক্লিক করুন।\n'
        '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>সতর্কবার্তা:</b> লিংকটি পাওয়ার ২৪ ঘণ্টার মধ্যে ব্যবহার করতে হবে।'
    ) if cat == "Gemini" else ""

    gemini_notice_en = (
        '\n\n━━━━━━━━━━━━━━━━━━━━━\n'
        '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>How To Use:</b> Paste the redeem link into your browser and click “Activate Offer”.\n'
        '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>Notice:</b> The link must be redeemed within 24 hours of receiving the order.'
    ) if cat == "Gemini" else ""

    if is_bd:
        success_msg = (
            f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>ক্রয় সফল হয়েছে!</b>\n\n'
            f'{prod_emoji} <b>{prod_display} ({qty} টি)</b>'
            f'{type_badge if cat == "Gmail" else ""}\n'
            f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>মোট বিল:</b> {fmt_bal(total_price)} ৳ | <b>বর্তমান ব্যালেন্স:</b> {fmt_bal(new_balance)} ৳\n\n'
            f'{raw_creds_text}'
            f'{gmail_notice_bd}'
            f'{gemini_notice_bd}'
        )
    else:
        total_usdt = fmt_usdt(total_price / rate)
        bal_usdt = fmt_usdt(new_balance / rate)
        success_msg = (
            f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>Purchase Successful!</b>\n\n'
            f'{prod_emoji} <b>{prod_display} x{qty}</b>'
            f'{type_badge if cat == "Gmail" else ""}\n'
            f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>Total:</b> ${total_usdt} USDT | <b>Balance:</b> ${bal_usdt} USDT\n\n'
            f'{raw_creds_text}'
            f'{gmail_notice_en}'
            f'{gemini_notice_en}'
        )

    delivered = False
    try:
        bot.edit_message_text(success_msg, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
        delivered = True
    except Exception:
        plain_creds = []
        for cred in creds_list:
            cred_str = cred.strip()
            if cat in ["Hotmail", "Outlook", "Outlook.fr"]:
                plain_creds.append(f'<code>{escape(cred_str)}</code>')
            elif cat == "Proxy":
                plain_creds.append(f'<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji> <code>{escape(cred_str)}</code>')
            elif '|' in cred_str:
                c_parts = [escape(p.strip()) for p in cred_str.split('|')]
                c_em = c_parts[0] if len(c_parts) > 0 else ""
                c_pw = c_parts[1] if len(c_parts) > 1 else ""
                lbl_e = "ইমেইল" if is_bd else "Email"
                lbl_p = "পাসওয়ার্ড" if is_bd else "Password"
                plain_creds.append(f'<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <b>{lbl_e}:</b> <code>{c_em}</code>\n<tg-emoji emoji-id="5215642288071387368">🔒</tg-emoji> <b>{lbl_p}:</b> <code>{c_pw}</code>')
            else:
                plain_creds.append(f'<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <code>{escape(cred_str)}</code>')
        if is_bd:
            plain_msg = (
                f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>ক্রয় সফল হয়েছে!</b>\n━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <b>পণ্য:</b> {prod_display} ({qty} টি)\n'
                f"{('<tg-emoji emoji-id=\"5447410659077661506\">🔍</tg-emoji> <b>স্ট্যাটাস:</b> <code>' + type_label + '</code>' + chr(10)) if cat == 'Gmail' else ''}"
                f'<tg-emoji emoji-id="6170011680831969294">💵</tg-emoji> <b>মোট বিল:</b> {fmt_bal(total_price)} ৳ | <tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>বর্তমান ব্যালেন্স:</b> {fmt_bal(new_balance)} ৳\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n\n'
                f"{'\n\n'.join(plain_creds)}"
            )
        else:
            total_usdt = fmt_usdt(total_price / rate)
            bal_usdt = fmt_usdt(new_balance / rate)
            plain_msg = (
                f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Purchase Successful!</b>\n━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <b>Product:</b> {prod_display} x{qty}\n'
                f"{('<tg-emoji emoji-id=\"5447410659077661506\">🔍</tg-emoji> <b>Status:</b> <code>' + type_label + '</code>' + chr(10)) if cat == 'Gmail' else ''}"
                f'<tg-emoji emoji-id="6170011680831969294">💵</tg-emoji> <b>Total Bill:</b> ${total_usdt} USDT | <tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Balance:</b> ${bal_usdt} USDT\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n\n'
                f"{'\n\n'.join(plain_creds)}"
            )
        try:
            bot.edit_message_text(plain_msg, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
            delivered = True
        except:
            try:
                bot.send_message(user_id, plain_msg, parse_mode="HTML")
                delivered = True
            except Exception as e3:
                print(f"Failed to deliver item to user {user_id}: {e3}")

    if not delivered:
        # AUTOMATIC ROLLBACK & REFUND IF TELEGRAM DELIVERY FAILED
        users_collection.update_one({"chat_id": user_id}, {"$inc": {"balance": total_price}})
        vpn_collection.update_many({"_id": {"$in": item_ids}}, {"$set": {"status": "available", "buyer_id": None}})
        try:
            fail_refund = f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> নেটওয়ার্ক সমস্যার কারণে ডেলিভারি ব্যর্থ হয়েছে!\nআপনার {fmt_bal(total_price)} ৳ ব্যালেন্স স্বয়ংক্রিয়ভাবে রিফান্ড করা হয়েছে।' if is_bd else f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> Delivery failed due to network error!\nYour balance of {fmt_bal(total_price)} ৳ has been automatically refunded.'
            bot.send_message(user_id, fail_refund)
        except:
            pass
        return

    # Send Log
    username = user.get("username")
    first_name = user.get("first_name", "Unknown")
    user_identifier = f"@{username}" if username else first_name

    log_msg = f'<tg-emoji emoji-id="5395463407589672312">🛒</tg-emoji> <b>New Purchase</b>\n\nUser: {user_identifier} (<code>{user_id}</code>)\nCountry: {country}\nItem: <b>{cat} (x{qty})</b>\nPrice Paid: <b>{total_price} ৳</b>\nRemaining Balance: <b>{fmt_bal(new_balance)} ৳</b>'
    try:
        bot.send_message(-1002978737951, log_msg, parse_mode="HTML")
    except:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('dlfile_'))
def handle_download_file(call):
    if not check_user_country_callback(call):
        return
    parts = call.data.split('_')
    cat = parts[1]
    dur = parts[2]
    qty = int(parts[3])
    fmt = parts[4]

    user_id = call.message.chat.id
    country = get_user_country(user_id) or "bd"
    is_bd = (country == 'bd')
    lang = get_user_lang(user_id)

    conf = get_config()
    rate = get_usdt_rate()
    price = get_product_price(cat, conf)
    total_price = price * qty

    user = users_collection.find_one({"chat_id": user_id})
    balance = user.get("balance", 0.0) if user else 0.0

    is_live_checked = False
    if cat == "Gmail":
        init_avail = vpn_collection.count_documents({"category": "Gmail", "status": "available"})
        if init_avail == 0:
            bot.answer_callback_query(call.id, texts[lang]['buy_sold_out'], show_alert=True)
            return

        if balance < price:
            bot.answer_callback_query(call.id, texts[lang]['buy_no_bal'], show_alert=True)
            return

        bot.answer_callback_query(call.id)

        wait_text = (
            f'<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> <b>অ্যাকাউন্ট ভেরিফিকেশন চলছে...</b>\n\n'
            f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <i>আপনার {qty} টি জিমেইল লাইভ সার্ভার থেকে যাচাই করা হচ্ছে...</i>\n'
            f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <i>অনুগ্রহ করে ২-৩ সেকেন্ড অপেক্ষা করুন...</i>'
            if is_bd else
            f'<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> <b>Account Verification in Progress...</b>\n\n'
            f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <i>Verifying your {qty} Gmail accounts with live server...</i>\n'
            f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <i>Please wait a few seconds...</i>'
        )
        try:
            bot.edit_message_text(wait_text, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
            time.sleep(1.2)
        except:
            pass

        good_items = []
        reserved_ids = []
        checker_failed = False
        while len(good_items) < qty:
            needed = qty - len(good_items)
            batch_size = max(needed, 10)
            candidates = list(vpn_collection.find({"category": "Gmail", "status": "available", "_id": {"$nin": reserved_ids}}).limit(batch_size))
            if not candidates:
                break

            c_emails = []
            c_map = {}
            for cand in candidates:
                c_cred = cand.get("credentials", "").strip()
                c_em = parse_gmail_address(c_cred)
                if not c_em:
                    vpn_collection.update_one({"_id": cand["_id"]}, {"$set": {"status": "bad_gmail"}})
                    bad_gmail_collection.insert_one({
                        "email": "Invalid Format",
                        "credentials": c_cred,
                        "failed_at": datetime.now(timezone.utc),
                        "reason": "invalid_format"
                    })
                else:
                    c_emails.append(c_em)
                    c_map[c_em] = cand

            if not c_emails:
                continue

            chk_res = check_gmail_batch_live(c_emails)
            if chk_res is None:
                # ⚠️ Checker issue / server down / timeout -> Deliver directly from stock without checking
                print("[Gmail Checker] Checker service unavailable. Delivering directly from stock without checking.")
                checker_failed = True
                break

            for c_em in c_emails:
                cand = c_map[c_em]
                is_live = chk_res.get(c_em, False)
                if is_live:
                    if len(good_items) < qty:
                        good_items.append(cand)
                        reserved_ids.append(cand["_id"])
                else:
                    vpn_collection.update_one({"_id": cand["_id"]}, {"$set": {"status": "bad_gmail"}})
                    bad_gmail_collection.insert_one({
                        "email": c_em,
                        "credentials": cand.get("credentials", ""),
                        "failed_at": datetime.now(timezone.utc),
                        "reason": "verify_or_dead"
                    })

        # If checker failed at any point: deliver directly from available stock without checking!
        if checker_failed:
            needed = qty - len(good_items)
            extra_stock = list(vpn_collection.find({"category": "Gmail", "status": "available", "_id": {"$nin": reserved_ids}}).limit(needed))
            good_items.extend(extra_stock)

        is_live_checked = not checker_failed

        if not good_items:
            fail_text = (
                '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> দুঃখিত! স্টকে থাকা জিমেইল অ্যাকাউন্টগুলো যাচাইয়ে সমস্যা পাওয়া গেছে এবং কোনো সচল অ্যাকাউন্ট পাওয়া যায়নি।\nআপনার কোনো ব্যালেন্স কাটা হয়নি।'
                if is_bd else
                '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> Sorry! Available Gmail accounts failed verification and no active accounts remain. No balance was deducted.'
            )
            try:
                bot.edit_message_text(fail_text, chat_id=user_id, message_id=call.message.message_id)
            except:
                bot.send_message(user_id, fail_text)
            return

        items = good_items
        qty = len(good_items)
        total_price = price * qty
        if balance < total_price:
            bot.send_message(user_id, texts[lang]['buy_no_bal'])
            return
    else:
        items = list(vpn_collection.find(get_stock_query(cat, dur)).limit(qty))
        if len(items) < qty:
            bot.answer_callback_query(call.id, texts[lang]['buy_sold_out'], show_alert=True)
            return

        if balance < total_price:
            bot.answer_callback_query(call.id, texts[lang]['buy_no_bal'], show_alert=True)
            return

        bot.answer_callback_query(call.id)

    item_ids = [item['_id'] for item in items]
    new_balance = round(balance - total_price, 2)
    users_collection.update_one({"chat_id": user_id}, {"$set": {"balance": new_balance}})
    vpn_collection.update_many({"_id": {"$in": item_ids}}, {"$set": {"status": "sold", "buyer_id": user_id, "price_paid": price, "sold_at": datetime.now(timezone.utc)}})

    if is_bd:
        short_names = {
            "Nord": "নর্ড ভিপিএন (৭ দিন)",
            "Hotmail": "হটমেইল ক্লিন মেইল",
            "Outlook": "আউটলুক ক্লিন মেইল",
            "Outlook.fr": "আউটলুক.এফআর মেইল",
            "Proxy": "হাই-স্পিড প্রক্সি",
            "Gmail": "জি-মেইল অ্যাকাউন্ট",
            "Gemini": "জেমিনি প্রো (১৮ মাস)"
        }
    else:
        short_names = {
            "Nord": "Nord — 7D",
            "Hotmail": "Hotmail Mail",
            "Outlook": "Outlook Mail",
            "Outlook.fr": "Outlook.fr Mail",
            "Proxy": "High-Speed Proxy",
            "Gmail": "Gmail Account",
            "Gemini": "Gemini Pro 18 Months"
        }
    prod_display = short_names.get(cat, f"{cat} — {dur}")

    cat_emojis = {
        "Nord": '<tg-emoji emoji-id="5990056785967321926">🛡️</tg-emoji>',
        "Hotmail": '<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji>',
        "Outlook": '<tg-emoji emoji-id="5253742260054409879">⚡</tg-emoji>',
        "Outlook.fr": '<tg-emoji emoji-id="5344008428073280881">⚡</tg-emoji>',
        "Proxy": '<tg-emoji emoji-id="5848067868695991015">🌐</tg-emoji>',
        "Gmail": '<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji>',
        "Gemini": '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji>'
    }
    prod_emoji = cat_emojis.get(cat, "")

    type_badge = ""
    type_label = ""
    if cat == "Gmail":
        if is_live_checked:
            type_label = "চেক করা জিমেইল" if is_bd else "Checked Gmail"
            type_badge = f'\n<tg-emoji emoji-id="5447410659077661506">🔍</tg-emoji> <b>স্ট্যাটাস:</b> <code>{type_label}</code> <tg-emoji emoji-id="5213406375341731253">✅</tg-emoji>'
        else:
            type_label = "নন চেক" if is_bd else "Non-checked"
            type_badge = f'\n<tg-emoji emoji-id="5447410659077661506">🔍</tg-emoji> <b>স্ট্যাটাস:</b> <code>{type_label}</code> <tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji>'

    gmail_notice_bd = (
        '\n\n━━━━━━━━━━━━━━━━━━━━━\n'
        '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <i>এই জিমেইলগুলোতে কোনো সমস্যা থাকলে আপনি ক্রয়ের ১ ঘণ্টার (1 hour) মধ্যে রিপ্লেসমেন্ট করে নিতে পারবেন।</i>'
    ) if cat == "Gmail" else ""

    gmail_notice_en = (
        '\n\n━━━━━━━━━━━━━━━━━━━━━\n'
        '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <i>If there are any issues with these Gmail accounts, you can claim a replacement within 1 hour of purchase.</i>'
    ) if cat == "Gmail" else ""

    gemini_notice_bd = (
        '\n\n━━━━━━━━━━━━━━━━━━━━━\n'
        '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>ব্যবহার নির্দেশিকা:</b> রিডিম লিংকটি কপি করে আপনার ব্রাউজারে পেস্ট করুন এবং “Activate Offer” এ ক্লিক করুন।\n'
        '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>সতর্কবার্তা:</b> লিংকটি পাওয়ার ২৪ ঘণ্টার মধ্যে ব্যবহার করতে হবে।'
    ) if cat == "Gemini" else ""

    gemini_notice_en = (
        '\n\n━━━━━━━━━━━━━━━━━━━━━\n'
        '<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <b>How To Use:</b> Paste the redeem link into your browser and click “Activate Offer”.\n'
        '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>Notice:</b> The link must be redeemed within 24 hours of receiving the order.'
    ) if cat == "Gemini" else ""

    raw_lines = []
    for item in items:
        cred = item.get("credentials", "").strip()
        if '|' in cred:
            c_parts = cred.split('|')
            if len(c_parts) >= 4 and c_parts[0].strip() == c_parts[2].strip() and c_parts[1].strip() == c_parts[3].strip():
                cred = "|".join([c_parts[0].strip(), c_parts[1].strip()] + [x.strip() for x in c_parts[4:]])
        # Strip any hidden control characters that cause box shapes [] in Notepad!
        cred = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', cred).strip()
        raw_lines.append(cred)

    if fmt == 'msg':
        # Direct in-chat delivery for multiple items
        formatted_blocks = []
        for i, item in enumerate(items, 1):
            cred = item.get("credentials", "").strip()
            if '|' in cred:
                c_parts = cred.split('|')
                if len(c_parts) >= 4 and c_parts[0].strip() == c_parts[2].strip() and c_parts[1].strip() == c_parts[3].strip():
                    cred = "|".join([c_parts[0].strip(), c_parts[1].strip()] + [x.strip() for x in c_parts[4:]])
            
            if cat in ["Hotmail", "Outlook", "Outlook.fr"]:
                block = f"<b>#{i} {prod_display}:</b>\n<code>{escape(cred)}</code>"
                formatted_blocks.append(block)
            elif cat == "Proxy":
                lbl_proxy = "হাই-স্পিড প্রক্সি" if is_bd else "High-Speed Proxy"
                block = f"<b>#{i} {lbl_proxy}:</b>\n<tg-emoji emoji-id=\"5848067868695991015\">🌐</tg-emoji> <code>{escape(cred)}</code>"
                formatted_blocks.append(block)
            elif cat == "Nord":
                if '|' in cred:
                    c_parts = [escape(p.strip()) for p in cred.split('|')]
                elif ':' in cred:
                    c_parts = [escape(p.strip()) for p in cred.split(':')]
                else:
                    c_parts = [escape(p.strip()) for p in cred.split(' ')]
                lbl_u = "ইউজারনেম/মেইল" if is_bd else "Username/Mail"
                lbl_p = "পাসওয়ার্ড" if is_bd else "Password"
                if len(c_parts) >= 2:
                    block = f"<b>#{i} {prod_display}:</b>\n<tg-emoji emoji-id=\"5253742260054409879\">📧</tg-emoji> <b>{lbl_u}:</b> <code>{c_parts[0]}</code>\n<tg-emoji emoji-id=\"5870972873450984431\">🔒</tg-emoji> <b>{lbl_p}:</b> <code>{c_parts[1]}</code>"
                else:
                    block = f"<b>#{i}:</b> <code>{c_parts[0]}</code>"
                formatted_blocks.append(block)
            elif '|' in cred:
                c_parts = [escape(p.strip()) for p in cred.split('|')]
                em = c_parts[0] if len(c_parts) > 0 else ""
                pw = c_parts[1] if len(c_parts) > 1 else ""
                lbl_e = "ইমেইল" if is_bd else "Email"
                lbl_p = "পাসওয়ার্ড" if is_bd else "Password"
                block = f"<b>#{i} {prod_display}:</b>\n<tg-emoji emoji-id=\"5253742260054409879\">📧</tg-emoji> <b>{lbl_e}:</b> <code>{em}</code>\n<tg-emoji emoji-id=\"5870972873450984431\">🔒</tg-emoji> <b>{lbl_p}:</b> <code>{pw}</code>"
                formatted_blocks.append(block)
            elif cat == "Gemini":
                block = f"<b>#{i} Gemini:</b>\n<code>{escape(cred)}</code>"
                formatted_blocks.append(block)
            else:
                block = f"<b>#{i}:</b> <code>{escape(cred)}</code>"
                formatted_blocks.append(block)

        chunks = []
        curr_chunk = []
        curr_len = 0
        for b in formatted_blocks:
            if curr_len + len(b) + 2 > 3500:
                chunks.append("\n\n".join(curr_chunk))
                curr_chunk = [b]
                curr_len = len(b)
            else:
                curr_chunk.append(b)
                curr_len += len(b) + 2
        if curr_chunk:
            chunks.append("\n\n".join(curr_chunk))

        if is_bd:
            hdr_msg = (
                f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>ক্রয় সফল হয়েছে!</b>\n\n'
                f'{prod_emoji} <b>{prod_display} ({qty} টি)</b>'
                f'{type_badge if cat == "Gmail" else ""}\n'
                f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>মোট বিল:</b> {fmt_bal(total_price)} ৳ | <b>বর্তমান ব্যালেন্স:</b> {fmt_bal(new_balance)} ৳\n\n'
                f'{gmail_notice_bd}'
                f'{gemini_notice_bd}'
            )
        else:
            total_usdt = fmt_usdt(total_price / rate)
            bal_usdt = fmt_usdt(new_balance / rate)
            hdr_msg = (
                f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>Purchase Successful!</b>\n\n'
                f'{prod_emoji} <b>{prod_display} x{qty}</b>'
                f'{type_badge if cat == "Gmail" else ""}\n'
                f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>Total:</b> ${total_usdt} USDT | <b>Balance:</b> ${bal_usdt} USDT\n\n'
                f'{gmail_notice_en}'
                f'{gemini_notice_en}'
            )

        first_full = f"{hdr_msg}\n━━━━━━━━━━━━━━━━━━━━━\n\n{chunks[0]}" if chunks else hdr_msg
        delivered = False
        try:
            bot.edit_message_text(first_full, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
            delivered = True
        except Exception:
            try:
                bot.send_message(user_id, first_full, parse_mode="HTML")
                delivered = True
            except Exception as e:
                print(f"Error sending message chunk: {e}")

        for extra in chunks[1:]:
            try:
                bot.send_message(user_id, extra, parse_mode="HTML")
            except Exception as e:
                print(f"Error sending extra chunk: {e}")

    elif fmt == 'xlsx':
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Accounts"

        if cat in ["Hotmail", "Outlook", "Outlook.fr"]:
            header1 = "Accounts" if not is_bd else "অ্যাকাউন্ট"
            ws.append([header1])
            for line in raw_lines:
                ws.append([line])
        elif cat == "Gmail":
            header1 = "ইমেইল" if is_bd else "Email / Gmail"
            header2 = "পাসওয়ার্ড" if is_bd else "Password"
            ws.append([header1, header2])
            for line in raw_lines:
                if '|' in line:
                    parts = [p.strip() for p in line.split('|')]
                    ws.append([parts[0], parts[1] if len(parts) > 1 else ""])
                else:
                    ws.append([line])
        elif cat == "Proxy":
            header1 = "Proxy" if not is_bd else "প্রক্সি"
            ws.append([header1])
            for line in raw_lines:
                ws.append([line])
        elif cat == "Nord":
            headers = ["ইউজারনেম / ইমেইল", "পাসওয়ার্ড"] if is_bd else ["Username / Email", "Password"]
            ws.append(headers)
            for line in raw_lines:
                if '|' in line:
                    parts = [p.strip() for p in line.split('|')]
                    ws.append([parts[0], parts[1] if len(parts) > 1 else ""])
                elif ':' in line:
                    parts = [p.strip() for p in line.split(':')]
                    ws.append([parts[0], parts[1] if len(parts) > 1 else ""])
                else:
                    ws.append([line])
        elif cat == "Gemini":
            headers = ["রিডিম লিংক"] if is_bd else ["Redeem Link"]
            ws.append(headers)
            for line in raw_lines:
                ws.append([line])
        else:
            headers = ["ক্রেডেনশিয়াল"] if is_bd else ["Credentials"]
            ws.append(headers)
            for line in raw_lines:
                ws.append([line])

        # Header styling
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        for col_idx in range(1, len(ws[1]) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Auto-fit column widths
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = openpyxl.utils.get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 15), 80)

        file_data = io.BytesIO()
        wb.save(file_data)
        file_data.seek(0)
        ext = "xlsx"
        file_data.name = f"{cat}_{qty}_accounts.{ext}"

    else:
        # Standard text format: Windows CRLF (\r\n) line endings so Notepad never displays square boxes ("ক্ষোপ ক্ষোপ")!
        # Encoded with utf-8-sig (UTF-8 with BOM) so Windows Notepad identifies UTF-8 immediately without square box glyphs.
        file_content = "\r\n".join(raw_lines)
        file_data = io.BytesIO(file_content.encode('utf-8-sig'))
        ext = "txt"
        file_data.name = f"{cat}_{qty}_accounts.{ext}"

    if fmt != 'msg':
        if is_bd:
            success_msg = (
                f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>ক্রয় সফল হয়েছে!</b>\n\n'
                f'{prod_emoji} <b>{prod_display} ({qty} টি)</b>'
                f'{type_badge if cat == "Gmail" else ""}\n'
                f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>মোট বিল:</b> {fmt_bal(total_price)} ৳ | <b>বর্তমান ব্যালেন্স:</b> {fmt_bal(new_balance)} ৳\n\n'
                f'<tg-emoji emoji-id="4967656361073574498">📁</tg-emoji> <i>আপনার {ext.upper()} ফাইলটি তৈরি করা হয়েছে এবং নিচে সংযুক্ত করা হলো!</i>'
                f'{gmail_notice_bd}'
                f'{gemini_notice_bd}'
            )
        else:
            total_usdt = fmt_usdt(total_price / rate)
            bal_usdt = fmt_usdt(new_balance / rate)
            success_msg = (
                f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>Purchase Successful!</b>\n\n'
                f'{prod_emoji} <b>{prod_display} x{qty}</b>'
                f'{type_badge if cat == "Gmail" else ""}\n'
                f'<tg-emoji emoji-id="6170331831989180565">💎</tg-emoji> <b>Total:</b> ${total_usdt} USDT | <b>Balance:</b> ${bal_usdt} USDT\n\n'
                f'<tg-emoji emoji-id="4967656361073574498">📁</tg-emoji> <i>Your {ext.upper()} file has been generated and attached below!</i>'
                f'{gmail_notice_en}'
                f'{gemini_notice_en}'
            )

        try:
            bot.edit_message_text(success_msg, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
        except:
            pass

        delivered = False
        try:
            doc_caption = f"{prod_display} ({qty} টি){(' - ' + type_label) if cat == 'Gmail' else ''}"
            bot.send_document(user_id, file_data, caption=doc_caption)
            delivered = True
        except Exception as e1:
            print(f"Document delivery failed: {e1}")
            try:
                plain_lines = "\n".join(raw_lines)
                plain_type = (f" [স্ট্যাটাস: {type_label}]") if cat == 'Gmail' else ""
                hdr = f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>ক্রয় সফল ({qty} টি অ্যাকাউন্ট){plain_type}:</b>\n\n' if is_bd else f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Purchase Successful ({qty} accounts){plain_type}:</b>\n\n'
                bot.send_message(user_id, f"{hdr}{escape(plain_lines)}", parse_mode="HTML")
                delivered = True
            except Exception as e2:
                print(f"Fallback text delivery failed: {e2}")

    if not delivered:
        # AUTOMATIC ROLLBACK & REFUND IF FILE DELIVERY FAILED
        users_collection.update_one({"chat_id": user_id}, {"$inc": {"balance": total_price}})
        vpn_collection.update_many({"_id": {"$in": item_ids}}, {"$set": {"status": "available", "buyer_id": None}})
        try:
            fail_refund = f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> নেটওয়ার্ক সমস্যার কারণে ফাইল ডেলিভারি ব্যর্থ হয়েছে!\nআপনার {fmt_bal(total_price)} ৳ ব্যালেন্স রিফান্ড করা হয়েছে।' if is_bd else f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> File delivery failed due to network error!\nYour balance of {fmt_bal(total_price)} ৳ has been automatically refunded.'
            bot.send_message(user_id, fail_refund)
        except:
            pass
        return

    # Send Log
    username = user.get("username")
    first_name = user.get("first_name", "Unknown")
    user_identifier = f"@{username}" if username else first_name

    log_msg = f'<tg-emoji emoji-id="5395463407589672312">🛒</tg-emoji> <b>New Multi-Purchase ({ext.upper()})</b>\n\nUser: {user_identifier} (<code>{user_id}</code>)\nCountry: {country}\nItem: <b>{cat} (x{qty})</b>\nPrice Paid: <b>{total_price} ৳</b>\nRemaining Balance: <b>{fmt_bal(new_balance)} ৳</b>'
    try:
        bot.send_message(-1002978737951, log_msg, parse_mode="HTML")
    except:
        pass

def send_admin_replacement_panel(chat_id, message_id=None):
    pending_count = replacement_requests_collection.count_documents({"status": "pending"})
    approved_count = replacement_requests_collection.count_documents({"status": "approved"})
    rejected_count = replacement_requests_collection.count_documents({"status": "rejected"})

    pipeline = [
        {"$match": {"status": "pending"}},
        {"$group": {"_id": None, "total": {"$sum": "$refund_amt"}}}
    ]
    claim_agg = list(replacement_requests_collection.aggregate(pipeline))
    total_claim = claim_agg[0]["total"] if claim_agg else 0.0

    msg = (
        f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <b>রিপ্লেসমেন্ট রিকোয়েস্ট ম্যানেজমেন্ট প্যানেল</b>\n'
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>পেন্ডিং রিকোয়েস্ট:</b> <b>{pending_count:,}</b> টি\n'
        f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>মোট রিফান্ড দাবি:</b> <b>{fmt_bal(total_claim)} ৳</b>\n'
        f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>অনুমোদিত (Approved):</b> {approved_count:,} টি\n'
        f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>বাতিল (Rejected):</b> {rejected_count:,} টি\n'
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f'<tg-emoji emoji-id="5463289097336405244">👇</tg-emoji> নিচে থেকে রিভিউ ফাইল ডাউনলোড করুন অথবা এক্সেপ্ট / রিজেক্ট অপশন নির্বাচন করুন:'
    )

    markup = InlineKeyboardMarkup(row_width=2)
    # Download options
    markup.add(
        InlineKeyboardButton("রিভিউ ফাইল (.xlsx)", callback_data="repl_dl_xlsx", icon_custom_emoji_id="5348125953090403204"),
        InlineKeyboardButton("রিভিউ ফাইল (.txt)", callback_data="repl_dl_txt", icon_custom_emoji_id="4967656361073574498")
    )
    # Bulk actions by file
    markup.add(
        InlineKeyboardButton("✅ ফাইল দিয়ে এক্সেপ্ট", callback_data="repl_file_accept_prompt", icon_custom_emoji_id="5213406375341731253"),
        InlineKeyboardButton("❌ ফাইল দিয়ে রিজেক্ট", callback_data="repl_file_reject_prompt", icon_custom_emoji_id="5215642288071387368")
    )
    # Manual review & Bulk prompts
    markup.add(
        InlineKeyboardButton(f"👀 ম্যানুয়াল রিভিউ ({pending_count} টি)", callback_data="badgmail_repl_0", icon_custom_emoji_id="5447410659077661506"),
        InlineKeyboardButton("সব অ্যাপ্রুভ (All)", callback_data="badgmail_appall_prompt", icon_custom_emoji_id="5213406375341731253")
    )
    markup.add(
        InlineKeyboardButton("সব রিজেক্ট (All)", callback_data="badgmail_rejall_prompt", icon_custom_emoji_id="5215642288071387368")
    )
    markup.add(
        InlineKeyboardButton("Back to Admin", callback_data="repl_back_admin", icon_custom_emoji_id="5220079633533250496")
    )

    if message_id:
        try:
            bot.edit_message_text(msg, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
            return
        except:
            pass
    bot.send_message(chat_id, msg, reply_markup=markup, parse_mode="HTML")

def extract_emails_from_message(message):
    emails = []
    if message.document:
        file_name = (message.document.file_name or "").lower()
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        
        if file_name.endswith('.xlsx'):
            import io
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(downloaded), data_only=True)
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for cell in row:
                        if cell and isinstance(cell, str):
                            m = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', cell)
                            if m:
                                emails.append(m.group(0).lower().strip())
        else:
            try:
                text = downloaded.decode('utf-8')
            except:
                text = downloaded.decode('latin1', errors='ignore')
            for line in text.splitlines():
                m = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', line)
                if m:
                    emails.append(m.group(0).lower().strip())
                    
    elif message.text:
        for line in message.text.splitlines():
            m = re.search(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', line)
            if m:
                emails.append(m.group(0).lower().strip())
                
    seen = set()
    result = []
    for e in emails:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result

def process_bulk_accept_replacement(message):
    if is_back_or_cancel_text(message.text) or (message.text and message.text.strip() in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', 'Cancel', '❌ Cancel']):
        bot.send_message(message.chat.id, "অ্যাকশন বাতিল করা হয়েছে।", reply_markup=get_admin_menu())
        return

    target_emails = extract_emails_from_message(message)
    if not target_emails:
        msg = bot.send_message(
            message.chat.id,
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো বৈধ জিমেইল বা ফাইল পাওয়া যায়নি!\nদয়া করে আবার .txt / .xlsx ফাইল দিন অথবা জিমেইলগুলো লিখে পাঠান:\n(বাতিল করতে <b>Return to Admin</b> লিখুন)',
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin')
        )
        bot.register_next_step_handler(msg, process_bulk_accept_replacement)
        return

    accepted_count = 0
    total_refunded = 0.0
    not_found = []

    for em in target_emails:
        req = replacement_requests_collection.find_one({"target_email": em, "status": "pending"})
        if req:
            refund_amt = float(req.get("refund_amt", 5.0))
            target_uid = req["user_id"]
            
            replacement_requests_collection.update_one(
                {"_id": req["_id"]},
                {"$set": {
                    "status": "approved",
                    "processed_at": datetime.now(timezone.utc),
                    "processed_by": message.from_user.id
                }}
            )
            if req.get("order_id"):
                vpn_collection.update_one({"_id": req["order_id"]}, {"$set": {"status": "replaced"}})
                
            users_collection.update_one({"chat_id": target_uid}, {"$inc": {"balance": refund_amt}})
            u_doc = users_collection.find_one({"chat_id": target_uid}, {"balance": 1})
            new_bal = u_doc.get("balance", 0.0) if u_doc else 0.0

            try:
                user_notice = (
                    f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>আপনার রিপ্লেসমেন্ট রিকোয়েস্ট অনুমোদিত হয়েছে!</b>\n'
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f'<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <b>জিমেইল:</b> <code>{em}</code>\n'
                    f'<tg-emoji emoji-id="6170011680831969294">💵</tg-emoji> <b>ফেরতকৃত অর্থ:</b> <b>+{fmt_bal(refund_amt)} ৳</b>\n'
                    f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>আপনার বর্তমান ব্যালেন্স:</b> <b>{fmt_bal(new_bal)} ৳</b>\n'
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <i>অ্যাডমিন আপনার রিকোয়েস্টটি অনুমোদন করেছেন। টাকা ওয়ালেটে যোগ করা হয়েছে।</i>'
                )
                bot.send_message(target_uid, user_notice, parse_mode="HTML")
            except:
                pass

            accepted_count += 1
            total_refunded += refund_amt
        else:
            not_found.append(em)

    summary_msg = (
        f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>বাল্ক এক্সেপ্ট রিপোর্ট সম্পন্ন!</b>\n'
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 <b>মোট ফাইল/মেসেজে প্রাপ্ত:</b> {len(target_emails)} টি\n"
        f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>সফলভাবে এক্সেপ্ট ও রিফান্ড:</b> <b>{accepted_count}</b> টি\n'
        f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>মোট ফেরতকৃত অর্থ:</b> <b>{fmt_bal(total_refunded)} ৳</b>\n'
        f'<tg-emoji emoji-id="5215642288071387368">⚠️</tg-emoji> <b>পেন্ডিং তালিকায় ছিল না:</b> {len(not_found)} টি\n'
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    if not_found:
        preview = ", ".join([f"<code>{e}</code>" for e in not_found[:5]])
        if len(not_found) > 5:
            preview += f" (+{len(not_found)-5} more)"
        summary_msg += f"\n<tg-emoji emoji-id=\"5215642288071387368\">❌</tg-emoji> <b>পেন্ডিং ছাড়া জিমেইল:</b>\n{preview}"

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("রিপ্লেসমেন্ট প্যানেল", callback_data="repl_panel", icon_custom_emoji_id="5212988801441344587"))
    bot.send_message(message.chat.id, summary_msg, parse_mode="HTML", reply_markup=get_admin_menu())
    bot.send_message(message.chat.id, "রিপ্লেসমেন্ট প্যানেল দেখতে নিচের বাটনে চাপ দিন:", reply_markup=markup)

def process_bulk_reject_replacement(message):
    if is_back_or_cancel_text(message.text) or (message.text and message.text.strip() in ['Return to Admin', '🔙 Return to Admin', 'Back to Admin', 'Cancel', '❌ Cancel']):
        bot.send_message(message.chat.id, "অ্যাকশন বাতিল করা হয়েছে।", reply_markup=get_admin_menu())
        return

    target_emails = extract_emails_from_message(message)
    if not target_emails:
        msg = bot.send_message(
            message.chat.id,
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> কোনো বৈধ জিমেইল বা ফাইল পাওয়া যায়নি!\nদয়া করে আবার .txt / .xlsx ফাইল দিন অথবা জিমেইলগুলো লিখে পাঠান:\n(বাতিল করতে <b>Return to Admin</b> লিখুন)',
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin')
        )
        bot.register_next_step_handler(msg, process_bulk_reject_replacement)
        return

    rejected_count = 0
    not_found = []

    for em in target_emails:
        req = replacement_requests_collection.find_one({"target_email": em, "status": "pending"})
        if req:
            target_uid = req["user_id"]
            
            replacement_requests_collection.update_one(
                {"_id": req["_id"]},
                {"$set": {
                    "status": "rejected",
                    "processed_at": datetime.now(timezone.utc),
                    "processed_by": message.from_user.id
                }}
            )
            if req.get("order_id"):
                vpn_collection.update_one({"_id": req["order_id"]}, {"$set": {"status": "replacement_rejected"}})
                
            try:
                user_notice = (
                    f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>রিপ্লেসমেন্ট রিকোয়েস্ট বাতিল (Rejected)</b>\n'
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f'<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <b>জিমেইল:</b> <code>{em}</code>\n'
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>দুঃখিত, আপনার রিকোয়েস্টটি বাতিল করা হয়েছে। এটি রিপ্লেসমেন্টের জন্য অযোগ্য।</i>'
                )
                bot.send_message(target_uid, user_notice, parse_mode="HTML")
            except:
                pass

            rejected_count += 1
        else:
            not_found.append(em)

    summary_msg = (
        f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>বাল্ক রিজেক্ট রিপোর্ট সম্পন্ন!</b>\n'
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 <b>মোট ফাইল/মেসেজে প্রাপ্ত:</b> {len(target_emails)} টি\n"
        f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>সফলভাবে রিজেক্ট:</b> <b>{rejected_count}</b> টি (কোনো রিফান্ড দেওয়া হয়নি)\n'
        f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>পেন্ডিং তালিকায় ছিল না:</b> {len(not_found)} টি\n'
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    if not_found:
        preview = ", ".join([f"<code>{e}</code>" for e in not_found[:5]])
        if len(not_found) > 5:
            preview += f" (+{len(not_found)-5} more)"
        summary_msg += f"\n<tg-emoji emoji-id=\"5215642288071387368\">❌</tg-emoji> <b>পেন্ডিং ছাড়া জিমেইল:</b>\n{preview}"

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("রিপ্লেসমেন্ট প্যানেল", callback_data="repl_panel", icon_custom_emoji_id="5212988801441344587"))
    bot.send_message(message.chat.id, summary_msg, parse_mode="HTML", reply_markup=get_admin_menu())
    bot.send_message(message.chat.id, "রিপ্লেসমেন্ট প্যানেল দেখতে নিচের বাটনে চাপ দিন:", reply_markup=markup)


# --- BAD GMAIL MANAGEMENT HANDLERS ---
def show_badgmail_repl_item(chat_id, message_id, pending_list, idx):
    if not pending_list:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("ব্যাড জিমেইল মেনু", callback_data="badgmail_main", icon_custom_emoji_id="5220079633533250496"))
        msg = (
            '<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <b>রিপ্লেসমেন্ট রিকোয়েস্ট রিভিউ</b>\n'
            "━━━━━━━━━━━━━━━━━━━━━\n"
            '<tg-emoji emoji-id="5463297803235113601">🎉</tg-emoji> <b>কোনো পেন্ডিং রিপ্লেসমেন্ট রিকোয়েস্ট নেই!</b>\n'
            "সকল রিকোয়েস্ট ইতিমধ্যে রিভিউ ও নিষ্পত্তি সম্পন্ন হয়েছে।"
        )
        try:
            bot.edit_message_text(msg, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
        except:
            bot.send_message(chat_id, msg, reply_markup=markup, parse_mode="HTML")
        return

    idx = max(0, min(idx, len(pending_list) - 1))
    req = pending_list[idx]
    req_id = str(req["_id"])
    uname = f"@{req.get('username')}" if req.get('username') else req.get('first_name', 'User')
    claim_time = req.get("claimed_at")
    claim_str = claim_time.strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(claim_time, datetime) else str(claim_time or "")

    msg = (
        f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <b>রিপ্লেসমেন্ট রিকোয়েস্ট রিভিউ ({idx + 1} / {len(pending_list)})</b>\n'
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f'<tg-emoji emoji-id="4967667085606912536">👤</tg-emoji> <b>ক্রেতা:</b> {uname} (<code>{req.get("user_id")}</code>)\n'
        f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>জিমেইল:</b> <code>{req.get("target_email")}</code>\n'
        f'<tg-emoji emoji-id="5463289097336405244">🔐</tg-emoji> <b>বিক্রয়কৃত পাসওয়ার্ড:</b> <code>{req.get("password") or "N/A"}</code>\n'
        f'<tg-emoji emoji-id="5463289097336405244">🔑</tg-emoji> <b>ডেলিভারিকৃত ক্রেডেনশিয়াল:</b>\n<code>{req.get("credentials")}</code>\n\n'
        f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>ফেরতযোগ্য অর্থ:</b> <b>{fmt_bal(req.get("refund_amt", 5.0))} ৳</b>\n'
        f'<tg-emoji emoji-id="5213349767672769194">⏰</tg-emoji> <b>দাবির সময়:</b> {claim_str}\n'
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f'<tg-emoji emoji-id="5463289097336405244">👇</tg-emoji> কী ব্যবস্থা নিতে চান নির্বাচন করুন:'
    )

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("অ্যাপ্রুভ (Approve)", callback_data=f"badgmail_app_{req_id}_{idx}", icon_custom_emoji_id="5213406375341731253"),
        InlineKeyboardButton("রিজেক্ট (Reject)", callback_data=f"badgmail_rej_{req_id}_{idx}", icon_custom_emoji_id="5215642288071387368")
    )
    markup.add(
        InlineKeyboardButton("গুগল চেক (Test Google)", callback_data=f"badgmail_test_{req_id}_{idx}", icon_custom_emoji_id="5447410659077661506"),
        InlineKeyboardButton("ফাইল ডাউনলোড", callback_data="repl_file_menu", icon_custom_emoji_id="4967656361073574498")
    )
    
    nav_row = []
    if idx > 0:
        nav_row.append(InlineKeyboardButton("আগেরটি", callback_data=f"badgmail_repl_{idx - 1}", icon_custom_emoji_id="5220079633533250496"))
    if idx < len(pending_list) - 1:
        nav_row.append(InlineKeyboardButton("পরেরটি", callback_data=f"badgmail_repl_{idx + 1}", icon_custom_emoji_id="5463289097336405244"))
    if nav_row:
        markup.row(*nav_row)

    if len(pending_list) > 1:
        markup.row(
            InlineKeyboardButton("সব অ্যাপ্রুভ (All)", callback_data="badgmail_appall_prompt", icon_custom_emoji_id="5213406375341731253"),
            InlineKeyboardButton("সব রিজেক্ট (All)", callback_data="badgmail_rejall_prompt", icon_custom_emoji_id="5215642288071387368")
        )

    markup.add(InlineKeyboardButton("ব্যাড জিমেইল মেনু", callback_data="badgmail_main", icon_custom_emoji_id="5220079633533250496"))

    try:
        bot.edit_message_text(msg, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")
    except:
        bot.send_message(chat_id, msg, reply_markup=markup, parse_mode="HTML")

ADMIN_REPL_CALLBACKS = {
    'repl_panel', 'repl_back_admin', 'repl_file_accept_prompt',
    'repl_file_reject_prompt', 'repl_file_menu', 'repl_dl_xlsx', 'repl_dl_txt'
}

@bot.callback_query_handler(func=lambda call: call.data.startswith('badgmail_') or call.data in ADMIN_REPL_CALLBACKS or call.data.startswith('toggle_checker_'))
def handle_bad_gmail_callback(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Unauthorized.", show_alert=True)
        return

    action = call.data

    if action == "repl_panel":
        send_admin_replacement_panel(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)
        return

    elif action == "repl_back_admin":
        bot.send_message(call.message.chat.id, "Returning to Admin Panel...", reply_markup=get_admin_menu())
        bot.answer_callback_query(call.id)
        return

    elif action == "repl_file_accept_prompt":
        bot.answer_callback_query(call.id)
        msg_text = (
            '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>ফাইল দিয়ে এক্সেপ্ট (Bulk Accept by File)</b>\n'
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "দয়া করে যে জিমেইলগুলো আপনি এক্সেপ্ট ও রিফান্ড করতে চান, সেগুলোর <b>.txt</b> বা <b>.xlsx</b> ফাইল আপলোড করুন অথবা মেসেজে লিখে পাঠান (প্রতি লাইনে একটি করে জিমেইল)।\n\n"
            "⚡ <i>যেসকল জিমেইল পেন্ডিং তালিকায় পাওয়া যাবে, স্বয়ংক্রিয়ভাবে সেগুলোর অর্থ ক্রেতার ওয়ালেটে ব্যাক যাবে এবং ক্রেতাকে নোটিফিকেশন দেওয়া হবে।</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "(বাতিল করতে <b>Return to Admin</b> লিখুন)"
        )
        msg = bot.send_message(call.message.chat.id, msg_text, parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
        bot.register_next_step_handler(msg, process_bulk_accept_replacement)
        return

    elif action == "repl_file_reject_prompt":
        bot.answer_callback_query(call.id)
        msg_text = (
            '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>ফাইল দিয়ে রিজেক্ট (Bulk Reject by File)</b>\n'
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "দয়া করে যে জিমেইলগুলো আপনি বাতিল (Reject) করতে চান, সেগুলোর <b>.txt</b> বা <b>.xlsx</b> ফাইল আপলোড করুন অথবা মেসেজে লিখে পাঠান (প্রতি লাইনে একটি করে জিমেইল)।\n\n"
            "⚠️ <i>এক্ষেত্রে ক্রেতা কোনো টাকা ব্যাক পাবে না এবং ক্রেতাকে জানানো হবে যে তার জিমেইলটি রিপ্লেসমেন্টের জন্য অযোগ্য।</i>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "(বাতিল করতে <b>Return to Admin</b> লিখুন)"
        )
        msg = bot.send_message(call.message.chat.id, msg_text, parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
        bot.register_next_step_handler(msg, process_bulk_reject_replacement)
        return

    if action == "badgmail_main":
        count = bad_gmail_collection.count_documents({})
        pending_count = replacement_requests_collection.count_documents({"status": "pending"})
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton(f"রিভিউ ({pending_count} টি)", callback_data="badgmail_repl_0", icon_custom_emoji_id="5212988801441344587"),
            InlineKeyboardButton(f"ফাইল ({pending_count} টি)", callback_data="repl_file_menu", icon_custom_emoji_id="4967656361073574498")
        )
        markup.row(
            InlineKeyboardButton("ব্যাড জিমেইল ফাইল", callback_data="badgmail_file", icon_custom_emoji_id="4967656361073574498"),
            InlineKeyboardButton("ক্লিয়ার (Clear)", callback_data="badgmail_clear_prompt", icon_custom_emoji_id="5212992409213872592")
        )
        msg = (
            f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>Bad Gmail & Replacement Management</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <b>পেন্ডিং রিপ্লেসমেন্ট দাবি:</b> <b>{pending_count}</b> টি\n'
            f'<tg-emoji emoji-id="5395463407589672312">📦</tg-emoji> <b>মোট ব্যাড জিমেইল জমা:</b> {count:,} টি\n\n'
            f'<tg-emoji emoji-id="5463289097336405244">👇</tg-emoji> নিচের যে কোনো একটি অপশন নির্বাচন করুন:'
        )
        try:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        except:
            pass
        bot.answer_callback_query(call.id)
        return

    elif action.startswith("badgmail_repl_"):
        parts = action.split("_")
        idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        pending_list = list(replacement_requests_collection.find({"status": "pending"}).sort("claimed_at", 1))
        bot.answer_callback_query(call.id)
        show_badgmail_repl_item(call.message.chat.id, call.message.message_id, pending_list, idx)
        return

    elif action.startswith("badgmail_test_"):
        parts = action.split("_")
        req_id = parts[2]
        from bson.objectid import ObjectId
        req = replacement_requests_collection.find_one({"_id": ObjectId(req_id)})
        if not req:
            bot.answer_callback_query(call.id, "রিকোয়েস্ট পাওয়া যায়নি!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "গুগল সার্ভারে যাচাই করা হচ্ছে...")
        em = req.get("target_email")
        pwd = req.get("password") or extract_password_from_cred(req.get("credentials", ""))
        is_wrong, detail = check_google_wrong_password(em, pwd)
        status_str = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> রং পাসওয়ার্ড প্রমাণিত (Google: Wrong Password)' if is_wrong else '<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> পাসওয়ার্ড সঠিক রয়েছে (Valid Password)'
        bot.send_message(
            call.message.chat.id,
            f'<tg-emoji emoji-id="5447410659077661506">🔍</tg-emoji> <b>গুগল সার্ভার টেস্ট রেজাল্ট:</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f'<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <b>জিমেইল:</b> <code>{em}</code>\n'
            f'<tg-emoji emoji-id="5213403875670765022">🔑</tg-emoji> <b>টেস্টেড পাসওয়ার্ড:</b> <code>{pwd}</code>\n'
            f'<tg-emoji emoji-id="5463289097336405244">📊</tg-emoji> <b>ফলাফল:</b> {status_str}\n'
            f'<tg-emoji emoji-id="5249288301659041068">🌐</tg-emoji> <b>গুগল মেসেজ:</b> <code>{detail}</code>\n'
            f"━━━━━━━━━━━━━━━━━━━━━",
            parse_mode="HTML"
        )
        return

    elif action.startswith("badgmail_app_"):
        parts = action.split("_")
        req_id = parts[2]
        idx = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from bson.objectid import ObjectId
        req = replacement_requests_collection.find_one({"_id": ObjectId(req_id)})
        if not req or req.get("status") != "pending":
            bot.answer_callback_query(call.id, "এই রিকোয়েস্টটি ইতোমধ্যে নিষ্পত্তি হয়েছে!", show_alert=True)
            pending_list = list(replacement_requests_collection.find({"status": "pending"}).sort("claimed_at", 1))
            show_badgmail_repl_item(call.message.chat.id, call.message.message_id, pending_list, idx)
            return

        target_uid = req["user_id"]
        refund_amt = float(req.get("refund_amt", 5.0))
        target_email = req.get("target_email")

        # Refund to user
        users_collection.update_one({"chat_id": target_uid}, {"$inc": {"balance": refund_amt}})
        u = users_collection.find_one({"chat_id": target_uid})
        new_bal = u.get("balance", 0.0) if u else 0.0

        replacement_requests_collection.update_one(
            {"_id": req["_id"]},
            {"$set": {
                "status": "approved",
                "processed_at": datetime.now(timezone.utc),
                "processed_by": call.from_user.id
            }}
        )

        if req.get("order_id"):
            vpn_collection.update_one(
                {"_id": req["order_id"]},
                {"$set": {
                    "status": "refunded",
                    "is_replaced": True,
                    "refund_amount": refund_amt,
                    "refunded_at": datetime.now(timezone.utc)
                }}
            )

        bad_gmail_collection.insert_one({
            "email": target_email,
            "credentials": req.get("credentials", ""),
            "failed_at": datetime.now(timezone.utc),
            "user_id": target_uid,
            "reason": "wrong_password_approved"
        })

        # Send approval notification to customer
        user_lang = get_user_lang(target_uid)
        is_bd = (user_lang == 'bn')
        if is_bd:
            cust_msg = (
                f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>রং পাসওয়ার্ড রিকোয়েস্ট অনুমোদিত!</b>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>জিমেইল:</b> <code>{target_email}</code>\n'
                f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>ফেরতকৃত অর্থ:</b> <b>+{fmt_bal(refund_amt)} ৳</b>\n'
                f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>আপনার বর্তমান ব্যালেন্স:</b> <b>{fmt_bal(new_bal)} ৳</b>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <i>অ্যাডমিন আপনার রিকোয়েস্টটি রিভিউ করে অনুমোদন করেছেন। পুরো টাকা আপনার ওয়ালেটে ফেরত দেওয়া হয়েছে।</i>'
            )
        else:
            cust_msg = (
                f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>Wrong Password Request Approved!</b>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>Gmail:</b> <code>{target_email}</code>\n'
                f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Refunded Amount:</b> <b>+{fmt_bal(refund_amt)} ৳</b>\n'
                f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Current Balance:</b> <b>{fmt_bal(new_bal)} ৳</b>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <i>Admin reviewed and approved your request. Full refund has been credited to your wallet.</i>'
            )
        try:
            bot.send_message(target_uid, cust_msg, parse_mode="HTML")
        except Exception as e:
            print(f"Error sending approval notice to user {target_uid}: {e}")

        bot.answer_callback_query(call.id, f"অনুমোদিত! ইউজারের ওয়ালেটে {fmt_bal(refund_amt)} ৳ যোগ হয়েছে।", show_alert=False)

        pending_list = list(replacement_requests_collection.find({"status": "pending"}).sort("claimed_at", 1))
        show_badgmail_repl_item(call.message.chat.id, call.message.message_id, pending_list, min(idx, len(pending_list) - 1))
        return

    elif action.startswith("badgmail_rej_"):
        parts = action.split("_")
        req_id = parts[2]
        idx = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        from bson.objectid import ObjectId
        req = replacement_requests_collection.find_one({"_id": ObjectId(req_id)})
        if not req or req.get("status") != "pending":
            bot.answer_callback_query(call.id, "এই রিকোয়েস্টটি ইতোমধ্যে নিষ্পত্তি হয়েছে!", show_alert=True)
            pending_list = list(replacement_requests_collection.find({"status": "pending"}).sort("claimed_at", 1))
            show_badgmail_repl_item(call.message.chat.id, call.message.message_id, pending_list, idx)
            return

        target_uid = req["user_id"]
        target_email = req.get("target_email")

        replacement_requests_collection.update_one(
            {"_id": req["_id"]},
            {"$set": {
                "status": "rejected",
                "processed_at": datetime.now(timezone.utc),
                "processed_by": call.from_user.id
            }}
        )

        if req.get("order_id"):
            vpn_collection.update_one(
                {"_id": req["order_id"]},
                {"$set": {"status": "replacement_rejected"}}
            )

        # Send rejection notification to customer
        user_lang = get_user_lang(target_uid)
        is_bd = (user_lang == 'bn')
        if is_bd:
            cust_msg = (
                f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>রং পাসওয়ার্ড রিকোয়েস্ট বাতিল (Rejected)</b>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>জিমেইল:</b> <code>{target_email}</code>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>দুঃখিত, আপনার রিকোয়েস্টটি বাতিল করা হয়েছে। এটি সঠিক জিমেইল ছিল এবং আপনি এটি ইতিমধ্যে ব্যবহার করেছেন, স্যার।</i>'
            )
        else:
            cust_msg = (
                f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>Wrong Password Request Rejected</b>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>Gmail:</b> <code>{target_email}</code>\n'
                f'━━━━━━━━━━━━━━━━━━━━━\n'
                f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>Sorry, your request has been rejected. This was a valid Gmail and you have already used it, sir.</i>'
            )
        try:
            bot.send_message(target_uid, cust_msg, parse_mode="HTML")
        except Exception as e:
            print(f"Error sending rejection notice to user {target_uid}: {e}")

        bot.answer_callback_query(call.id, "রিকোয়েস্ট বাতিল করা হয়েছে এবং ইউজারকে নোটিফিকেশন পাঠানো হয়েছে।", show_alert=False)

        pending_list = list(replacement_requests_collection.find({"status": "pending"}).sort("claimed_at", 1))
        show_badgmail_repl_item(call.message.chat.id, call.message.message_id, pending_list, min(idx, len(pending_list) - 1))
        return

    elif action == "badgmail_appall_prompt":
        pending_count = replacement_requests_collection.count_documents({"status": "pending"})
        if pending_count == 0:
            bot.answer_callback_query(call.id, "কোনো পেন্ডিং রিকোয়েস্ট নেই!", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("হ্যাঁ, সবগুলো অ্যাপ্রুভ করুন", callback_data="badgmail_appall_confirm", icon_custom_emoji_id="5213406375341731253"),
            InlineKeyboardButton("বাতিল", callback_data="badgmail_repl_0", icon_custom_emoji_id="5215642288071387368")
        )
        msg = (
            f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>নিশ্চিতকরণ: সবগুলো অ্যাপ্রুভ</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"আপনি কি পেন্ডিং থাকা সকল (<b>{pending_count}</b> টি) রিকোয়েস্ট এক সাথে অ্যাপ্রুভ করে ব্যালেন্স রিফান্ড করতে চান?"
        )
        bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return

    elif action == "badgmail_appall_confirm":
        pending_list = list(replacement_requests_collection.find({"status": "pending"}))
        if not pending_list:
            bot.answer_callback_query(call.id, "কোনো পেন্ডিং রিকোয়েস্ট নেই!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "সবগুলো অ্যাপ্রুভ হচ্ছে...")
        for req in pending_list:
            target_uid = req["user_id"]
            refund_amt = float(req.get("refund_amt", 5.0))
            target_email = req.get("target_email")
            users_collection.update_one({"chat_id": target_uid}, {"$inc": {"balance": refund_amt}})
            u = users_collection.find_one({"chat_id": target_uid})
            new_bal = u.get("balance", 0.0) if u else 0.0
            replacement_requests_collection.update_one({"_id": req["_id"]}, {"$set": {"status": "approved", "processed_at": datetime.now(timezone.utc), "processed_by": call.from_user.id}})
            if req.get("order_id"):
                vpn_collection.update_one({"_id": req["order_id"]}, {"$set": {"status": "refunded", "is_replaced": True, "refund_amount": refund_amt, "refunded_at": datetime.now(timezone.utc)}})
            bad_gmail_collection.insert_one({"email": target_email, "credentials": req.get("credentials", ""), "failed_at": datetime.now(timezone.utc), "user_id": target_uid, "reason": "wrong_password_approved"})
            try:
                bot.send_message(
                    target_uid,
                    f'<tg-emoji emoji-id="6169979524411825292">✅</tg-emoji> <b>রং পাসওয়ার্ড রিকোয়েস্ট অনুমোদিত!</b>\n━━━━━━━━━━━━━━━━━━━━━\n<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <b>জিমেইল:</b> <code>{target_email}</code>\n<tg-emoji emoji-id="6170011680831969294">💵</tg-emoji> <b>ফেরতকৃত অর্থ:</b> <b>+{fmt_bal(refund_amt)} ৳</b>\n<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>আপনার বর্তমান ব্যালেন্স:</b> <b>{fmt_bal(new_bal)} ৳</b>\n━━━━━━━━━━━━━━━━━━━━━\n<tg-emoji emoji-id="5431644246450908867">⚡</tg-emoji> <i>অ্যাডমিন আপনার রিকোয়েস্টটি অনুমোদন করেছেন। টাকা ওয়ালেটে যোগ করা হয়েছে।</i>',
                    parse_mode="HTML"
                )
            except:
                pass
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("ব্যাড জিমেইল মেনু", callback_data="badgmail_main", icon_custom_emoji_id="5220079633533250496"))
        bot.edit_message_text(f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> সফলভাবে সকল (<b>{len(pending_list)}</b> টি) রিকোয়েস্ট অ্যাপ্রুভ ও রিফান্ড করা হয়েছে!', chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        return

    elif action == "badgmail_rejall_prompt":
        pending_count = replacement_requests_collection.count_documents({"status": "pending"})
        if pending_count == 0:
            bot.answer_callback_query(call.id, "কোনো পেন্ডিং রিকোয়েস্ট নেই!", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("হ্যাঁ, সবগুলো রিজেক্ট করুন", callback_data="badgmail_rejall_confirm", icon_custom_emoji_id="5215642288071387368"),
            InlineKeyboardButton("বাতিল", callback_data="badgmail_repl_0", icon_custom_emoji_id="5220079633533250496")
        )
        msg = (
            f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>নিশ্চিতকরণ: সবগুলো রিজেক্ট</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"আপনি কি পেন্ডিং থাকা সকল (<b>{pending_count}</b> টি) রিকোয়েস্ট বাতিল (Reject) করতে চান? কোনো রিফান্ড যাবে না।"
        )
        bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)
        return

    elif action == "badgmail_rejall_confirm":
        pending_list = list(replacement_requests_collection.find({"status": "pending"}))
        if not pending_list:
            bot.answer_callback_query(call.id, "কোনো পেন্ডিং রিকোয়েস্ট নেই!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "সবগুলো রিজেক্ট হচ্ছে...")
        for req in pending_list:
            target_uid = req["user_id"]
            target_email = req.get("target_email")
            replacement_requests_collection.update_one({"_id": req["_id"]}, {"$set": {"status": "rejected", "processed_at": datetime.now(timezone.utc), "processed_by": call.from_user.id}})
            if req.get("order_id"):
                vpn_collection.update_one({"_id": req["order_id"]}, {"$set": {"status": "replacement_rejected"}})
            try:
                bot.send_message(
                    target_uid,
                    f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>রং পাসওয়ার্ড রিকোয়েস্ট বাতিল (Rejected)</b>\n━━━━━━━━━━━━━━━━━━━━━\n<tg-emoji emoji-id="4967656361073574498">📧</tg-emoji> <b>জিমেইল:</b> <code>{target_email}</code>\n━━━━━━━━━━━━━━━━━━━━━\n<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>দুঃখিত, আপনার রিকোয়েস্টটি বাতিল করা হয়েছে। এটি সঠিক জিমেইল ছিল এবং আপনি এটি ইতিমধ্যে ব্যবহার করেছেন, স্যার।</i>',
                    parse_mode="HTML"
                )
            except:
                pass
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("ব্যাড জিমেইল মেনু", callback_data="badgmail_main", icon_custom_emoji_id="5220079633533250496"))
        bot.edit_message_text(f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> সফলভাবে সকল (<b>{len(pending_list)}</b> টি) রিকোয়েস্ট বাতিল (Reject) করা হয়েছে!', chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        return

    elif action == "repl_file_menu":
        pending_count = replacement_requests_collection.count_documents({"status": "pending"})
        total_count = replacement_requests_collection.count_documents({})
        if total_count == 0:
            bot.answer_callback_query(call.id, "কোনো রিপ্লেসমেন্ট রিকোয়েস্ট জমা নেই!", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Excel ফাইল (.xlsx)", callback_data="repl_dl_xlsx", icon_custom_emoji_id="5348125953090403204"),
            InlineKeyboardButton("TXT ফাইল (.txt)", callback_data="repl_dl_txt", icon_custom_emoji_id="4967656361073574498")
        )
        markup.add(InlineKeyboardButton("ব্যাড জিমেইল মেনু", callback_data="badgmail_main", icon_custom_emoji_id="5220079633533250496"))
        msg = (
            f'<tg-emoji emoji-id="5348125953090403204">📥</tg-emoji> <b>রিপ্লেসমেন্ট রিকোয়েস্ট ফাইল ডাউনলোড</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>পেন্ডিং রিকোয়েস্ট:</b> <b>{pending_count:,}</b> টি\n'
            f'<tg-emoji emoji-id="5395463407589672312">📦</tg-emoji> <b>সর্বমোট রিকোয়েস্ট:</b> <b>{total_count:,}</b> টি\n\n'
            f"কোন ফরম্যাটে ডাউনলোড করতে চান নির্বাচন করুন:"
        )
        try:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        except:
            pass
        bot.answer_callback_query(call.id)
        return

    elif action == "repl_dl_xlsx":
        pending_list = list(replacement_requests_collection.find({"status": "pending"}).sort("claimed_at", 1))
        if not pending_list:
            pending_list = list(replacement_requests_collection.find().sort("claimed_at", -1))
        if not pending_list:
            bot.answer_callback_query(call.id, "কোনো রিপ্লেসমেন্ট রিকোয়েস্ট নেই!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "Excel ফাইল তৈরি হচ্ছে...")

        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Replacements ({len(pending_list)})"

        header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
        border_thin = Border(
            left=Side(style='thin', color="E5E7EB"),
            right=Side(style='thin', color="E5E7EB"),
            top=Side(style='thin', color="E5E7EB"),
            bottom=Side(style='thin', color="E5E7EB")
        )

        headers = ["#", "Gmail", "Sold Password", "Delivered Credentials", "User ID", "Username", "Refund (৳)", "Claim Time (UTC)", "Status"]
        ws.append(headers)
        for col_num in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        row_font = Font(name="Consolas", size=10)
        for row_idx, req in enumerate(pending_list, start=2):
            em = req.get("target_email", "")
            pwd = req.get("password", "")
            cred = req.get("credentials", "")
            uid = str(req.get("user_id", ""))
            uname = req.get("username") or req.get("first_name", "")
            amt = float(req.get("refund_amt", 5.0))
            claim_time = req.get("claimed_at")
            c_str = claim_time.strftime("%Y-%m-%d %H:%M:%S") if isinstance(claim_time, datetime) else str(claim_time or "")
            st = req.get("status", "pending")

            ws.append([row_idx - 1, em, pwd, cred, uid, uname, amt, c_str, st])
            for col_idx in range(1, len(headers) + 1):
                c = ws.cell(row=row_idx, column=col_idx)
                c.font = row_font
                c.border = border_thin

        ws.column_dimensions["A"].width = 6
        ws.column_dimensions["B"].width = 30
        ws.column_dimensions["C"].width = 20
        ws.column_dimensions["D"].width = 45
        ws.column_dimensions["E"].width = 16
        ws.column_dimensions["F"].width = 18
        ws.column_dimensions["G"].width = 12
        ws.column_dimensions["H"].width = 22
        ws.column_dimensions["I"].width = 12

        file_data = io.BytesIO()
        wb.save(file_data)
        file_data.seek(0)
        file_data.name = f"Replacement_Requests_{len(pending_list)}.xlsx"

        bot.send_document(
            call.message.chat.id,
            file_data,
            caption=f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <b>মোট {len(pending_list):,} টি রিপ্লেসমেন্ট রিকোয়েস্ট রিপোর্ট</b> (.xlsx)',
            parse_mode="HTML"
        )
        return

    elif action == "repl_dl_txt":
        pending_list = list(replacement_requests_collection.find({"status": "pending"}).sort("claimed_at", 1))
        if not pending_list:
            pending_list = list(replacement_requests_collection.find().sort("claimed_at", -1))
        if not pending_list:
            bot.answer_callback_query(call.id, "কোনো রিপ্লেসমেন্ট রিকোয়েস্ট নেই!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "TXT ফাইল তৈরি হচ্ছে...")

        lines = []
        for req in pending_list:
            cred = (req.get("credentials") or "").strip()
            if cred:
                lines.append(cred)
            else:
                em = req.get("target_email", "")
                pwd = req.get("password", "")
                if pwd:
                    lines.append(f"{em}:{pwd}")
                else:
                    lines.append(em)

        file_data = io.BytesIO("\n".join(lines).encode('utf-8'))
        file_data.name = f"Replacement_Requests_{len(pending_list)}.txt"

        bot.send_document(
            call.message.chat.id,
            file_data,
            caption=f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <b>মোট {len(pending_list):,} টি রিপ্লেসমেন্ট রিকোয়েস্ট তালিকা</b> (.txt)',
            parse_mode="HTML"
        )
        return

    elif action == "badgmail_file":
        count = bad_gmail_collection.count_documents({})
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Excel ফাইল (.xlsx)", callback_data="badgmail_dl_xlsx", icon_custom_emoji_id="5348125953090403204"),
            InlineKeyboardButton("TXT ফাইল (.txt)", callback_data="badgmail_dl_txt", icon_custom_emoji_id="4967656361073574498")
        )
        markup.add(InlineKeyboardButton("ফিরে যান", callback_data="badgmail_main", icon_custom_emoji_id="5220079633533250496"))
        msg = (
            f'<tg-emoji emoji-id="4967656361073574498">📁</tg-emoji> <b>ব্যাড জিমেইল ফাইল ডাউনলোড</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"মোট জমা: <b>{count:,}</b> টি\n\n"
            f"কোন ফরম্যাটে ডাউনলোড করতে চান নির্বাচন করুন:"
        )
        try:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        except:
            pass
        bot.answer_callback_query(call.id)
        return

    elif action == "badgmail_dl_xlsx":
        count = bad_gmail_collection.count_documents({})
        if count == 0:
            bot.answer_callback_query(call.id, "কোন ব্যাড জিমেইল জমা নেই!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "ফাইল তৈরি হচ্ছে...")

        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"Bad Gmail ({count})"

        header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="DC2626", end_color="DC2626", fill_type="solid")
        border_thin = Border(
            left=Side(style='thin', color="E5E7EB"),
            right=Side(style='thin', color="E5E7EB"),
            top=Side(style='thin', color="E5E7EB"),
            bottom=Side(style='thin', color="E5E7EB")
        )

        ws.append(["Original / Combo Format", "Email Address", "Failed Date (UTC)", "Reason"])
        for col_num in range(1, 5):
            cell = ws.cell(row=1, column=col_num)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        row_font = Font(name="Consolas", size=10)
        docs = list(bad_gmail_collection.find().sort("failed_at", -1))
        for row_idx, doc in enumerate(docs, start=2):
            raw_c = doc.get("credentials", "")
            em = doc.get("email", "")
            f_date = doc.get("failed_at")
            f_str = f_date.strftime("%Y-%m-%d %H:%M:%S") if isinstance(f_date, datetime) else str(f_date or "")
            reason = doc.get("reason", "verify")

            ws.append([raw_c, em, f_str, reason])
            for col_idx in range(1, 5):
                c = ws.cell(row=row_idx, column=col_idx)
                c.font = row_font
                c.border = border_thin

        ws.column_dimensions["A"].width = 40
        ws.column_dimensions["B"].width = 30
        ws.column_dimensions["C"].width = 22
        ws.column_dimensions["D"].width = 18

        file_data = io.BytesIO()
        wb.save(file_data)
        file_data.seek(0)
        file_data.name = f"Bad_Gmails_{count}.xlsx"

        bot.send_document(
            call.message.chat.id,
            file_data,
            caption=f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>মোট {count:,} টি ব্যাড জিমেইল রিপোর্ট</b> (.xlsx)',
            parse_mode="HTML"
        )
        return

    elif action == "badgmail_dl_txt":
        count = bad_gmail_collection.count_documents({})
        if count == 0:
            bot.answer_callback_query(call.id, "কোন ব্যাড জিমেইল জমা নেই!", show_alert=True)
            return
        bot.answer_callback_query(call.id, "ফাইল তৈরি হচ্ছে...")

        docs = list(bad_gmail_collection.find().sort("failed_at", -1))
        lines = [doc.get("credentials", doc.get("email", "")).strip() for doc in docs if doc.get("credentials") or doc.get("email")]
        file_data = io.BytesIO("\n".join(lines).encode('utf-8'))
        file_data.name = f"Bad_Gmails_{count}.txt"

        bot.send_document(
            call.message.chat.id,
            file_data,
            caption=f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>মোট {count:,} টি ব্যাড জিমেইল তালিকা</b> (.txt)',
            parse_mode="HTML"
        )
        return

    elif action == "badgmail_clear_prompt":
        count = bad_gmail_collection.count_documents({})
        if count == 0:
            bot.answer_callback_query(call.id, "কোন ব্যাড জিমেইল জমা নেই!", show_alert=True)
            return
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("হ্যাঁ, সব ক্লিয়ার করুন", callback_data="badgmail_clear_confirm", icon_custom_emoji_id="5212992409213872592"),
            InlineKeyboardButton("না, বাতিল", callback_data="badgmail_main", icon_custom_emoji_id="5215642288071387368")
        )
        msg = (
            f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <b>সতর্কবার্তা: ব্যাড জিমেইল ক্লিয়ার</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"আপনি কি নিশ্চিতভাবে আগের জমার সকল ব্যাড জিমেইল (<b>{count:,}</b> টি) মুছে ফেলতে চান?\n\n"
            f"<i>ক্লিয়ার করার পর তালিকা খালি হয়ে যাবে এবং নতুন করে আবার জমা শুরু হবে।</i>"
        )
        try:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        except:
            pass
        bot.answer_callback_query(call.id)
        return

    elif action == "badgmail_clear_confirm":
        res = bad_gmail_collection.delete_many({})
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("ব্যাড জিমেইল মেনু", callback_data="badgmail_main", icon_custom_emoji_id="5220079633533250496"))
        msg = (
            f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>সফলভাবে ক্লিয়ার করা হয়েছে!</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"পূর্বের মোট <b>{res.deleted_count:,}</b> টি ব্যাড জিমেইল রেকর্ড ডাটাবেস থেকে সম্পূর্ণ মুছে ফেলা হয়েছে।\n\n"
            f"এখন থেকে যে সকল ব্যাড জিমেইল পাওয়া যাবে সেগুলো আবার নতুন করে এখানে জমা হতে থাকবে।"
        )
        try:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        except:
            pass
        bot.answer_callback_query(call.id, "ক্লিয়ার সম্পন্ন হয়েছে!")
        return

# --- GMAIL CHECKER MANAGEMENT HANDLERS ---
@bot.callback_query_handler(func=lambda call: call.data in ['toggle_checker_mails_so', 'toggle_checker_gmailchecklive', 'prompt_set_mails_so_key', 'test_active_gmail_checker', 'close_checker_menu'])
def handle_gmail_checker_callbacks(call):
    global CHECKER_CONFIG_LAST_UPDATE
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Unauthorized.", show_alert=True)
        return

    action = call.data

    if action == 'toggle_checker_mails_so':
        config_collection.update_one({"_id": "gmail_checker_settings"}, {"$set": {"active_checker": "mails_so"}}, upsert=True)
        CHECKER_CONFIG_LAST_UPDATE = 0
        try:
            bot.answer_callback_query(call.id, "Mails.so API চেকার সক্রিয় (ON) করা হয়েছে!", show_alert=True)
        except:
            pass
        try:
            bot.edit_message_text(get_gmail_checker_panel_text(), chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_gmail_checker_markup(), parse_mode="HTML")
        except:
            pass
        return

    elif action == 'toggle_checker_gmailchecklive':
        config_collection.update_one({"_id": "gmail_checker_settings"}, {"$set": {"active_checker": "gmailchecklive"}}, upsert=True)
        CHECKER_CONFIG_LAST_UPDATE = 0
        try:
            bot.answer_callback_query(call.id, "GmailCheckLive চেকার সক্রিয় (ON) করা হয়েছে!", show_alert=True)
        except:
            pass
        try:
            bot.edit_message_text(get_gmail_checker_panel_text(), chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_gmail_checker_markup(), parse_mode="HTML")
        except:
            pass
        return

    elif action == 'prompt_set_mails_so_key':
        try:
            bot.answer_callback_query(call.id)
        except:
            pass
        conf = get_checker_config()
        current_key = conf.get("mails_so_api_key", "7fe63f6c-0a3c-4b0f-83d3-caf9ea7f4f6a")
        msg_text = (
            f'<tg-emoji emoji-id="5463289097336405244">🔑</tg-emoji> <b>Mails.so API Key পরিবর্তন</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"বর্তমান Key: <code>{current_key}</code>\n\n"
            f"দয়া করে নতুন Mails.so API Key লিখে পাঠান:\n"
            f"(বাতিল করতে <b>Return to Admin</b> লিখুন)"
        )
        msg = bot.send_message(call.message.chat.id, msg_text, parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('Return to Admin'))
        bot.register_next_step_handler(msg, process_set_mails_so_key)
        return

    elif action == 'test_active_gmail_checker':
        try:
            bot.answer_callback_query(call.id, "চেকার টেস্ট করা হচ্ছে...")
        except:
            pass
        conf = get_checker_config()
        active = conf.get("active_checker", "mails_so")
        t0 = time.time()
        test_email = "kamrolhasan12398@gmail.com"
        res = check_gmail_batch_live([test_email])
        elapsed = round(time.time() - t0, 2)
        
        if res is not None:
            status_str = 'সফল (Online) <tg-emoji emoji-id="5206584567116352967">🟢</tg-emoji>'
            val = res.get(test_email)
            result_desc = f"<code>{test_email}</code> -> <b>{'সচল / Good <tg-emoji emoji-id=\"5213406375341731253\">✅</tg-emoji>' if val else 'বাতিল/ভেরিফাই / Dead <tg-emoji emoji-id=\"5215642288071387368\">❌</tg-emoji>'}</b>"
        else:
            status_str = 'ব্যর্থ (Offline / Error) <tg-emoji emoji-id="5215642288071387368">🔴</tg-emoji>'
            result_desc = "সার্ভার থেকে কোনো রেসপন্স পাওয়া যায়নি।"
            
        test_msg = (
            f'<tg-emoji emoji-id="5447410659077661506">🧪</tg-emoji> <b>চেকার টেস্ট ফলাফল ({active})</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f'<tg-emoji emoji-id="5249288301659041068">📡</tg-emoji> <b>স্ট্যাটাস:</b> {status_str}\n'
            f'<tg-emoji emoji-id="5213349767672769194">⏱️</tg-emoji> <b>রেসপন্স সময়:</b> {elapsed} সেকেন্ড\n'
            f'<tg-emoji emoji-id="5447410659077661506">🔍</tg-emoji> <b>ফলাফল:</b> {result_desc}\n'
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        bot.send_message(call.message.chat.id, test_msg, parse_mode="HTML")
        return

    elif action == 'close_checker_menu':
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.send_message(call.message.chat.id, "Store Stock Menu:", reply_markup=get_store_admin_menu())
        return

def process_set_mails_so_key(message):
    if message.chat.id not in ADMIN_IDS:
        return
    if message.text in ['🔙 Return to Admin', '🔙 Back to Admin', '❌ Cancel', '❌ বাতিল']:
        bot.send_message(message.chat.id, "বাতিল করা হয়েছে।", reply_markup=get_admin_menu())
        return
    new_key = message.text.strip()
    if not new_key or len(new_key) < 10:
        bot.send_message(message.chat.id, '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>অবৈধ API Key! দয়া করে সঠিক Key দিন।</b>', parse_mode="HTML", reply_markup=get_admin_menu())
        return
    config_collection.update_one({"_id": "gmail_checker_settings"}, {"$set": {"mails_so_api_key": new_key}}, upsert=True)
    global CHECKER_CONFIG_LAST_UPDATE; CHECKER_CONFIG_LAST_UPDATE = 0
    bot.send_message(
        message.chat.id,
        f'<tg-emoji emoji-id="5213406375341731253">✅</tg-emoji> <b>Mails.so API Key সফলভাবে আপডেট করা হয়েছে!</b>\n<code>{new_key}</code>',
        parse_mode="HTML",
        reply_markup=get_admin_menu()
    )
    bot.send_message(message.chat.id, get_gmail_checker_panel_text(), reply_markup=get_gmail_checker_markup(), parse_mode="HTML")


# ====================================================
# GMAIL REPLACEMENT SYSTEM
# ====================================================

def check_google_wrong_password(email, password):
    """
    Checks Google account authentication.
    Returns (is_wrong_password: bool, detail_message: str)
    """
    if not email or not password:
        return False, "Email or password missing"
    url = "https://android.clients.google.com/auth"
    headers = {
        "User-Agent": "GoogleAuth/1.4 (mako JDQ39E)",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "Email": email,
        "Passwd": password,
        "service": "ac2dm",
        "accountType": "HOSTED_OR_GOOGLE",
        "has_permission": "1",
        "source": "android",
        "androidId": "9774d56d682e549c",
        "device_country": "us",
        "operatorCountry": "us",
        "lang": "en",
        "sdk_version": "17"
    }
    try:
        resp = requests.post(url, headers=headers, data=data, timeout=10)
        text = resp.text
        # BadAuthentication indicates wrong password / invalid credentials
        if "BadAuthentication" in text or (resp.status_code == 403 and "badauthentication" in text.lower()):
            return True, 'Wrong password. Try again or click "Forgot password?" for more options.'
        elif "NeedsBrowser" in text or "Token=" in text or "Auth=" in text or resp.status_code == 200:
            return False, "Password is valid"
        elif "CaptchaRequired" in text or "DeviceManagementRequiredOrSyncDisabled" in text:
            return False, "Password is valid (Verification active)"
        else:
            if resp.status_code == 403:
                return True, 'Wrong password. Try again or click "Forgot password?" for more options.'
            return False, text
    except Exception as e:
        print(f"[Google Auth Check Error] {email}: {e}")
        return False, f"Network error: {e}"

def extract_email_and_password(raw_line):
    if not raw_line:
        return None, None
    line = raw_line.strip()
    email = parse_gmail_address(line)
    if not email:
        return None, None
    
    remainder = ""
    idx = line.lower().find(email.lower())
    if idx != -1:
        after_email = line[idx + len(email):].strip()
        if after_email and after_email[0] in [':', '|', '\t', ' ', ';']:
            remainder = after_email[1:].strip()
        else:
            remainder = after_email
            
    if not remainder:
        return email, None
        
    tokens = re.split(r'[:|\t ;]', remainder)
    tokens = [t.strip() for t in tokens if t.strip()]
    if tokens:
        return email, tokens[0]
    return email, None

def extract_password_from_cred(cred_str):
    _, pwd = extract_email_and_password(cred_str)
    return pwd or ""

@bot.callback_query_handler(func=lambda call: call.data == 'claim_repl_gmail')
def handle_claim_repl_gmail(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    is_bd = (lang == 'bn')
    
    try:
        bot.answer_callback_query(call.id)
    except:
        pass
        
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("ভেরিফাই সমস্যা" if is_bd else "Verify Problem", callback_data="user_repl_prob_verify", style="danger", icon_custom_emoji_id="5240241223632954241"),
        InlineKeyboardButton("পাসওয়ার্ড চেঞ্জ" if is_bd else "Password Change", callback_data="user_repl_prob_passchange", style="primary", icon_custom_emoji_id="5870972873450984431"),
        InlineKeyboardButton("রং পাসওয়ার্ড" if is_bd else "Wrong Password", callback_data="user_repl_prob_wrongpass", style="danger", icon_custom_emoji_id="5212988801441344587"),
        InlineKeyboardButton("ফিরে যান" if is_bd else "Back", callback_data="claim_repl_back", style="primary", icon_custom_emoji_id="5416113713428057601")
    )

    if is_bd:
        msg_text = (
            '<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji> <b>জিমেইল রিপ্লেসমেন্ট সার্ভিস</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'আপনার জিমেইলে কোন সমস্যা হচ্ছে দয়া করে নির্বাচন করুন:'
        )
    else:
        msg_text = (
            '<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji> <b>Gmail Replacement Service</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'Please select what problem you are facing with your Gmail:'
        )

    try:
        bot.edit_message_text(msg_text, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except:
        bot.send_message(user_id, msg_text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data == 'claim_repl_back')
def handle_claim_repl_back(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    is_bd = (lang == 'bn')
    try:
        bot.answer_callback_query(call.id)
    except:
        pass
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("Gmail", callback_data="claim_repl_gmail", style="primary", icon_custom_emoji_id="6118546560897781055")
    )
    if is_bd:
        repl_prompt = (
            '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <b>অ্যাকাউন্ট রিপ্লেসমেন্ট সার্ভিস</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'আমাদের বট থেকে ক্রয়কৃত কোনো জিমেইল অ্যাকাউন্টে সমস্যা থাকলে নিচে <b>Gmail</b> বাটনে ক্লিক করে রিপ্লেসমেন্ট রিকোয়েস্ট পাঠান:'
        )
    else:
        repl_prompt = (
            '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <b>Account Replacement Service</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'If any Gmail purchased from our store has an issue, click the <b>Gmail</b> button below:'
        )
    try:
        bot.edit_message_text(repl_prompt, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except:
        bot.send_message(user_id, repl_prompt, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ['repl_prob_verify', 'user_repl_prob_verify'])
def handle_repl_prob_verify(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    is_bd = (lang == 'bn')
    try:
        bot.answer_callback_query(call.id)
    except:
        pass
        
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("ফিরে যান" if is_bd else "Back", callback_data="claim_repl_gmail", style="primary", icon_custom_emoji_id="5416113713428057601")
    )
    if is_bd:
        msg_text = (
            '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>ভেরিফাই সমস্যা গ্রহণযোগ্য নয়</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'আমাদের জিমেইল কেনার সময়েই ভেরিফাই চেক করে দিই, তাই আপনার ভেরিফাই জিমেইল গ্রহণযোগ্য নয়।\n\n'
            '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>ক্রয় করার মুহূর্তে প্রতিটি জিমেইল লাইভ সার্ভার দিয়ে স্বয়ংক্রিয়ভাবে যাচাই করে সচল অবস্থায় ডেলিভারি দেওয়া হয়। তাই পরবর্তীতে ভেরিফাই সমস্যার জন্য কোনো রিপ্লেসমেন্ট বা রিফান্ড প্রযোজ্য নয়।</i>\n'
            '━━━━━━━━━━━━━━━━━━━━━'
        )
    else:
        msg_text = (
            '<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>Verification Issue Not Accepted</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'We live-check Gmail verification at the time of purchase, so verify claims are not accepted.\n\n'
            '<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>Every Gmail account is live-checked at the time of purchase and delivered fully active. Therefore, verification issues are not eligible for replacement or refund.</i>\n'
            '━━━━━━━━━━━━━━━━━━━━━'
        )
    try:
        bot.edit_message_text(msg_text, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except:
        bot.send_message(user_id, msg_text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ['repl_prob_passchange', 'user_repl_prob_passchange'])
def handle_repl_prob_passchange(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    is_bd = (lang == 'bn')
    try:
        bot.answer_callback_query(call.id)
    except:
        pass
        
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("ফিরে যান" if is_bd else "Back", callback_data="claim_repl_gmail", style="primary", icon_custom_emoji_id="5416113713428057601")
    )
    if is_bd:
        msg_text = (
            '<tg-emoji emoji-id="5870972873450984431">🔑</tg-emoji> <b>জিমেইল পাসওয়ার্ড চেঞ্জ / লগইন সমাধান নির্দেশিকা</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'আপনি যদি জিমেইলে লগইন বা পাসওয়ার্ড পরিবর্তনে সমস্যা অনুভব করেন, তবে নিচের ধাপগুলো অনুসরণ করুন:\n\n'
            '<tg-emoji emoji-id="5305538504787246900">1⃣</tg-emoji> <b>প্রথম ধাপ:</b>\n'
            'প্রথমে গুগল জিমেইল লগইন পেজে যান। আপনার জিমেইলটি বসিয়ে <b>Next</b> বাটনে চাপ দিন।\n\n'
            '<tg-emoji emoji-id="5305551505653250543">2⃣</tg-emoji> <b>দ্বিতীয় ধাপ:</b>\n'
            'পাসওয়ার্ড না মিললে বা সমস্যা হলে নিচে থাকা <b>Forgot password?</b> অপশনে ক্লিক করুন।\n\n'
            '<tg-emoji emoji-id="5305599007991545064">3⃣</tg-emoji> <b>তৃতীয় ধাপ:</b>\n'
            'এরপর গুগল আপনার কাছে আগের পাসওয়ার্ড জানতে চাইবে (<i>"Enter last password"</i>)। সেখানে আমাদের বট থেকে আপনাকে দেওয়া পাসওয়ার্ডটি প্রবেশ করান।\n\n'
            '<tg-emoji emoji-id="5305464708659165869">4⃣</tg-emoji> <b>চতুর্থ ধাপ:</b>\n'
            'এরপর আপনার নতুন পাসওয়ার্ড সেট করুন অথবা <b>Finish / Continue</b> দিন — সাথে সাথেই আপনার জিমেইল সফলভাবে লগইন হয়ে যাবে!\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            '<tg-emoji emoji-id="5463289097336405244">💡</tg-emoji> <i>এই পদ্ধতিতে আপনি খুব সহজেই আপনার জিমেইলে প্রবেশ করতে পারবেন।</i>'
        )
    else:
        msg_text = (
            '<tg-emoji emoji-id="5870972873450984431">🔑</tg-emoji> <b>Password Change / Login Guide</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'If you are having trouble logging in or changing your password, please follow these steps:\n\n'
            '<tg-emoji emoji-id="5305538504787246900">1⃣</tg-emoji> <b>Step 1:</b>\n'
            'Go to the Gmail login page. Enter your Gmail and click <b>Next</b>.\n\n'
            '<tg-emoji emoji-id="5305551505653250543">2⃣</tg-emoji> <b>Step 2:</b>\n'
            'Click on the <b>Forgot password?</b> option below.\n\n'
            '<tg-emoji emoji-id="5305599007991545064">3⃣</tg-emoji> <b>Step 3:</b>\n'
            'Google will ask for your previous password (<i>"Enter last password"</i>). Enter the password provided by our bot.\n\n'
            '<tg-emoji emoji-id="5305464708659165869">4⃣</tg-emoji> <b>Step 4:</b>\n'
            'Set your new password or click <b>Finish / Continue</b> — you will immediately be logged in successfully!\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            '<tg-emoji emoji-id="5463289097336405244">💡</tg-emoji> <i>This method will smoothly get you into your account.</i>'
        )
    try:
        bot.edit_message_text(msg_text, chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except:
        bot.send_message(user_id, msg_text, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data in ['repl_prob_wrongpass', 'user_repl_prob_wrongpass'])
def handle_repl_prob_wrongpass(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    is_bd = (lang == 'bn')
    try:
        bot.answer_callback_query(call.id)
    except:
        pass

    if is_bd:
        prompt_text = (
            '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <b>রং পাসওয়ার্ড - জিমেইল সাবমিট</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'অনুগ্রহ করে আপনার যে জিমেইলটিতে রং পাসওয়ার্ড দেখাচ্ছে সেটি পাঠান।'
        )
    else:
        prompt_text = (
            '<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <b>Wrong Password - Submit Gmail</b>\n'
            '━━━━━━━━━━━━━━━━━━━━━\n'
            'Please send the Gmail account that shows Wrong Password.'
        )
    msg = bot.send_message(user_id, prompt_text, parse_mode="HTML", reply_markup=get_cancel_menu(lang))
    bot.register_next_step_handler(msg, process_wrong_password_check)

def process_wrong_password_check(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    is_bd = (lang == 'bn')
    
    # 1. Check for cancel / back navigation
    if message.text and (message.text in ['❌ Cancel', '❌ বাতিল', '🔙 Back', '🔙 ফিরে যান', '❌  (Cancel)', '🔙 '] or is_back_or_cancel_text(message.text) or message.text in texts['en'].values() or message.text in texts['bn'].values()):
        cancel_msg = '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> রিকোয়েস্ট বাতিল করা হয়েছে।' if is_bd else '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> Request cancelled.'
        bot.send_message(user_id, cancel_msg, reply_markup=get_main_menu(lang, user_id))
        return
        
    # 2. Extract lines from text or uploaded file (.txt or .xlsx)
    raw_lines = []
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            file_name = (message.document.file_name or "").lower()
            if file_name.endswith(".xlsx") or file_name.endswith(".xls"):
                raw_lines = parse_xlsx_to_lines(downloaded_file)
            else:
                raw_text = decode_file_content(downloaded_file)
                raw_lines = raw_text.splitlines()
        except Exception as e:
            bot.send_message(user_id, f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>ফাইল পড়তে সমস্যা হয়েছে:</b> {escape(str(e))}' if is_bd else f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>Error reading document:</b> {escape(str(e))}', parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
            return
    elif message.text:
        raw_lines = message.text.strip().splitlines()
    else:
        bot.send_message(user_id, '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>অনুগ্রহ করে টেক্সট অথবা .txt / .xlsx ফাইল দিন।</b>' if is_bd else '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>Please provide text or a .txt / .xlsx file.</b>', parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
        return

    # 3. Parse Gmail addresses and passwords from lines
    items_to_process = []
    seen = set()
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        em, pwd = extract_email_and_password(line)
        if em and em not in seen:
            seen.add(em)
            items_to_process.append({"email": em, "password": pwd, "raw": line})
            
    if not items_to_process:
        bot.send_message(user_id, '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>কোনো বৈধ জিমেইল পাওয়া যায়নি! অনুগ্রহ করে সঠিক তথ্য দিন।</b>' if is_bd else '<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <b>No valid Gmail found! Please provide valid Gmail details.</b>', parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
        return

    # 4. Processing status feedback
    loading_text = (
        f'<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> <b>তথ্য যাচাই করা হচ্ছে...</b>\n\n'
        f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <i>আপনার প্রেরিত {len(items_to_process)} টি জিমেইল ক্রয়ের তথ্য যাচাই করা হচ্ছে...</i>'
    ) if is_bd else (
        f'<tg-emoji emoji-id="5285184156555306745">⚡</tg-emoji> <b>Verifying Information...</b>\n\n'
        f'<tg-emoji emoji-id="5212988801441344587">🔄</tg-emoji> <i>Verifying purchase records for {len(items_to_process)} Gmail account(s)...</i>'
    )
    status_msg = bot.send_message(user_id, loading_text, parse_mode="HTML")

    # 5. Process accounts and submit for review
    now_utc = datetime.now(timezone.utc)
    pending_list = []
    rejected_not_found = []
    rejected_expired = []
    rejected_already = []

    user_obj = users_collection.find_one({"chat_id": user_id}) or {}
    uname = (user_obj.get("username") or "").lstrip("@")
    first_name = user_obj.get("first_name", "User")

    for item in items_to_process:
        target_email = item["email"]
        user_pass = item["password"]
        raw_line = item["raw"]
        
        # Check purchase history in vpn_collection
        matching_doc = None
        expired_doc = None
        user_orders = list(vpn_collection.find({"category": "Gmail", "buyer_id": user_id}).sort("sold_at", -1))
        for doc in user_orders:
            doc_em = parse_gmail_address(doc.get("credentials", ""))
            if doc_em and doc_em.lower() == target_email.lower():
                # Check 1-hour validity window
                sold_at = doc.get("sold_at") or doc.get("timestamp")
                if isinstance(sold_at, str):
                    try:
                        sold_at = datetime.fromisoformat(sold_at)
                    except:
                        pass
                if isinstance(sold_at, datetime):
                    if sold_at.tzinfo is None:
                        sold_at = sold_at.replace(tzinfo=timezone.utc)
                    if (now_utc - sold_at) <= timedelta(hours=1):
                        matching_doc = doc
                        break
                    else:
                        expired_doc = doc
                else:
                    expired_doc = doc
                
        if not matching_doc:
            if expired_doc:
                rejected_expired.append(target_email)
            else:
                rejected_not_found.append(target_email)
            continue
            
        if matching_doc.get("is_replaced") or matching_doc.get("status") in ["refunded", "replacement_pending"]:
            rejected_already.append(target_email)
            continue

        # Check if already in replacement_requests_collection
        existing_req = replacement_requests_collection.find_one({"target_email": target_email, "status": "pending"})
        if existing_req:
            rejected_already.append(target_email)
            continue

        # Extract password delivered by the store
        db_pass = extract_password_from_cred(matching_doc.get("credentials", ""))
        test_pass = db_pass if db_pass else user_pass

        refund_amt = matching_doc.get("price_paid") or get_product_price("Gmail")
        try:
            refund_amt = float(refund_amt)
        except:
            refund_amt = 5.0

        # Save request to replacement_requests_collection for Admin review
        req_doc = {
            "user_id": user_id,
            "username": uname,
            "first_name": first_name,
            "target_email": target_email,
            "credentials": matching_doc.get("credentials", raw_line),
            "user_input": raw_line,
            "password": test_pass or "",
            "sold_at": matching_doc.get("sold_at") or matching_doc.get("timestamp"),
            "order_id": matching_doc.get("_id"),
            "refund_amt": refund_amt,
            "status": "pending",
            "claimed_at": now_utc,
            "reason": "wrong_password"
        }
        replacement_requests_collection.insert_one(req_doc)

        if matching_doc.get("_id"):
            vpn_collection.update_one({"_id": matching_doc["_id"]}, {"$set": {"status": "replacement_pending"}})

        pending_list.append((target_email, refund_amt))

    # Delete loading message
    try:
        bot.delete_message(user_id, status_msg.message_id)
    except:
        pass

    # 6. Deliver responses to user
    if len(items_to_process) == 1:
        target_email = items_to_process[0]["email"]
        if pending_list:
            _, refund_amt = pending_list[0]
            if is_bd:
                msg_text = (
                    f'<tg-emoji emoji-id="5285184156555306745">⏳</tg-emoji> <b>আপনার রিকোয়েস্টটি রিভিউতে জমা হয়েছে</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>জিমেইল:</b> <code>{target_email}</code>\n'
                    f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>রিফান্ড দাবি:</b> <b>{fmt_bal(refund_amt)} ৳</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <i>আপনার রিকোয়েস্টটি রিভিউতে রয়েছে। কিছুক্ষণের মধ্যে রিভিউ করে টাকা দেওয়া হবে।</i>'
                )
            else:
                msg_text = (
                    f'<tg-emoji emoji-id="5285184156555306745">⏳</tg-emoji> <b>Your Request is Under Review</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>Gmail:</b> <code>{target_email}</code>\n'
                    f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>Refund Claim:</b> <b>{fmt_bal(refund_amt)} ৳</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <i>Your request is under review. Balance will be refunded shortly after review.</i>'
                )
            bot.send_message(user_id, msg_text, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
            return
            
        elif rejected_not_found:
            if is_bd:
                msg_text = (
                    f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>অ্যাকাউন্ট পাওয়া যায়নি</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>জিমেইল:</b> <code>{target_email}</code>\n\n'
                    f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>এই জিমেইলটি আমাদের বট থেকে এই টেলিগ্রাম আইডি দিয়ে ক্রয় করা হয়নি। শুধুমাত্র এই বট থেকে কেনা অ্যাকাউন্টের জন্য রিপ্লেসমেন্ট প্রযোজ্য।</i>'
                )
            else:
                msg_text = (
                    f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>Account Not Found</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>Gmail:</b> <code>{target_email}</code>\n\n'
                    f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>This Gmail was not purchased from our bot using this Telegram account. Only accounts bought from this bot are eligible.</i>'
                )
            bot.send_message(user_id, msg_text, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
            return
            
        elif rejected_expired:
            if is_bd:
                msg_text = (
                    f'<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <b>রিপ্লেসমেন্ট সময়সীমা উত্তীর্ণ!</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>জিমেইল:</b> <code>{target_email}</code>\n\n'
                    f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <i>এই জিমেইলটি ক্রয়ের ১ ঘণ্টার (1 hour) সময়সীমা পার হয়ে গেছে। ১ ঘণ্টার বেশি পুরোনো ক্রয়ের ক্ষেত্রে রিপ্লেসমেন্ট প্রযোজ্য নয়।</i>'
                )
            else:
                msg_text = (
                    f'<tg-emoji emoji-id="5212988801441344587">⚠️</tg-emoji> <b>Replacement Window Expired!</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>Gmail:</b> <code>{target_email}</code>\n\n'
                    f'<tg-emoji emoji-id="5215642288071387368">❌</tg-emoji> <i>The 1-hour replacement window for this Gmail has expired. Replacements are only valid within 1 hour of purchase.</i>'
                )
            bot.send_message(user_id, msg_text, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
            return
            
        elif rejected_already:
            if is_bd:
                msg_text = (
                    f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>পূর্বে প্রক্রিয়াধীন বা নিষ্পত্তি সম্পন্ন</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>জিমেইল:</b> <code>{target_email}</code>\n\n'
                    f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>এই অ্যাকাউন্টটির বিপরীতে ইতিমধ্যে একটি রিকোয়েস্ট জমা আছে বা পূর্বে রিফান্ড দেওয়া হয়েছে।</i>'
                )
            else:
                msg_text = (
                    f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> <b>Already Pending or Refunded</b>\n'
                    f'━━━━━━━━━━━━━━━━━━━━━\n'
                    f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>Gmail:</b> <code>{target_email}</code>\n\n'
                    f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> <i>A request is already pending or has already been resolved for this account.</i>'
                )
            bot.send_message(user_id, msg_text, parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))
            return

    else:
        # Case B: Batch of accounts requested
        summary_lines = []
        if is_bd:
            summary_lines.append('<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji> <b>রং পাসওয়ার্ড রিপ্লেসমেন্ট সাবমিট রিপোর্ট</b>')
            summary_lines.append('━━━━━━━━━━━━━━━━━━━━━')
            summary_lines.append(f'<tg-emoji emoji-id="5348125953090403204">📊</tg-emoji> মোট যাচাইকৃত: <b>{len(items_to_process)}</b> টি')
            if pending_list:
                total_claim = sum(amt for _, amt in pending_list)
                summary_lines.append(f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>রিভিউতে জমা হয়েছে:</b> <b>{len(pending_list)}</b> টি (দাবি: {fmt_bal(total_claim)} ৳)')
            if rejected_expired:
                summary_lines.append(f'<tg-emoji emoji-id="5213349767672769194">⏰</tg-emoji> সময়সীমা উত্তীর্ণ (১ ঘণ্টার বেশি): <b>{len(rejected_expired)}</b> টি')
            if rejected_not_found:
                summary_lines.append(f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> কেনা হয়নি (অননুমোদিত): <b>{len(rejected_not_found)}</b> টি')
            if rejected_already:
                summary_lines.append(f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> পূর্বে প্রক্রিয়াধীন/নিষ্পত্তিকৃত: <b>{len(rejected_already)}</b> টি')
            summary_lines.append('━━━━━━━━━━━━━━━━━━━━━')
            if pending_list:
                summary_lines.append('<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <i>আপনার জমাকৃত জিমেইলগুলো রিভিউতে রয়েছে। কিছুক্ষণের মধ্যে রিভিউ করে টাকা দেওয়া হবে।</i>')
        else:
            summary_lines.append('<tg-emoji emoji-id="6118546560897781055">📧</tg-emoji> <b>Wrong Password Replacement Report</b>')
            summary_lines.append('━━━━━━━━━━━━━━━━━━━━━')
            summary_lines.append(f'<tg-emoji emoji-id="5348125953090403204">📊</tg-emoji> Total Processed: <b>{len(items_to_process)}</b>')
            if pending_list:
                total_claim = sum(amt for _, amt in pending_list)
                summary_lines.append(f'<tg-emoji emoji-id="5215327832040811010">⏳</tg-emoji> <b>Submitted for Review:</b> <b>{len(pending_list)}</b> (Claim: {fmt_bal(total_claim)} ৳)')
            if rejected_expired:
                summary_lines.append(f'<tg-emoji emoji-id="5213349767672769194">⏰</tg-emoji> Expired (>1 hour): <b>{len(rejected_expired)}</b>')
            if rejected_not_found:
                summary_lines.append(f'<tg-emoji emoji-id="5240241223632954241">🚫</tg-emoji> Not Purchased: <b>{len(rejected_not_found)}</b>')
            if rejected_already:
                summary_lines.append(f'<tg-emoji emoji-id="5213134259098761044">⚠️</tg-emoji> Already Pending/Resolved: <b>{len(rejected_already)}</b>')
            summary_lines.append('━━━━━━━━━━━━━━━━━━━━━')
            if pending_list:
                summary_lines.append('<tg-emoji emoji-id="4967656361073574498">📝</tg-emoji> <i>Your claims are under review. Refunds will be credited shortly after review.</i>')

        bot.send_message(user_id, "\n".join(summary_lines), parse_mode="HTML", reply_markup=get_main_menu(lang, user_id))

    # 7. Notify Admin Channel & Admins
    if pending_list:
        u_identifier = f"@{uname}" if uname else f"User `{user_id}`"
        em_preview = ", ".join([f"<code>{em}</code>" for em, _ in pending_list[:3]])
        if len(pending_list) > 3:
            em_preview += f" (+{len(pending_list)-3} more)"
        total_claim = sum(amt for _, amt in pending_list)
        admin_notice = (
            f'<tg-emoji emoji-id="5463289097336405244">🔔</tg-emoji> <b>নতুন রং পাসওয়ার্ড রিপ্লেসমেন্ট রিকোয়েস্ট ({len(pending_list)} টি)!</b>\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f'<tg-emoji emoji-id="4967667085606912536">👤</tg-emoji> <b>ক্রেতা:</b> {u_identifier} (<code>{user_id}</code>)\n'
            f'<tg-emoji emoji-id="4970246557065544891">📧</tg-emoji> <b>জিমেইল:</b> {em_preview}\n'
            f'<tg-emoji emoji-id="5373174941095050893">💰</tg-emoji> <b>মোট রিফান্ড দাবি:</b> <b>{fmt_bal(total_claim)} ৳</b>\n'
            f'<tg-emoji emoji-id="5213349767672769194">⏰</tg-emoji> <b>সময়:</b> {now_utc.strftime("%H:%M:%S UTC")}\n'
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f'<tg-emoji emoji-id="5215391376081954505">🔗</tg-emoji> <b>Admin Panel > Bad Gmail > রিপ্লেসমেন্ট রিকোয়েস্ট</b> থেকে Approve বা Reject করুন।'
        )
        try:
            bot.send_message(-1002978737951, admin_notice, parse_mode="HTML")
        except:
            pass
        for a_id in ADMIN_IDS:
            try:
                bot.send_message(a_id, admin_notice, parse_mode="HTML")
            except:
                pass


def run_bot():
    print("Clearing previous webhooks...")
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception:
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
