import discord
import json
import os
import random
import yaml
import logging
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler # 追加

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

# --- 追加機能: Git同期処理 ---
async def sync_git_repository():
    """Gitリポジトリを確認し、差分があればプルして反映する"""
    try:
        logging.info("Checking for Git updates...")
        # 1. リモートの情報を更新
        subprocess.run(["git", "fetch"], check=True)
        
        # 2. 現在のブランチとリモートの差分を確認
        status = subprocess.run(
            ["git", "status", "-uno"], 
            capture_output=True, 
            text=True
        ).stdout

        if "Your branch is behind" in status or "can be fast-forwarded" in status:
            logging.info("Update found. Pulling changes from Git...")
            # 強制的にGit側の内容で上書き（サーバー側の未コミット変更は破棄されるので注意）
            subprocess.run(["git", "reset", "--hard", "origin/main"], check=True)
            subprocess.run(["git", "pull"], check=True)
            
            # ファイルが変わったので設定と応答を再読み込み
            load_config()
            load_responses()
            logging.info("Git sync completed and responses reloaded.")
        else:
            logging.info("No updates found. Server is up to date.")
            
    except Exception as e:
        logging.error(f"Git sync error: {e}")

# --- 既存の読み込み関数 ---
def load_config():
    global config
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
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
    
    # 定期実行スケジューラーの開始 (10分ごとにGitチェック)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(sync_git_repository, 'interval', minutes=60) #１時間更新
    scheduler.start()

    utc_tz = timezone.utc
    jst_tz = timezone(timedelta(hours=9))
    now_utc = datetime.now(utc_tz)
    now_jst = datetime.now(jst_tz)
    format_str = "%Y-%m-%d %H:%M:%S"

    sys_log_id = config.get("system_log_channel_id")
    if sys_log_id:
        sys_channel = client.get_channel(sys_log_id)
        if sys_channel:
            embed = discord.Embed(title="🚀 Bot Online", color=0x2ecc71, timestamp=now_utc)
            embed.add_field(name="ステータス", value="✅ 正常稼働中", inline=True)
            embed.add_field(name="Git同期", value="🔄 60分毎に自動チェック中", inline=True)
            embed.add_field(name="JST (日本標準時)", value=f"`{now_jst.strftime(format_str)}`", inline=False)
            await sys_channel.send(embed=embed)

@client.event
async def on_message(message):
    global config
    if message.author == client.user: return

    allowed_ids = config.get("allowed_channels", [])
    if message.channel.id not in allowed_ids: return

    content = message.content.strip()
    admin_id = config.get("admin_user_id")

    if content == "!help":
        embed = discord.Embed(title="📜 コマンドヘルプ", color=0x34495e)
        embed.add_field(name="!status", value="統計と直近ログを表示", inline=False)
        embed.add_field(name="!reload", value="設定とGit同期を手動実行", inline=False)
        embed.add_field(name="!logreset", value="ログファイルをリセット", inline=False)
        embed.add_field(name="!restart", value="ボットを再起動（管理者のみ）", inline=False)
        await message.channel.send(embed=embed)
        return

    if content == "!logreset":
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now()} [INFO] Log reset\n")
        await message.channel.send("🧹 ログをリセットしました。")
        return

    if content == "!restart":
        if admin_id and message.author.id == admin_id:
            await message.channel.send("🔄 再起動します...")
            os.execv(sys.executable, ['python3'] + sys.argv)
        else:
            await message.channel.send("⚠️ 権限がありません。")
        return

    if content == "!status":
        now_dt = datetime.now()
        target_days = [(now_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(9)]
        ok_count, err_count = 0, 0
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines:
                    if line[:10] in target_days:
                        if "[INFO]" in line: ok_count += 1
                        elif "[ERROR]" in line: err_count += 1
                recent_logs = [line.strip() for line in lines[-15:]]
        log_text = "\n".join(recent_logs) if recent_logs else "ログなし"
        embed = discord.Embed(title="📊 Bot 9日間統計", color=0x9b59b6, timestamp=now_dt)
        embed.add_field(name="✅ OK / ❌ ERR", value=f"{ok_count} / {err_count}")
        embed.add_field(name="📝 直近ログ", value=f"```text\n{log_text[:1000]}\n```", inline=False)
        await message.channel.send(embed=embed)
        return

    if content == "!reload":
        await sync_git_repository() # 手動でもGit同期を走らせる
        await message.channel.send("🔄 Git同期とリロードが完了しました。")
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
                    await log_channel.send(embed=log_embed)
            break

if TOKEN:
    client.run(TOKEN)