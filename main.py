import discord
import json
import os
import random
import yaml
import logging
import sys
from datetime import datetime, timedelta, timezone
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
    logging.info(f'Logged in as {client.user} (ID: {client.user.id})')
    
    utc_tz = timezone.utc
    jst_tz = timezone(timedelta(hours=9))
    now_utc = datetime.now(utc_tz)
    now_jst = datetime.now(jst_tz)
    format_str = "%Y-%m-%d %H:%M:%S"

    sys_log_id = config.get("system_log_channel_id")
    if sys_log_id:
        sys_channel = client.get_channel(sys_log_id)
        if sys_channel:
            embed = discord.Embed(
                title="🚀 Bot Online / システム起動",
                color=0x2ecc71,
                timestamp=now_utc
            )
            embed.add_field(name="ステータス", value="✅ 正常稼働中", inline=True)
            embed.add_field(name="監視チャンネル", value=f"{len(config.get('allowed_channels', []))} 箇所", inline=True)
            embed.add_field(name="JST (日本標準時)", value=f"`{now_jst.strftime(format_str)}`", inline=False)
            embed.add_field(name="UTC (協定世界時)", value=f"`{now_utc.strftime(format_str)}`", inline=False)
            embed.set_footer(text=f"{client.user.name} System Manager")
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
    admin_id = config.get("admin_user_id")

    # --- 追加: !help コマンド ---
    if content == "!help":
        embed = discord.Embed(title="📜 コマンドヘルプ", color=0x34495e)
        embed.add_field(name="!status", value="過去9日間の統計と直近ログを表示", inline=False)
        embed.add_field(name="!reload", value="設定と応答リストを再読み込み", inline=False)
        embed.add_field(name="!logreset", value="活動ログファイルをリセット", inline=False)
        embed.add_field(name="!restart", value="ボットを再起動（管理者のみ）", inline=False)
        await message.channel.send(embed=embed)
        return

    # --- 追加: !logreset コマンド ---
    if content == "!logreset":
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"{datetime.now()} [INFO] Log file was reset by {message.author}\n")
            await message.channel.send("🧹 **Log Reset:** 活動ログファイルを初期化しました。")
            logging.info(f"Log reset by {message.author}")
        except Exception as e:
            await message.channel.send(f"❌ リセット失敗: {e}")
        return

    # --- 追加: !restart コマンド (管理者限定) ---
    if content == "!restart":
        if admin_id and message.author.id == admin_id:
            await message.channel.send("🔄 **Restarting...** ボットを再起動します。")
            logging.info(f"Manual restart triggered by {message.author}")
            os.execv(sys.executable, ['python3'] + sys.argv)
        else:
            await message.channel.send("⚠️ **Access Denied:** 管理者権限が必要です。")
        return

    # --- 既存: !status コマンド ---
    if content == "!status":
        try:
            now_dt = datetime.now()
            target_days = [(now_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(9)]
            ok_count, err_count = 0, 0
            recent_logs = []
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in lines:
                        if line[:10] in target_days:
                            if "[INFO]" in line: ok_count += 1
                            elif "[ERROR]" in line or "[CRITICAL]" in line: err_count += 1
                    recent_logs = [line.strip() for line in lines[-15:]]
            
            log_text = "\n".join(recent_logs) if recent_logs else "ログはありません。"
            embed = discord.Embed(title="📊 Bot 9日間統計レポート", color=0x9b59b6, timestamp=now_dt)
            embed.add_field(name="期間", value=f"{target_days[-1]} ～ {target_days[0]}", inline=False)
            embed.add_field(name="✅ OK / ❌ ERR", value=f"{ok_count} / {err_count}", inline=True)
            embed.add_field(name="📝 直近ログ", value=f"```text\n{log_text[:1000]}\n```", inline=False)
            await message.channel.send(embed=embed)
        except Exception as e:
            await message.channel.send(f"Status取得エラー: {e}")
        return

    # --- 既存: !reload コマンド ---
    if content == "!reload":
        try:
            config = load_config()
            load_responses()
            await message.channel.send("🔄 **System Reloaded:** 設定を最新に更新しました。")
            logging.info(f"Reload by {message.author}")
        except Exception as e:
            await message.channel.send(f"❌ Error: {e}")
        return

    # --- 既存: 自動応答ロジック ---
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
                    log_embed.add_field(name="送信内容", value=final_response, inline=False)
                    await log_channel.send(embed=log_embed)
            break

if TOKEN:
    client.run(TOKEN)