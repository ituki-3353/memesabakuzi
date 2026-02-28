import discord
import json
import os
import random
import yaml
import logging
import sys
import subprocess
import shutil
import re  # 正規表現用
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler # 追加
from discord import app_commands

config = {}
cached_responses = {}
shuffle_pools = {}
user_intros = {}

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
tree = app_commands.CommandTree(client)

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
        # safe.directory=* を追加して所有権エラーを回避
        subprocess.run(["git", "-c", "safe.directory=*", "fetch"], check=True)
        
        # 2. 現在のブランチとリモートの差分を確認
        status = subprocess.run(
            ["git", "-c", "safe.directory=*", "status", "-uno"], 
            capture_output=True, 
            text=True
        ).stdout

        if "Your branch is behind" in status or "can be fast-forwarded" in status:
            logging.info("Update found. Pulling changes from Git...")
            # 強制的にGit側の内容で上書き（サーバー側の未コミット変更は破棄されるので注意）
            subprocess.run(["git", "-c", "safe.directory=*", "reset", "--hard", "origin/main"], check=True)
            subprocess.run(["git", "-c", "safe.directory=*", "pull"], check=True)
            
            # ファイルが変わったので設定と応答を再読み込み
            load_config()
            load_responses()
            logging.info("Git sync completed and responses reloaded.")
        else:
            logging.info("No updates found. Server is up to date.")
            
    except Exception as e:
        logging.error(f"Git sync error: {e}")

async def collect_netatwi_section():
    target_channel_id = config.get("netatwi_channel_id")
    if not target_channel_id:
        return

    trigger_emoji_config = config.get("reaction_trigger", "🇳").strip()
    min_count = config.get("min_reaction_count", 1)
    
    channel = client.get_channel(target_channel_id)
    new_responses = []

    if channel:
        logging.info(f"Scanning channel {channel.name} for netatwi...")
        # 過去ログをスキャン
        async for msg in channel.history(limit=None):
            if msg.author.bot: continue
            # 特定のリアクションがついているかチェック
            for reaction in msg.reactions:
                emoji_str = str(reaction.emoji)
                # 絵文字が一致し、かつ指定数以上のリアクションがある場合
                if (emoji_str == trigger_emoji_config or 
                    emoji_str == "🇳" or 
                    "regional_indicator_n" in emoji_str):
                    
                    if reaction.count >= min_count:
                        if msg.content and msg.content not in new_responses:
                            new_responses.append(msg.content)
                        break 

        # responses.yml の特定のセクションを更新する処理
        if new_responses:
            try:
                with open('responses.yml', 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                
                if 'ネタツイ' not in data:
                    data['ネタツイ'] = []
                
                # 既存のネタツイと重複しないように追加
                existing_set = set(data['ネタツイ'])
                added_count = 0
                for resp in new_responses:
                    if resp not in existing_set:
                        data['ネタツイ'].append(resp)
                        added_count += 1
                
                if added_count > 0:
                    with open('responses.yml', 'w', encoding='utf-8') as f:
                        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                    
                    # メモリ上のキャッシュも更新
                    load_responses()
                    logging.info(f"Collected {added_count} new netatwi responses.")
                else:
                    logging.info("No new netatwi responses to add.")

            except Exception as e:
                logging.error(f"Failed to update responses.yml: {e}")

# --- 既存の読み込み関数 ---
def load_config():
    global config
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
    return config

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
admin_ids = config.get("admin_user_id", [])

# --- スラッシュコマンド定義 ---
@tree.command(name="test", description="テストテキストを出力します")
async def test(interaction: discord.Interaction):
    # スラッシュコマンドへの返信
    await interaction.response.send_message("テストテキスト")

@tree.command(name="reload", description="設定とGit同期、ネタツイ収集を実行（管理者のみ）")
async def reload_command(interaction: discord.Interaction):
    admin_ids = config.get("admin_user_id", [])
    if interaction.user.id not in admin_ids:
        await interaction.response.send_message("⚠️ 権限がありません。", ephemeral=True)
        return

    await interaction.response.defer()
    status_msg = await interaction.followup.send("🔄 全期間のネタツイを再収集しています...", wait=True)
    
    # 1. Git同期
    await sync_git_repository()
    
    # 2. ネタツイ収集設定
    netatwi_id = config.get("netatwi_channel_id")
    trigger_emoji = config.get("reaction_trigger", "🇳").strip()
    target_channel = client.get_channel(netatwi_id)

    collected_texts = []
    scanned_messages_count = 0
    if target_channel:
        # 全てのメッセージを取得
        async for msg in target_channel.history(limit=None):
            scanned_messages_count += 1
            if msg.author.bot: continue
            for reaction in msg.reactions:
                # 設定されたリアクション絵文字か判定 (柔軟な比較)
                r_str = str(reaction.emoji)
                if r_str == trigger_emoji or r_str == "🇳" or "regional_indicator_n" in r_str:
                    if msg.content and msg.content not in collected_texts:
                        collected_texts.append(msg.content)
                    break

    # 3. responses.yml への反映
    if collected_texts:
        try:
            # 既存のファイルを読み込む（他のセクションを消さないため）
            if os.path.exists('responses.yml'):
                with open('responses.yml', 'r', encoding='utf-8') as f:
                    res_data = yaml.safe_load(f) or {}
            else:
                res_data = {}

            # 「ネタツイ」セクションを更新（既存データを保持しつつ追加）
            if "ネタツイ" not in res_data:
                res_data["ネタツイ"] = []
            
            # 重複排除して追加
            existing_set = set(res_data["ネタツイ"])
            added_count = 0
            for text in collected_texts:
                if text not in existing_set:
                    res_data["ネタツイ"].append(text)
                    added_count += 1
            
            with open('responses.yml', 'w', encoding='utf-8') as f:
                yaml.dump(res_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            
            # メモリ上のキャッシュを更新
            load_responses()

            # 収集結果をファイルに書き出す
            report_filename = f"collected_netatwi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(report_filename, "w", encoding="utf-8") as rf:
                rf.write(f"--- ネタツイ収集結果 ({len(collected_texts)}件) ---\n\n")
                for i, text in enumerate(collected_texts, 1):
                    rf.write(f"[{i}]\n{text}\n\n---\n\n")
            
            await status_msg.edit(content=f"✅ 成功！\nスキャンメッセージ数 / 収集ネタ数: `{scanned_messages_count} / {len(collected_texts)}`\n新規追加: `{added_count}`件")
            await interaction.followup.send(file=discord.File(report_filename))
            
            os.remove(report_filename)
        except Exception as e:
            await status_msg.edit(content=f"❌ ファイル書き込みエラー: {e}")
    else:
        await status_msg.edit(content=f"⚠️ 指定した期間・リアクションに合致するメッセージが見つかりませんでした。\nスキャンメッセージ数: `{scanned_messages_count}`件")

@tree.command(name="restart", description="ボットを再起動（管理者のみ）")
async def restart_command(interaction: discord.Interaction):
    admin_ids = config.get("admin_user_id", [])
    if interaction.user.id not in admin_ids:
        await interaction.response.send_message("⚠️ 権限がありません。", ephemeral=True)
        return
    
    await interaction.response.send_message("🔄 再起動します...")
    os.execv(sys.executable, ['python3'] + sys.argv)

@tree.command(name="status", description="統計と直近ログを表示")
async def status_command(interaction: discord.Interaction):
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
                    elif "[ERROR]" in line: err_count += 1
            recent_logs = [line.strip() for line in lines[-15:]]
    log_text = "\n".join(recent_logs) if recent_logs else "ログなし"
    embed = discord.Embed(title="📊 Bot 9日間統計", color=0x9b59b6, timestamp=now_dt)
    embed.add_field(name="✅ OK / ❌ ERR", value=f"{ok_count} / {err_count}")
    embed.add_field(name="📝 直近ログ", value=f"```text\n{log_text[:1000]}\n```", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="admin-check", description="管理者権限を確認")
async def admin_check_command(interaction: discord.Interaction):
    admin_ids = config.get("admin_user_id", [])
    if interaction.user.id in admin_ids:
        embed = discord.Embed(title="✅ 登録されています。", color=0x2ecc71)
        embed.add_field(name="ID", value=interaction.user.id, inline=False)
        embed.add_field(name="", value="管理者リストに登録されています。\n管理者専用コマンドの使用が許可されています。", inline=False)
        embed.set_footer(text="bot管理者チェックツール")
    else:
        embed = discord.Embed(title="❌ 登録されていません。", color=0xe74c3c)
        embed.add_field(name="ID", value=interaction.user.id, inline=False)
        embed.add_field(name="", value="管理者リストに登録されていません。\n管理者専用コマンドの使用はできません。", inline=False)
        embed.set_footer(text="bot管理者チェックツール")
    await interaction.response.send_message(embed=embed)

@tree.command(name="monthly-report", description="月例レポートを表示")
async def monthly_report_command(interaction: discord.Interaction):
    await interaction.response.defer()
    # 既存のロジックを再利用するために、メッセージ送信部分だけ調整が必要ですが、
    # ここではロジックを再実装します（on_messageの実装とほぼ同じ）
    # ※長くなるため、on_message側の実装を関数化するのが理想ですが、
    # 今回はリクエストに従いコマンド内に展開します。
    await generate_monthly_report(interaction)

# --- 3. イベントハンドラ ---

@client.event
async def on_ready():
    logging.info(f'Logged in as {client.user} (ID: {client.user.id})')
    
    # スラッシュコマンド同期
    try:
        synced = await tree.sync()
        logging.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logging.error(f"Command sync error: {e}")
    
    # スケジューラー開始
    scheduler = AsyncIOScheduler()
    scheduler.add_job(sync_git_repository, 'interval', minutes=10)
    scheduler.add_job(collect_netatwi_section, 'interval', minutes=60)
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

async def generate_monthly_report(interaction: discord.Interaction):
    try:
        # JSTタイムゾーンを設定
        jst_tz = timezone(timedelta(hours=9))
        now_dt = datetime.now(jst_tz)
        # 過去30日間を対象
        days_30 = [(now_dt - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(30)]
        
        stats_daily = {day: {"OK": 0, "ERR": 0, "WARN": 0, "REQ": 0, "RES": 0} for day in days_30}
        info_count, err_count, warn_count = 0, 0, 0
        response_count = 0 
        trigger_stats = {} 

        # --- 1. ログファイルの解析 ---
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    log_date = line[:10]
                    if log_date in stats_daily:
                        # ログレベル集計
                        if "[INFO]" in line:
                            info_count += 1
                            stats_daily[log_date]["OK"] += 1
                            if "by " in line: # ユーザーからのリクエストとみなす
                                stats_daily[log_date]["REQ"] += 1
                        elif "[ERROR]" in line or "[CRITICAL]" in line:
                            err_count += 1
                            stats_daily[log_date]["ERR"] += 1
                        elif "[WARNING]" in line:
                            warn_count += 1
                            stats_daily[log_date]["WARN"] += 1
                        
                        # 応答解析
                        if "Match: '" in line:
                            response_count += 1
                            stats_daily[log_date]["RES"] += 1
                            try:
                                t_name = line.split("Match: '")[1].split("'")[0]
                                trigger_stats[t_name] = trigger_stats.get(t_name, 0) + 1
                            except: pass

        total_req = sum(d["REQ"] for d in stats_daily.values())

        # --- 2. 詳細レポートファイルの生成 ---
        report_filename = f"Detailed_Report_{now_dt.strftime('%Y%m%d_%H%M%S')}.txt"
        with open(report_filename, "w", encoding="utf-8") as rf:
            rf.write(f"=== DISCORD BOT DETAILED MONTHLY REPORT ({now_dt.strftime('%Y/%m')}) ===\n")
            rf.write(f"Generated at: {now_dt.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            rf.write("[SYSTEM INFO]\n")
            try:
                # 実行ファイルの絶対パスからディレクトリを取得
                current_dir = os.path.dirname(os.path.abspath(__file__))
                
                git_res = subprocess.run(
                    ["git", "-c", "safe.directory=*", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    cwd=current_dir, # ここを動的なパスに変更
                    check=True
                )
                git_ver = git_res.stdout.strip()
            except subprocess.CalledProcessError as e:
                # エラー内容をログに詳しく出す（デバッグ用）
                logging.error(f"Git subprocess error: {e.stderr}")
                git_ver = "Git-Error"
            except Exception as e:
                logging.error(f"Git general error: {e}")
                git_ver = "No-Git-Repo"
            rf.write(f"Python Version: {sys.version}\n")
            rf.write(f"Git Hash: {git_ver}\n")

            # システムリソース情報の追加
            try:
                total, used, free = shutil.disk_usage(".")
                rf.write(f"Disk Usage: Total {total // (2**30)}GB / Used {used // (2**30)}GB / Free {free // (2**30)}GB\n")
            except Exception:
                rf.write("Disk Usage: N/A\n")

            try:
                mem_info = subprocess.run(["free", "-h"], capture_output=True, text=True).stdout
                if mem_info:
                    rf.write(f"Memory Info:\n{mem_info.strip()}\n")
            except Exception:
                pass

            rf.write(f"Total Response Variations: {sum(len(v) for v in cached_responses.values())}\n")
            rf.write(f"Total User Intros: {len(user_intros)}\n\n")

            rf.write("[ALL TRIGGER STATISTICS]\n")
            sorted_all_triggers = sorted(trigger_stats.items(), key=lambda x: x[1], reverse=True)
            for k, v in sorted_all_triggers:
                rf.write(f"- {k}: {v} times\n")
            
            rf.write("\n[DAILY TRANSITION]\n")
            rf.write("Date       | REQ | RES | INFO | WARN | ERR \n")
            rf.write("-" * 45 + "\n")
            for day in reversed(days_30):
                d = stats_daily[day]
                rf.write(f"{day} | {d['REQ']:<3} | {d['RES']:<3} | {d['OK']:<4} | {d['WARN']:<4} | {d['ERR']:<3}\n")

        # --- 3. Discord用Embed（要約）の作成 ---
        sorted_top5 = sorted_all_triggers[:5]
        trigger_text = "\n".join([f"• {k}: {v}回" for k, v in sorted_top5]) if sorted_top5 else "データなし"

        embed = discord.Embed(
            title=f"📊 {now_dt.strftime('%Y年%m月')}度 月例要約レポート",
            color=0x3498db,
            timestamp=now_dt
        )
        embed.add_field(name="🚨 ログ統計", value=f"✅ INFO: {info_count}\n⚠️ WARN: {warn_count}\n❌ ERR: {err_count}", inline=True)
        embed.add_field(name="📩 通信統計", value=f"📥 受信Req: {total_req}\n📤 総応答数: {response_count}", inline=True)
        embed.add_field(name="", value=f"```text\n{trigger_text}\n```", inline=False)
        embed.add_field(name="📚 自己紹介DB", value=f"📝 登録数: {len(user_intros)}", inline=True)
        embed.add_field(name="⚙️ Git", value=f"\n⚙️ Git: `{git_ver}`", inline=True)
        embed.set_footer(text="詳細は添付のテキストファイルをご確認ください")

        # 送信
        await interaction.followup.send(embed=embed, file=discord.File(report_filename))
        
        # 後片付け
        os.remove(report_filename)
        logging.info(f"Full monthly report sent by {interaction.user}")

    except Exception as e:
        await interaction.followup.send(f"レポート作成失敗: {e}")
        logging.error(f"Monthly report full error: {e}")

@client.event
async def on_message(message):
    global config, user_intros
    if message.author == client.user: return

    # 管理者判定フラグ
    is_admin = message.author.id in admin_ids

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
        embed.add_field(name="!monthly-report", value="月例レポートをEmbedで表示", inline=False)
        embed.add_field(name="!reload", value="設定とGit同期を手動実行", inline=False)
        embed.add_field(name="!logreset", value="ログファイルをリセット", inline=False)
        embed.add_field(name="!restart", value="ボットを再起動（管理者のみ）", inline=False)
        if is_admin:
            embed.set_footer(text="INFO：あなたのユーザーIDから管理権限を確認しました。\n管理者専用コマンドの使用が許可されています。")
        await message.channel.send(embed=embed)
        return
    
    if content == "!logreset":
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"{datetime.now()} [INFO] Log reset\n")
        await message.channel.send("🧹 ログをリセットしました。")
        return

    if content == "!collect-netatwi":
        if is_admin:
            await message.channel.send("🔄 ネタツイ収集中...")
            await collect_netatwi_section()
            await message.channel.send("✅ 収集完了。")
        else:
            await message.channel.send("⚠️ 権限がありません。")
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