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
from datetime import datetime, timezone, timedelta

# MongoDB Connection
password = urllib.parse.quote_plus("aass1122@")
MONGO_URI = f"mongodb+srv://kamrolhasandeveloper:{password}@cluster0.gxc2lwl.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI, maxPoolSize=100, serverSelectionTimeoutMS=5000) 
db = client['kamrol_bot_db']
users_collection = db['users']
config_collection = db['config']
transactions_collection = db['transactions']
vpn_collection = db['vpn_stock']

# Test Main MongoDB Connection
try:
    print("Testing Main MongoDB connection...")
    client.admin.command('ping')
    print("✅ Main MongoDB Connection Successful!")
except Exception as e:
    print(f"❌ Main MongoDB Connection Failed!\nError: {e}")

# --- CACHING SYSTEM ---
LANG_CACHE = {}

def get_user_lang(chat_id):
    if chat_id in LANG_CACHE:
        return LANG_CACHE[chat_id]
        
    user = users_collection.find_one({"chat_id": chat_id})
    if user and "language" in user:
        LANG_CACHE[chat_id] = user["language"]
        return user["language"]
        
    LANG_CACHE[chat_id] = "bn"
    return "bn"

CONFIG_CACHE = {}
CONFIG_LAST_UPDATE = 0

def get_config():
    global CONFIG_CACHE, CONFIG_LAST_UPDATE
    if time.time() - CONFIG_LAST_UPDATE < 10 and CONFIG_CACHE:
        return CONFIG_CACHE
        
    conf = config_collection.find_one({"_id": "payment_settings"})
    if not conf:
        conf = {"_id": "payment_settings", "bkash": "Not set", "binance": "Not set", "min_deposit": 5.0, "vpn_price": 15.0, "hotmail_price": 5.0, "outlook_price": 5.0, "outlook_fr_price": 5.0, "working_bin": ""}
        config_collection.insert_one(conf)
        
    CONFIG_CACHE = conf
    CONFIG_LAST_UPDATE = time.time()
    return conf

# বট ইনিশিয়ালাইজেশন
TOKEN = '8523237591:AAFjAsYJbAj3oY0dxdAWFGPoOEE2OyEeLjA'
bot = telebot.TeleBot(TOKEN, threaded=True, num_threads=100) 

telebot.logger.setLevel(logging.INFO)

ADMIN_IDS = [6412225513, 8596783717]

# Translation Dictionary
texts = {
    'en': {
        'buy_btn': '🛒 Buy',
        'deposit_btn': '💳 Deposit',
        'balance_btn': '💰 Balance',
        'price_btn': '💵 Price',
        'lang_btn': '🌐 Language',
        'mail_btn': '📥 Mail Inbox',
        'welcome': "✨ Welcome {name}! 🤖\n🚀 Enjoy fast, secure service — use the menu below to get started!",
        'buy_text': "What would you like to buy?",
        'deposit_text': "Please select your preferred deposit method:",
        'balance_text': "Your Balance is: {balance} BDT",
        'price_text': "Here is the Price list.",
        'lang_text': "Please select your language below:",
        'mail_text': "Please send your credentials in the following format:\n`mail|pass|refresh_token|client_id`",
        'unknown': "Unknown command. Please use the menu below.",
        'lang_changed': "Language changed to English! 🇬🇧",
        
        'returning': "Returning...",
        'dep_24h_limit': "❌ You can only send one deposit request per 24 hours. Please try again later.",
        'dep_ask_amount': "How much do you want to deposit via {method}?",
        'dep_cancelled': "Deposit cancelled.",
        'dep_min_err': "❌ Minimum deposit is {min_dep}. Please enter a valid amount:",
        'dep_invalid_amt': "❌ Invalid amount. Please enter a number:",
        'dep_submit_btn': "📝 Submit TrxID",
        'dep_instruct': "🔹 **Deposit via {method}**\n\nAmount: **{amount}**\n\nPlease send exactly this amount to the following {method_str}:\n`{target_acc}`\n\nAfter sending, click the button below to submit your Transaction ID.",
        'dep_ask_trxid': "Please enter your Transaction ID (TrxID) below:",
        'dep_wrong_trxid': "❌ Please send the money first, then provide the correct TrxID here.",
        'dep_used_trxid': "❌ This TrxID has already been used. Please provide a valid TrxID.",
        'dep_success': "✅ Your deposit request has been sent to the Admin. Please wait for approval.",
        
        'buy_vpn_btn': "🛡️ Buy Vpn",
        'buy_out_of_stock': "❌ Currently out of stock.",
        'buy_ask_vpn': "🛒 Which VPN do you want?",
        'buy_ask_hotmail': "🛒 Store > Hotmail",
        'buy_ask_outlook': "🛒 Store > Outlook",
        'buy_ask_outlook_fr': "🛒 Store > Outlook.fr",
        'buy_no_bal': "❌ Insufficient balance!",
        'buy_yes': "✅ Yes",
        'buy_no': "❌ No",
        'buy_confirm': "Are you sure you want to buy **{cat}({dur})**?\nPrice: {price} ৳",
        'buy_cancelled': "❌ Purchase cancelled.",
        'buy_sold_out': "❌ Out of stock! Please try again later.",
        'buy_success': "✅ **Purchase Successful!**\n\n**Item:** {cat}({dur})\n**Price:** {price} ৳\n**New Balance:** {new_bal} ৳\n\n**Credentials:**\n{creds}",
        
        'mail_cancelled': "Mail Inbox check cancelled.",
        'mail_invalid_fmt': "❌ Invalid format. Please use:\n`mail|pass|refresh_token|client_id`",
        'mail_working': "Working on it... Main menu restored.",
        'mail_start': "🔄 Starting Mail Tracker for {email}...",
        'mail_token_err': "❌ Failed to generate Access Token. Please check your refresh_token and client_id.",
        'mail_new': "📧 **New Email Received!**\n\n**Subject:** {subject}\n**Preview:** {preview}\n",
        'mail_code': "\n**Code:**\n`{code}`",
        'mail_no_code': "\n*(No code detected)*",
        'mail_token_exp': "❌ Access token expired or invalid.",
        'mail_not_found': "❌ No mail found (payni).",
    },
    'bn': {
        'buy_btn': '🛒 কিনুন',
        'deposit_btn': '💳 ডিপোজিট',
        'balance_btn': '💰 ব্যালেন্স',
        'price_btn': '💵 মূল্য',
        'lang_btn': '🌐 ভাষা',
        'mail_btn': '📥 মেইল ইনবক্স',
        'welcome': "✨ স্বাগতম {name}! 🤖\n🚀 দ্রুত ও নিরাপদ সার্ভিস উপভোগ করুন — শুরু করতে নিচের মেনুটি ব্যবহার করুন!",
        'buy_text': "আপনি কি কিনতে চান?",
        'deposit_text': "আপনার পছন্দের ডিপোজিট মাধ্যম নির্বাচন করুন:",
        'balance_text': "আপনার ব্যালেন্স: {balance} BDT",
        'price_text': "এটি Price লিস্ট।",
        'lang_text': "অনুগ্রহ করে নিচে থেকে আপনার ভাষা নির্বাচন করুন:",
        'mail_text': "অনুগ্রহ করে আপনার ক্রেডেনশিয়াল নিচের ফরম্যাটে দিন:\n`mail|pass|refresh_token|client_id`",
        'unknown': "অজানা কমান্ড। অনুগ্রহ করে নিচের মেনুটি ব্যবহার করুন।",
        'lang_changed': "আপনার ভাষা বাংলায় পরিবর্তন করা হয়েছে! 🇧🇩",
        
        'returning': "ফিরে যাচ্ছি...",
        'dep_24h_limit': "❌ আপনি ২৪ ঘণ্টার মধ্যে মাত্র একবার ডিপোজিট রিকোয়েস্ট পাঠাতে পারবেন। দয়া করে পরে আবার চেষ্টা করুন।",
        'dep_ask_amount': "আপনি {method} এর মাধ্যমে কত টাকা ডিপোজিট করতে চান?",
        'dep_cancelled': "ডিপোজিট বাতিল করা হয়েছে।",
        'dep_min_err': "❌ সর্বনিম্ন ডিপোজিট হলো {min_dep} ৳। দয়া করে সঠিক এমাউন্ট দিন:",
        'dep_invalid_amt': "❌ ভুল এমাউন্ট। দয়া করে সংখ্যা দিন:",
        'dep_submit_btn': "📝 TrxID সাবমিট করুন",
        'dep_instruct': "🔹 **ডিপোজিট মাধ্যম: {method}**\n\nপরিমাণ: **{amount}**\n\nদয়া করে নিচের {method_str} এ ঠিক এই পরিমাণ টাকা পাঠান:\n`{target_acc}`\n\nটাকা পাঠানোর পর নিচের বাটনে ক্লিক করে আপনার Transaction ID দিন।",
        'dep_ask_trxid': "দয়া করে আপনার Transaction ID (TrxID) নিচে দিন:",
        'dep_wrong_trxid': "❌ দয়া করে আগে টাকা পাঠান, তারপর এখানে সঠিক TrxID দিন।",
        'dep_used_trxid': "❌ এই TrxID টি ইতিমধ্যে ব্যবহার করা হয়েছে। দয়া করে সঠিক TrxID দিন।",
        'dep_success': "✅ আপনার ডিপোজিট রিকোয়েস্ট অ্যাডমিনের কাছে পাঠানো হয়েছে। অনুগ্রহ করে অপেক্ষা করুন।",
        
        'buy_vpn_btn': "🛡️ VPN কিনুন",
        'buy_out_of_stock': "❌ দুঃখিত, বর্তমানে স্টক নেই।",
        'buy_ask_vpn': "🛒 আপনি কোন VPN চান?",
        'buy_ask_hotmail': "🛒 স্টোর > Hotmail",
        'buy_ask_outlook': "🛒 স্টোর > Outlook",
        'buy_ask_outlook_fr': "🛒 স্টোর > Outlook.fr",
        'buy_no_bal': "❌ আপনার পর্যাপ্ত ব্যালেন্স নেই!",
        'buy_yes': "✅ হ্যাঁ",
        'buy_no': "❌ না",
        'buy_confirm': "আপনি কি নিশ্চিত যে আপনি **{cat}({dur})** কিনতে চান?\nমূল্য: {price} ৳",
        'buy_cancelled': "❌ কেনা বাতিল করা হয়েছে।",
        'buy_sold_out': "❌ স্টক শেষ! দয়া করে পরে আবার চেষ্টা করুন।",
        'buy_success': "✅ **সফলভাবে কেনা হয়েছে!**\n\n**আইটেম:** {cat}({dur})\n**মূল্য:** {price} ৳\n**নতুন ব্যালেন্স:** {new_bal} ৳\n\n**একাউন্ট ডিটেইলস:**\n{creds}",
        
        'mail_cancelled': "মেইল ইনবক্স চেক বাতিল করা হয়েছে।",
        'mail_invalid_fmt': "❌ ভুল ফরম্যাট। দয়া করে ব্যবহার করুন:\n`mail|pass|refresh_token|client_id`",
        'mail_working': "কাজ চলছে... মেইন মেনু ফিরে এসেছে।",
        'mail_start': "🔄 {email} এর জন্য মেইল ট্র্যাকার শুরু হচ্ছে...",
        'mail_token_err': "❌ Access Token তৈরি করতে ব্যর্থ। দয়া করে আপনার refresh_token এবং client_id চেক করুন।",
        'mail_new': "📧 **নতুন মেইল এসেছে!**\n\n**সাবজেক্ট:** {subject}\n**প্রিভিউ:** {preview}\n",
        'mail_code': "\n**কোড:**\n`{code}`",
        'mail_no_code': "\n*(কোনো কোড পাওয়া যায়নি)*",
        'mail_token_exp': "❌ Access token এর মেয়াদ শেষ বা ভুল।",
        'mail_not_found': "❌ কোনো মেইল পাওয়া যায়নি (payni)।",
    }
}

def get_main_menu(lang, chat_id=None):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    t = texts[lang]
    markup.add(
        KeyboardButton(t['buy_btn']), KeyboardButton(t['deposit_btn']),
        KeyboardButton(t['balance_btn']), KeyboardButton(t['price_btn']),
        KeyboardButton(t['lang_btn']), KeyboardButton(t['mail_btn'])
    )
    if chat_id in ADMIN_IDS:
        markup.add(KeyboardButton('⚙️ Admin Panel'))
    return markup

def get_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton('📊 Bot Stats'), KeyboardButton('📢 Broadcast'),
        KeyboardButton('💰 Manage Balance'), KeyboardButton('⚙️ Payment Settings'),
        KeyboardButton('📦 Manage Store'), KeyboardButton('💳 Add Working BIN')
    )
    markup.add(KeyboardButton('🏠 Back to User Menu'))
    return markup

def get_payment_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton('📱 Set bKash Number'), KeyboardButton('🏦 Set Binance ID'),
        KeyboardButton('💵 Set Min Deposit'), KeyboardButton('💵 VPN Price')
    )
    markup.add(
        KeyboardButton('💵 Hotmail Price'), KeyboardButton('💵 Outlook Price')
    )
    markup.add(
        KeyboardButton('💵 Outlook.fr Price'), KeyboardButton('🔙 Back to Admin')
    )
    return markup

def get_cancel_menu(lang):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    cancel_text = '❌ Cancel' if lang == 'en' else '❌ বাতিল (Cancel)'
    markup.add(KeyboardButton(cancel_text))
    return markup

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
            "language": "bn" 
        })
        LANG_CACHE[user_id] = "bn"
        print(f"New user saved: {first_name} ({user_id})")
        
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(KeyboardButton('English 🇬🇧'), KeyboardButton('বাংলা 🇧🇩'))
        bot.send_message(
            message.chat.id, 
            "Please select your language 👇\nঅনুগ্রহ করে আপনার ভাষা নির্বাচন করুন 👇", 
            reply_markup=markup
        )
    else:
        lang = user.get("language", "bn")
        LANG_CACHE[user_id] = lang
        welcome_text = texts[lang]['welcome'].format(name=first_name)
        bot.send_message(message.chat.id, welcome_text, reply_markup=get_main_menu(lang, message.chat.id))

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.chat.id in ADMIN_IDS:
        bot.send_message(message.chat.id, "Welcome to the Admin Panel! ⚙️\nPlease choose an option:", reply_markup=get_admin_menu())
    else:
        lang = get_user_lang(message.chat.id)
        bot.send_message(message.chat.id, texts[lang]['unknown'])

@bot.message_handler(func=lambda message: True)
def handle_menu(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    text = message.text

    # --- ADMIN PANEL HANDLERS ---
    if user_id in ADMIN_IDS:
        if text == '📊 Bot Stats':
            total_users = users_collection.estimated_document_count() 
            bot.send_message(user_id, f"📊 **Bot Statistics**\n\nTotal Users: {total_users}")
            return
            
        elif text == '📢 Broadcast':
            msg = bot.send_message(user_id, "Please send the message you want to broadcast to all users:")
            bot.register_next_step_handler(msg, process_broadcast)
            return
            
        elif text == '💰 Manage Balance':
            msg = bot.send_message(user_id, "Please send the **Chat ID** of the user you want to manage:")
            bot.register_next_step_handler(msg, process_manage_balance_id)
            return
            
        elif text == '⚙️ Payment Settings':
            bot.send_message(user_id, "⚙️ **Payment Settings**\nSelect an option to configure:", reply_markup=get_payment_admin_menu())
            return
            
        elif text == '🔙 Back to Admin':
            bot.send_message(user_id, "Returning to Admin Panel...", reply_markup=get_admin_menu())
            return
            
        elif text == '💳 Add Working BIN':
            msg = bot.send_message(user_id, "Please enter the new Working BIN (e.g. 51546200200):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Admin'))
            bot.register_next_step_handler(msg, process_add_working_bin)
            return
            
        elif text == '📱 Set bKash Number':
            msg = bot.send_message(user_id, "Please enter the new bKash Number:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Admin'))
            bot.register_next_step_handler(msg, process_set_bkash)
            return
            
        elif text == '🏦 Set Binance ID':
            msg = bot.send_message(user_id, "Please enter the new Binance ID:", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Admin'))
            bot.register_next_step_handler(msg, process_set_binance)
            return
            
        elif text == '💵 Set Min Deposit':
            msg = bot.send_message(user_id, "Please enter the new Minimum Deposit amount (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Admin'))
            bot.register_next_step_handler(msg, process_set_min_deposit)
            return
            
        elif text == '💵 VPN Price':
            msg = bot.send_message(user_id, "Please enter the new Nord VPN (7day) Price (e.g. 15.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Admin'))
            bot.register_next_step_handler(msg, process_set_vpn_price)
            return
            
        elif text == '💵 Hotmail Price':
            msg = bot.send_message(user_id, "Please enter the new Hotmail Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Admin'))
            bot.register_next_step_handler(msg, process_set_hotmail_price)
            return
            
        elif text == '💵 Outlook Price':
            msg = bot.send_message(user_id, "Please enter the new Outlook Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Admin'))
            bot.register_next_step_handler(msg, process_set_outlook_price)
            return
            
        elif text == '💵 Outlook.fr Price':
            msg = bot.send_message(user_id, "Please enter the new Outlook.fr Price (e.g. 5.0):", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Admin'))
            bot.register_next_step_handler(msg, process_set_outlook_fr_price)
            return
            
        elif text == '📦 Manage Store':
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton('➕ Add VPN Stock'), KeyboardButton('➕ Add Hotmail Stock'))
            markup.add(KeyboardButton('➕ Add Outlook Stock'), KeyboardButton('➕ Add Outlook.fr Stock'))
            markup.add(KeyboardButton('👁️ View Stock'), KeyboardButton('🗑️ Clear Stock'))
            markup.add(KeyboardButton('🔙 Back to Admin'))
            bot.send_message(user_id, "📦 **Store Management**\nSelect an option:", reply_markup=markup)
            return
            
        elif text == '👁️ View Stock':
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
            
        elif text == '🗑️ Clear Stock':
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton('🗑️ Clear VPN'), KeyboardButton('🗑️ Clear Hotmail'))
            markup.add(KeyboardButton('🗑️ Clear Outlook'), KeyboardButton('🗑️ Clear Outlook.fr'))
            markup.add(KeyboardButton('🔙 Back to Store'))
            bot.send_message(user_id, "⚠️ **Which stock do you want to delete?**\n*(Only available unsold stock will be deleted!)*", reply_markup=markup, parse_mode="Markdown")
            return
            
        elif text == '🗑️ Clear VPN':
            res = vpn_collection.delete_many({"category": "Nord", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available VPN accounts.", parse_mode="Markdown")
            return
            
        elif text == '🗑️ Clear Hotmail':
            res = vpn_collection.delete_many({"category": "Hotmail", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available Hotmail accounts.", parse_mode="Markdown")
            return
            
        elif text == '🗑️ Clear Outlook':
            res = vpn_collection.delete_many({"category": "Outlook", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available Outlook accounts.", parse_mode="Markdown")
            return
            
        elif text == '🗑️ Clear Outlook.fr':
            res = vpn_collection.delete_many({"category": "Outlook.fr", "status": "available"})
            bot.send_message(user_id, f"✅ Successfully deleted **{res.deleted_count}** available Outlook.fr accounts.", parse_mode="Markdown")
            return
            
        elif text == '🔙 Back to Store':
            markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
            markup.add(KeyboardButton('➕ Add VPN Stock'), KeyboardButton('➕ Add Hotmail Stock'))
            markup.add(KeyboardButton('➕ Add Outlook Stock'), KeyboardButton('➕ Add Outlook.fr Stock'))
            markup.add(KeyboardButton('👁️ View Stock'), KeyboardButton('🗑️ Clear Stock'))
            markup.add(KeyboardButton('🔙 Back to Admin'))
            bot.send_message(user_id, "📦 **Store Management**\nSelect an option:", reply_markup=markup)
            return
            
        elif text == '➕ Add VPN Stock':
            msg_instruct = "Please paste the VPN accounts.\n\n**Format 1:** `mail|pass` or `mail pass` (one per line)\n**Format 2:**\n📧 email@domain.com\n🔒 password"
            msg = bot.send_message(user_id, msg_instruct, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_vpn_stock)
            return

        elif text == '➕ Add Hotmail Stock':
            msg_text = "Please send a `.txt` file or paste the Hotmail accounts (one per line).\n\nFormat: `mail|pass|token`\nExample:\n`user@hotmail.com|pass123|tokenABC`\n\n*(If you have Excel, just copy the rows and paste here!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_hotmail_stock)
            return
            
        elif text == '➕ Add Outlook Stock':
            msg_text = "Please send a `.txt` file or paste the Outlook accounts (one per line).\n\nFormat: `mail|pass|token`\nExample:\n`user@outlook.com|pass123|tokenABC`\n\n*(If you have Excel, just copy the rows and paste here!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_outlook_stock)
            return
            
        elif text == '➕ Add Outlook.fr Stock':
            msg_text = "Please send a `.txt` file or paste the Outlook.fr accounts (one per line).\n\nFormat: `mail|pass|token`\nExample:\n`user@outlook.fr|pass123|tokenABC`\n\n*(If you have Excel, just copy the rows and paste here!)*"
            msg = bot.send_message(user_id, msg_text, parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('🔙 Back to Store'))
            bot.register_next_step_handler(msg, process_add_outlook_fr_stock)
            return
            
        elif text == '🏠 Back to User Menu':
            bot.send_message(user_id, "Returning to User Menu...", reply_markup=get_main_menu(lang, user_id))
            return
            
        elif text == '⚙️ Admin Panel':
            bot.send_message(user_id, "Welcome to the Admin Panel! ⚙️\nPlease choose an option:", reply_markup=get_admin_menu())
            return

    # --- REGULAR USER HANDLERS ---
    if text == 'English 🇬🇧':
        users_collection.update_one({"chat_id": user_id}, {"$set": {"language": "en"}})
        LANG_CACHE[user_id] = "en"
        bot.send_message(user_id, texts['en']['lang_changed'], reply_markup=get_main_menu("en", user_id))
        return
    elif text == 'বাংলা 🇧🇩':
        users_collection.update_one({"chat_id": user_id}, {"$set": {"language": "bn"}})
        LANG_CACHE[user_id] = "bn"
        bot.send_message(user_id, texts['bn']['lang_changed'], reply_markup=get_main_menu("bn", user_id))
        return
    elif text in ['🔙 Back', '🔙 ব্যাক', '❌ Cancel', '❌ বাতিল (Cancel)']:
        bot.send_message(user_id, texts[lang]['returning'], reply_markup=get_main_menu(lang, user_id))
        return

    # Handle main menu commands
    elif text in [texts['en']['buy_btn'], texts['bn']['buy_btn']]:
        bot.send_message(user_id, "Opening Store...", reply_markup=get_cancel_menu(lang))
        
        # row_width=1 korar karone button gulo upor-niche thakbe
        inline_markup = InlineKeyboardMarkup(row_width=1)
        inline_markup.add(
            InlineKeyboardButton(texts[lang]['buy_vpn_btn'], callback_data="buy_cat_vpn"),
            InlineKeyboardButton("📧 Buy Hotmail", callback_data="buy_cat_hotmail"),
            InlineKeyboardButton("📧 Buy Outlook", callback_data="buy_cat_outlook"),
            InlineKeyboardButton("📧 Buy Outlook.fr", callback_data="buy_cat_outlookfr")
        )
        bot.send_message(user_id, texts[lang]['buy_text'], reply_markup=inline_markup)
        
    elif text in [texts['en']['deposit_btn'], texts['bn']['deposit_btn']]:
        bot.send_message(user_id, "Opening Deposit Panel...", reply_markup=get_cancel_menu(lang))
        
        inline_markup = InlineKeyboardMarkup()
        inline_markup.add(
            InlineKeyboardButton("🟣 bKash", callback_data="depmethod_bKash"),
            InlineKeyboardButton("🔶 Binance", callback_data="depmethod_Binance")
        )
        bot.send_message(user_id, texts[lang]['deposit_text'], reply_markup=inline_markup)
        
    elif text in [texts['en']['balance_btn'], texts['bn']['balance_btn']]:
        user = users_collection.find_one({"chat_id": user_id}, {"balance": 1})
        balance = user.get("balance", 0.0) if user else 0.0
        bot.send_message(user_id, texts[lang]['balance_text'].format(balance=balance))
        
    elif text in [texts['en']['price_btn'], texts['bn']['price_btn']]:
        conf = get_config()
        vpn_price = conf.get("vpn_price", 15.0)
        hm_price = conf.get("hotmail_price", 5.0)
        out_price = conf.get("outlook_price", 5.0)
        out_fr_price = conf.get("outlook_fr_price", 5.0)
        msg_text = f"**Price List / মূল্য তালিকা:**\n\n🔹 Nord VPN (7day) = **{vpn_price} ৳**\n📧 Hotmail = **{hm_price} ৳**\n📧 Outlook = **{out_price} ৳**\n📧 Outlook.fr = **{out_fr_price} ৳**"
        bot.send_message(user_id, msg_text, parse_mode="Markdown")
        
    elif text in [texts['en']['lang_btn'], texts['bn']['lang_btn']]:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(KeyboardButton('English 🇬🇧'), KeyboardButton('বাংলা 🇧🇩'))
        back_text = '🔙 Back' if lang == 'en' else '🔙 ব্যাক'
        markup.add(KeyboardButton(back_text))
        bot.send_message(user_id, texts[lang]['lang_text'], reply_markup=markup)
        
    elif text in [texts['en']['mail_btn'], texts['bn']['mail_btn']]:
        msg = bot.send_message(user_id, texts[lang]['mail_text'], parse_mode="Markdown", reply_markup=get_cancel_menu(lang))
        bot.register_next_step_handler(msg, process_mail_credentials)
        
    else:
        if text.isdigit():
            num = int(text)
            count = num if 1 <= num <= 9 else 1
            
            conf = get_config()
            working_bin = conf.get("working_bin", "")
            if not working_bin:
                bot.send_message(user_id, "❌ No Working BIN set by admin.")
                return
                
            reply_text = ""
            for _ in range(count):
                c_num = generate_test_card(working_bin)
                mm, yy = get_random_expiry()
                cvv = get_random_cvv()
                reply_text += f"Card: `{c_num}`\nTarikh: `{mm}`\nYear: `{yy}`\nCVV: `{cvv}`\n\n"
                
            bot.send_message(user_id, reply_text.strip(), parse_mode="Markdown")
            return
            
        bot.send_message(user_id, texts[lang]['unknown'])

# --- ADMIN CONFIG FUNCTIONS ---
def process_set_bkash(message):
    if message.text == '🔙 Back to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"bkash": message.text}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0 
    bot.send_message(message.chat.id, f"✅ bKash number updated to: {message.text}", reply_markup=get_payment_admin_menu())

def process_set_binance(message):
    if message.text == '🔙 Back to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"binance": message.text}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f"✅ Binance ID updated to: {message.text}", reply_markup=get_payment_admin_menu())

def process_set_min_deposit(message):
    if message.text == '🔙 Back to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"min_deposit": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Min deposit updated to: {val}", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid amount.", reply_markup=get_payment_admin_menu())

def process_add_working_bin(message):
    if message.text == '🔙 Back to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return
    
    bin_str = message.text.strip()
    if not bin_str.isdigit():
        bot.send_message(message.chat.id, "❌ BIN must contain numbers only.", reply_markup=get_admin_menu())
        return
        
    config_collection.update_one({"_id": "payment_settings"}, {"$set": {"working_bin": bin_str}}, upsert=True)
    global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
    bot.send_message(message.chat.id, f"✅ Working BIN updated to: {bin_str}", reply_markup=get_admin_menu())

def generate_test_card(bin_str):
    card = list(str(bin_str))
    while len(card) < 15:
        card.append(str(random.randint(0, 9)))
        
    total = 0
    for i, digit in enumerate(reversed(card)):
        n = int(digit)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        
    check_digit = (10 - (total % 10)) % 10
    card.append(str(check_digit))
    return "".join(card)

def get_random_expiry():
    month = random.randint(1, 12)
    year = datetime.now().year + random.randint(1, 6)
    return f"{month:02d}", str(year)

def get_random_cvv():
    return f"{random.randint(0, 999):03d}"

def process_set_vpn_price(message):
    if message.text == '🔙 Back to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"vpn_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ VPN Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_hotmail_price(message):
    if message.text == '🔙 Back to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"hotmail_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Hotmail Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_outlook_price(message):
    if message.text == '🔙 Back to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"outlook_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Outlook Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid amount.", reply_markup=get_payment_admin_menu())

def process_set_outlook_fr_price(message):
    if message.text == '🔙 Back to Admin':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_payment_admin_menu())
        return
    try:
        val = float(message.text)
        config_collection.update_one({"_id": "payment_settings"}, {"$set": {"outlook_fr_price": val}}, upsert=True)
        global CONFIG_LAST_UPDATE; CONFIG_LAST_UPDATE = 0
        bot.send_message(message.chat.id, f"✅ Outlook.fr Price updated to: {val} ৳", reply_markup=get_payment_admin_menu())
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid amount.", reply_markup=get_payment_admin_menu())

def process_add_vpn_stock(message):
    if message.text == '🔙 Back to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return
        
    raw_text = message.text.strip()
    added_count = 0
    docs_to_insert = []
    
    # Check if the user is using the new Emoji format (📧 and 🔒)
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
                current_email = None # Reset for the next entry
    else:
        # Fallback to the old standard format (mail|pass or mail pass)
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

@bot.message_handler(content_types=['document', 'text'])
def process_add_hotmail_stock(message):
    if message.text == '🔙 Back to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    raw_text = ""
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            raw_text = downloaded_file.decode('utf-8')
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error reading file: {e}", reply_markup=get_admin_menu())
            return
    elif message.text:
        raw_text = message.text

    lines = raw_text.strip().split('\n')
    added_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        parts = re.split(r'[|\t]+', line)
        if len(parts) >= 3:
            email = parts[0].strip()
            password = parts[1].strip()
            token = parts[2].strip()
            
            docs_to_insert.append({
                "category": "Hotmail",
                "duration": "Standard",
                "credentials": f"{email}|{password}|{token}",
                "status": "available",
                "timestamp": datetime.now(timezone.utc)
            })
            added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Hotmail accounts to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

@bot.message_handler(content_types=['document', 'text'])
def process_add_outlook_stock(message):
    if message.text == '🔙 Back to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    raw_text = ""
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            raw_text = downloaded_file.decode('utf-8')
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error reading file: {e}", reply_markup=get_admin_menu())
            return
    elif message.text:
        raw_text = message.text

    lines = raw_text.strip().split('\n')
    added_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        parts = re.split(r'[|\t]+', line)
        if len(parts) >= 3:
            email = parts[0].strip()
            password = parts[1].strip()
            token = parts[2].strip()
            
            docs_to_insert.append({
                "category": "Outlook",
                "duration": "Standard",
                "credentials": f"{email}|{password}|{token}",
                "status": "available",
                "timestamp": datetime.now(timezone.utc)
            })
            added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Outlook accounts to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

@bot.message_handler(content_types=['document', 'text'])
def process_add_outlook_fr_stock(message):
    if message.text == '🔙 Back to Store':
        bot.send_message(message.chat.id, "Cancelled.", reply_markup=get_admin_menu())
        return

    raw_text = ""
    if message.document:
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            raw_text = downloaded_file.decode('utf-8')
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error reading file: {e}", reply_markup=get_admin_menu())
            return
    elif message.text:
        raw_text = message.text

    lines = raw_text.strip().split('\n')
    added_count = 0
    docs_to_insert = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        parts = re.split(r'[|\t]+', line)
        if len(parts) >= 3:
            email = parts[0].strip()
            password = parts[1].strip()
            token = parts[2].strip()
            
            docs_to_insert.append({
                "category": "Outlook.fr",
                "duration": "Standard",
                "credentials": f"{email}|{password}|{token}",
                "status": "available",
                "timestamp": datetime.now(timezone.utc)
            })
            added_count += 1
            
    if docs_to_insert:
        vpn_collection.insert_many(docs_to_insert)
            
    bot.send_message(message.chat.id, f"✅ Successfully added **{added_count}** Outlook.fr accounts to stock!", parse_mode="Markdown", reply_markup=get_admin_menu())

# --- ADMIN MULTI-STEP FUNCTIONS ---
def process_broadcast(message):
    if message.text in ['📊 Bot Stats', '📢 Broadcast', '💰 Manage Balance', '🏠 Back to User Menu']:
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
    if message.text in ['📊 Bot Stats', '📢 Broadcast', '💰 Manage Balance', '🏠 Back to User Menu']:
        bot.send_message(message.chat.id, "Balance management cancelled.")
        handle_menu(message)
        return

    try:
        target_id = int(message.text)
        target_user = users_collection.find_one({"chat_id": target_id})
        if not target_user:
            bot.send_message(message.chat.id, "❌ User not found in database.", reply_markup=get_admin_menu())
            return
        
        current_balance = target_user.get("balance", 0.0)
        msg = bot.send_message(
            message.chat.id, 
            f"User found: {target_user.get('first_name', 'Unknown')} ({target_id})\nCurrent Balance: {current_balance}\n\nEnter the amount to ADD (use negative like -10 to deduct):"
        )
        bot.register_next_step_handler(msg, process_manage_balance_amount, target_id, current_balance)
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid Chat ID. Must be numbers only. Cancelled.", reply_markup=get_admin_menu())

def process_manage_balance_amount(message, target_id, current_balance):
    if message.text in ['📊 Bot Stats', '📢 Broadcast', '💰 Manage Balance', '🏠 Back to User Menu']:
        bot.send_message(message.chat.id, "Balance management cancelled.")
        handle_menu(message)
        return

    try:
        amount = float(message.text)
        new_balance = current_balance + amount
        
        users_collection.update_one({"chat_id": target_id}, {"$set": {"balance": new_balance}})
        bot.send_message(message.chat.id, f"✅ Successfully updated balance!\n\nNew Balance: {new_balance}", reply_markup=get_admin_menu())
        
        try:
            bot.send_message(target_id, f"💰 Your balance has been updated by the Admin.\nAmount changed: {'+' if amount > 0 else ''}{amount}\nNew Balance: {new_balance}")
        except:
            pass 
    except ValueError:
        bot.send_message(message.chat.id, "❌ Invalid amount. Must be a number. Cancelled.", reply_markup=get_admin_menu())

# --- DEPOSIT FLOW FUNCTIONS ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('depmethod_'))
def handle_deposit_method(call):
    bot.answer_callback_query(call.id)
    method = call.data.split('_')[1]
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        
    last_tx = transactions_collection.find_one(
        {"user_id": user_id},
        sort=[("timestamp", -1)]
    )
    if last_tx and "timestamp" in last_tx:
        last_time = last_tx["timestamp"]
        if last_time.tzinfo:
            last_time = last_time.replace(tzinfo=None)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        
        if now_utc - last_time < timedelta(hours=24):
            bot.send_message(user_id, texts[lang]['dep_24h_limit'], reply_markup=get_main_menu(lang, user_id))
            return
            
    msg = bot.send_message(user_id, texts[lang]['dep_ask_amount'].format(method=method), reply_markup=get_cancel_menu(lang))
    bot.register_next_step_handler(msg, process_deposit_amount, method)

def process_deposit_amount(message, method):
    user_id = message.chat.id
    text = message.text.strip()
    lang = get_user_lang(user_id)
    
    if text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 ব্যাক'] or text in texts['en'].values() or text in texts['bn'].values():
        bot.send_message(user_id, texts[lang]['dep_cancelled'], reply_markup=get_main_menu(lang, user_id))
        return
        
    try:
        amount = float(text)
        conf = get_config()
        min_dep = conf.get("min_deposit", 5.0)
        
        if amount < min_dep:
            msg = bot.send_message(user_id, texts[lang]['dep_min_err'].format(min_dep=min_dep), reply_markup=get_cancel_menu(lang))
            bot.register_next_step_handler(msg, process_deposit_amount, method)
            return
            
        if method == 'bKash':
            target_acc = conf.get("bkash", "Not set")
            method_str = "bKash Number"
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
    parts = call.data.split('_')
    method = parts[1]
    amount = parts[2]
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
    
    if method == 'bKash':
        ask_msg = "Please enter your **10-character** bKash Transaction ID (TrxID) below:" if lang == 'en' else "দয়া করে আপনার **১০ সংখ্যার** bKash Transaction ID (TrxID) নিচে দিন:"
    else:
        ask_msg = "Please enter your **Binance** Transaction ID (TrxID) below:" if lang == 'en' else "দয়া করে আপনার **Binance** Transaction ID (TrxID) নিচে দিন:"
        
    msg = bot.send_message(user_id, ask_msg, parse_mode="Markdown", reply_markup=get_cancel_menu(lang))
    bot.register_next_step_handler(msg, process_deposit_final_trxid, method, amount)

def process_deposit_final_trxid(message, method, amount):
    user_id = message.chat.id
    text = message.text.strip()
    lang = get_user_lang(user_id)
    
    if text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 ব্যাক'] or text in texts['en'].values() or text in texts['bn'].values():
        bot.send_message(user_id, texts[lang]['dep_cancelled'], reply_markup=get_main_menu(lang, user_id))
        return
        
    if method == 'bKash' and len(text) != 10:
        bot.send_message(user_id, texts[lang]['dep_wrong_trxid'], reply_markup=get_main_menu(lang, user_id))
        return
    elif method == 'Binance' and len(text) < 8:
        bot.send_message(user_id, texts[lang]['dep_wrong_trxid'], reply_markup=get_main_menu(lang, user_id))
        return
        
    existing = transactions_collection.find_one({"trx_id": text})
    if existing:
        bot.send_message(user_id, texts[lang]['dep_used_trxid'], reply_markup=get_main_menu(lang, user_id))
        return
        
    transactions_collection.insert_one({
        "trx_id": text,
        "user_id": user_id,
        "amount": float(amount),
        "method": method,
        "status": "pending",
        "timestamp": datetime.now(timezone.utc)
    })
    
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Approve", callback_data=f"gdep_app_{text}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"gdep_rej_{text}")
    )
    
    admin_msg = f"🔔 **New Deposit Request**\nUser ID: `{user_id}`\nMethod: **{method}**\nAmount: **{amount}**\nTrxID: `{text}`"
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
        bot.edit_message_text("❌ Transaction not found in DB.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        return
        
    if tx['status'] != 'pending':
        bot.edit_message_text(f"⚠️ This transaction was already {tx['status']}.", chat_id=call.message.chat.id, message_id=call.message.message_id)
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
        bot.edit_message_text(f"❌ **Rejected**\nUser: `{target_id}`\nAmount: {amount}\nTrxID: `{trx_id}`", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        
        try:
            bot.send_message(target_id, "❌ Your deposit request was rejected by the admin. Please verify your TrxID and try again.")
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
        
        btn_text = f"🛡️ {cat}({dur}) | 💵 {vpn_price} ৳ | 📦 stock-{stock}"
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

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_item_'))
def handle_buy_item(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    parts = call.data.split('_')
    cat = parts[2]
    dur = parts[3]
    
    conf = get_config()
    if cat == "Hotmail":
        price = conf.get("hotmail_price", 5.0)
    elif cat == "Outlook":
        price = conf.get("outlook_price", 5.0)
    elif cat == "Outlook.fr":
        price = conf.get("outlook_fr_price", 5.0)
    else:
        price = conf.get("vpn_price", 15.0)
    
    user = users_collection.find_one({"chat_id": user_id}, {"balance": 1})
    balance = user.get("balance", 0.0) if user else 0.0
    
    if balance < price:
        bot.answer_callback_query(call.id, texts[lang]['buy_no_bal'], show_alert=True)
        return
        
    bot.answer_callback_query(call.id)
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton(texts[lang]['buy_yes'], callback_data=f"confbuy_yes_{cat}_{dur}"),
        InlineKeyboardButton(texts[lang]['buy_no'], callback_data="confbuy_no")
    )
    bot.edit_message_text(texts[lang]['buy_confirm'].format(cat=cat, dur=dur, price=price), chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('confbuy_'))
def handle_confirm_buy(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    
    if call.data == 'confbuy_no':
        bot.answer_callback_query(call.id)
        bot.edit_message_text(texts[lang]['buy_cancelled'], chat_id=user_id, message_id=call.message.message_id)
        return
        
    parts = call.data.split('_')
    cat = parts[2]
    dur = parts[3]
    
    conf = get_config()
    if cat == "Hotmail":
        price = conf.get("hotmail_price", 5.0)
    elif cat == "Outlook":
        price = conf.get("outlook_price", 5.0)
    elif cat == "Outlook.fr":
        price = conf.get("outlook_fr_price", 5.0)
    else:
        price = conf.get("vpn_price", 15.0)
    
    user = users_collection.find_one({"chat_id": user_id})
    balance = user.get("balance", 0.0) if user else 0.0
    
    if balance < price:
        bot.answer_callback_query(call.id, texts[lang]['buy_no_bal'], show_alert=True)
        bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        return
        
    bot.answer_callback_query(call.id)
    item = vpn_collection.find_one_and_update(
        {"category": cat, "duration": dur, "status": "available"},
        {"$set": {"status": "sold", "buyer_id": user_id, "price_paid": price, "sold_at": datetime.now(timezone.utc)}}
    )
    
    if not item:
        bot.edit_message_text(texts[lang]['buy_sold_out'], chat_id=user_id, message_id=call.message.message_id)
        return
        
    new_balance = balance - price
    users_collection.update_one({"chat_id": user_id}, {"$set": {"balance": new_balance}})
    
    creds_raw = item.get("credentials", "")
    cred_parts = creds_raw.split('|')
    
    if len(cred_parts) == 3 and (cat == "Hotmail" or cat == "Outlook" or cat == "Outlook.fr"):
        creds_formatted = f"📧 **Mail:**\n`{cred_parts[0]}`\n🔑 **Password:**\n`{cred_parts[1]}`\n🛡️ **Token:**\n`{cred_parts[2]}`"
    elif len(cred_parts) == 2:
        creds_formatted = f"📧 **Mail:**\n`{cred_parts[0]}`\n🔑 **Password:**\n`{cred_parts[1]}`"
    else:
        creds_formatted = f"`{creds_raw}`"
        
    msg = texts[lang]['buy_success'].format(cat=cat, dur=dur, price=price, new_bal=new_balance, creds=creds_formatted)
    
    bot.edit_message_text(msg, chat_id=user_id, message_id=call.message.message_id, parse_mode="Markdown")
    
    username = user.get("username")
    first_name = user.get("first_name", "Unknown")
    user_identifier = f"@{username}" if username else first_name
    
    log_msg = f"🛒 **New Purchase**\n\nUser: {user_identifier} (`{user_id}`)\nItem: {cat}({dur})\nPrice Paid: {price} ৳\nRemaining Balance: {new_balance} ৳"
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
    
    if text in ['❌ Cancel', '❌ বাতিল (Cancel)', '🔙 Back', '🔙 ব্যাক'] or text in texts['en'].values() or text in texts['bn'].values():
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
