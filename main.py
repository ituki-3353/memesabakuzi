import discord
import json
import os
import random
import yaml
import logging
from datetime import datetime
from collections import deque
from dotenv import load_dotenv

# --- 1. ログの設定 ---
LOG_FILE = "bot_activity.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

config = {}
cached_responses = {}
shuffle_pools = {}

def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load config.json: {e}")
        return {}

def load_responses():
    global cached_responses, shuffle_pools
    try:
        with open('responses.yml', 'r', encoding='utf-8') as f:
            cached_responses = yaml.safe_load(f)
            shuffle_pools = {trigger: [] for trigger in cached_responses.keys()}
        logging.info("Responses loaded.")
    except Exception as e:
        logging.error(f"Failed to load responses.yml: {e}")

def get_shuffled_response(trigger):
    global shuffle_pools
    if not shuffle_pools[trigger]:
        shuffle_pools[trigger] = list(cached_responses[trigger])
        random.shuffle(shuffle_pools[trigger])
    return shuffle_pools[trigger].pop()

config = load_config()
load_responses()

# --- 3. イベントハンドラ ---

@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user}')
    sys_log_id = config.get("system_log_channel_id")
    if sys_log_id:
        sys_channel = client.get_channel(sys_log_id)
        if sys_channel:
            embed = discord.Embed(title="🚀 Bot Online", color=0x2ecc71, timestamp=datetime.now())
            embed.add_field(name="Status", value="✅ 正常稼働中", inline=True)
            await sys_channel.send(embed=embed)

@client.event
async def on_message(message):
    global config
    if message.author == client.user:
        return

    allowed_ids = config.get("allowed_channels", [])
    if message.channel.id not in allowed_ids:
        return

    content = message.content.strip()

    # --- 管理コマンド: !status ---
    if content == "!status":
        try:
            # ログファイルの解析
            ok_count = 0
            err_count = 0
            recent_logs = []

            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    # 全行読んでカウント
                    lines = f.readlines()
                    for line in lines:
                        if "[INFO]" in line: ok_count += 1
                        if "[ERROR]" in line or "[CRITICAL]" in line: err_count += 1
                    
                    # 直近15行を取得
                    recent_logs = [line.strip() for line in lines[-15:]]
            
            log_text = "\n".join(recent_logs) if recent_logs else "ログはありません。"
            
            embed = discord.Embed(title="📊 Bot Status Report", color=0x9b59b6, timestamp=datetime.now())
            embed.add_field(name="稼働状況", value="🟢 Online", inline=True)
            embed.add_field(name="ログ統計", value=f"✅ OK: {ok_count} / ❌ ERR: {err_count}", inline=True)
            embed.add_field(name="直近15行のログ", value=f"```text\n{log_text[:1000]}\n```", inline=False)
            
            await message.channel.send(embed=embed)
            logging.info(f"Status command executed by {message.author}")
        except Exception as e:
            await message.channel.send(f"Status取得エラー: {e}")
        return

    # 管理コマンド: !reload
    if content == "!reload":
        try:
            config = load_config()
            load_responses()
            await message.channel.send("🔄 **System Reloaded**")
            logging.info(f"Reload by {message.author}")
        except Exception as e:
            await message.channel.send(f"❌ Error: {e}")
        return

    # 自動応答ロジック
    for trigger, responses in cached_responses.items():
        if trigger in content:
            raw_response = get_shuffled_response(trigger)
            final_response = raw_response.replace("[userName]", message.author.display_name)
            await message.channel.send(final_response)

            logging.info(f"Match: '{trigger}' by {message.author}")

            log_channel_id = config.get("log_channel_id")
            if log_channel_id:
                log_channel = client.get_channel(log_channel_id)
                if log_channel:
                    log_embed = discord.Embed(title="✨ 自動応答ログ", color=0x3498db)
                    log_embed.add_field(name="実行者", value=message.author.mention, inline=True)
                    log_embed.add_field(name="トリガー", value=f"`{trigger}`", inline=True)
                    await log_channel.send(embed=log_embed)
            break

if TOKEN:
    client.run(TOKEN)