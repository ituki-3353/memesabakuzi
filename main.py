import discord
import json
import os
import random
import yaml
import logging
import sys
import subprocess
import re  # 正規表現用
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler # 追加

# --- 1. ログの設定 ---
LOG_FILE = "bot_activity.log"
INTRO_DATA_FILE = "user_intros.json" # 自己紹介データ保存用


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
user_intros = {}

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

def load_intro_data():
    global user_intros
    if os.path.exists(INTRO_DATA_FILE):
        try:
            with open(INTRO_DATA_FILE, 'r', encoding='utf-8') as f:
                user_intros = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load intro data: {e}")

def save_intro_data():
    try:
        with open(INTRO_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_intros, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Failed to save intro data: {e}")

def parse_intro(text):
    """
    テンプレートの崩れに強く対応した解析ロジック。
    【名前/name】： でも 名前： でも抽出可能。
    """
    data = {}
    # 正規表現のポイント:
    # (?:【)? -> 「【」があってもなくても良い
    # 項目名    -> 「名前」「呼び方」など
    # (?:.*?】)? -> 「/name】」などの補足があってもなくても良い
    # [:：\s]* -> コロン（半角・全角）や空白が続いても良い
    # (.*)      -> その後の文字列をすべて取得
    patterns = {
        "name": r"(?:【)?名前(?:.*?】)?[:：\s]*(.*)",
        "call": r"(?:【)?呼び方(?:.*?】)?[:：\s]*(.*)",
        "age": r"(?:【)?年齢(?:.*?】)?[:：\s]*(.*)",
        "like": r"(?:【)?趣味(?:.*?】)?[:：\s]*(.*)",
        "message": r"(?:【)?(?:ひとこと|一言)(?:.*?】)?[:：\s]*(.*)"
    }
    
    for key, pattern in patterns.items():
        # re.IGNORECASE で英字の大小を無視
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            # 前後の空白を消して格納
            val = match.group(1).strip()
            data[key] = val if val else "未設定"
        else:
            data[key] = "未設定"
            
    return data

def get_shuffled_response(trigger):
    global shuffle_pools
    if not shuffle_pools[trigger]:
        shuffle_pools[trigger] = list(cached_responses[trigger])
        random.shuffle(shuffle_pools[trigger])
    return shuffle_pools[trigger].pop()

config = load_config()
load_responses()
load_intro_data()

# --- 3. イベントハンドラ ---

@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user} (ID: {client.user.id})')
    
    # スケジューラー開始
    scheduler = AsyncIOScheduler()
    scheduler.add_job(sync_git_repository, 'interval', minutes=10)
    scheduler.start()

    # --- 既存の自己紹介をインポートする処理 ---
    intro_channel_id = config.get("intro_channel_id")
    count = 0
    if intro_channel_id:
        intro_channel = client.get_channel(intro_channel_id)
        if intro_channel:
            logging.info("Scanning existing introductions...")
            # 過去のメッセージを200件（必要に応じて増減）取得
            async for msg in intro_channel.history(limit=200):
                if msg.author == client.user: continue
                if "名前" in msg.content:
                    intro_data = parse_intro(msg.content)
                    if intro_data["name"] != "未設定":
                        # 既存データと重複しても最新のもので更新
                        user_intros[msg.author.display_name] = intro_data
                        user_intros[msg.author.name] = intro_data
                        user_intros[intro_data["name"]] = intro_data
                        count += 1
            save_intro_data()
            logging.info(f"Imported {count} introductions from history.")

    # 起動通知の送信
    utc_tz = timezone.utc
    jst_tz = timezone(timedelta(hours=9))
    now_utc = datetime.now(utc_tz)
    now_jst = datetime.now(jst_tz)

    sys_log_id = config.get("system_log_channel_id")
    if sys_log_id:
        sys_channel = client.get_channel(sys_log_id)
        if sys_channel:
            embed = discord.Embed(title="再起動しました！", color=0x2ecc71, timestamp=now_utc)
            embed.add_field(name="ステータス", value="✅ 正常稼働中", inline=True)
            embed.add_field(name="過去ログ同期", value=f"✅ {count}件インポート済み", inline=True)
            embed.add_field(name="JST (日本標準時)", value=f"`{now_jst.strftime('%Y-%m-%d %H:%M:%S')}`", inline=False)
            embed.add_field(name="", value="再起動が要求されたため、再起動しました。", inline=False)
            await sys_channel.send(embed=embed)

@client.event
async def on_message(message):
    global config, user_intros
    if message.author == client.user: return

    content = message.content.strip()

    # --- 自己紹介チャンネルの監視と自動保存 ---
    intro_channel_id = config.get("intro_channel_id")
    if intro_channel_id and message.channel.id == intro_channel_id:
        if "【名前" in content: # テンプレートが含まれているか簡易チェック
            intro_data = parse_intro(content)
            # ユーザー名とIDをキーにして保存（検索しやすくするため）
            user_intros[message.author.display_name] = intro_data
            user_intros[str(message.author.id)] = intro_data
            save_intro_data()
            logging.info(f"Intro saved for {message.author.display_name}")
            await message.add_reaction("✅") # 保存完了の合図

    # --- 許可されたチャンネルでのコマンド処理 ---
    allowed_ids = config.get("allowed_channels", [])
    if message.channel.id not in allowed_ids: return

    admin_id = config.get("admin_user_id")

    # !user-info [ユーザー名 or メンション]
    if content.startswith("!user-info"):
        target_name = content.replace("!user-info", "").strip()
        if not target_name:
            await message.channel.send("⚠️ 検索したいユーザー名(サーナー内の表示名)を入力してください。例: `!user-info やま`")
            return
        
        # メンションからIDを抽出
        match = re.match(r'<@!?(\d+)>', target_name)
        if match:
            user_id = match.group(1)
            info = user_intros.get(user_id)
        else:
            info = user_intros.get(target_name)

        if info:
            embed = discord.Embed(title=f"👤 {info.get('name', target_name)} さんの自己紹介", color=0x3498db)
            embed.add_field(name="呼び方", value=info.get("call", "未設定"), inline=True)
            embed.add_field(name="年齢", value=info.get("age", "未設定"), inline=True)
            embed.add_field(name="趣味・好きなこと", value=info.get("like", "未設定"), inline=False)
            embed.add_field(name="ひとこと", value=info.get("message", "未設定"), inline=False)
            await message.channel.send(embed=embed)
        else:
            await message.channel.send(f"🔍 `{target_name}` さんの自己紹介データは見つかりませんでした。")
        return

    if content == "!help":
        embed = discord.Embed(title="📜 コマンドヘルプ", color=0x34495e)
        embed.add_field(name="!user-info [名前 or @メンション]", value="自己紹介情報を検索", inline=False)
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
            await message.channel.send("🔄 adminユーザーによる再起動が要求されました。再起動します。しばらくお待ち下さい。\
                                       \n起動完了ログが出力されない場合はログを確認後、コードを修正してください。")
            os.execv(sys.executable, ['python3'] + sys.argv)
        else:
            await message.channel.send("⚠️ 権限がありません。restaetコマンドは、adminリストにあるユーザーのみ使用できます。" \
                                       "\nYou don't have permission to use this command. Only users in the admin list can use it.")
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
        await message.channel.send("🔄 Git同期とリロードが完了しました。 \nGit and reload has complete.")
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