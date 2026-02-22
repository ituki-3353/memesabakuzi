import discord
import yaml
import json
import os
import random
import sys
from dotenv import load_dotenv

# --- 1. 設定・環境読み込み ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
YAML_PATH = '/server/Dis_bot/responses.yml'
CONFIG_PATH = '/server/Dis_bot/config.json'

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# 応答データとランダムプールの管理
cached_responses = {}
response_pools = {}
config = {}

# --- 2. データロード関数 ---

def load_config():
    """JSONからホワイトリスト等の設定を読み込む"""
    if not os.path.exists(CONFIG_PATH):
        default = {"allowed_channels": []}
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(default, f, indent=4)
        return default
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_responses():
    """YAMLから応答リストを読み込み、プールをリセットする"""
    global cached_responses, response_pools
    if not os.path.exists(YAML_PATH):
        print(f"⚠️ {YAML_PATH} が見つかりません。")
        return False
    try:
        with open(YAML_PATH, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            if isinstance(data, dict):
                cached_responses = data
                response_pools = {} # プールをリセット
                return True
    except Exception as e:
        print(f"❌ YAMLエラー: {e}")
    return False

# 初期起動時のロード
config = load_config()
load_responses()

# --- 3. メインロジック ---

@client.event
async def on_ready():
    print(f'--- Bot Status: Online ---')
    print(f'Logged in as: {client.user.name}')
    print(f'Monitoring Channels: {config.get("allowed_channels", [])}')
    print(f'---')

@client.event
async def on_message(message):
    global config
    # Bot自身の発言は無視
    if message.author == client.user:
        return

    # チャンネル制限（ホワイトリスト外は無視）
    allowed_ids = config.get("allowed_channels", [])
    if message.channel.id not in allowed_ids:
        return

    content = message.content.strip()

    # --- A. 管理コマンド (!reload) ---
    if content == "!reload":
        config = load_config()
        if load_responses():
            await message.channel.send("🔄 **System Reloaded:** チャンネル設定と応答リストを最新に更新しました。")
        else:
            await message.channel.send("❌ **Error:** 更新に失敗しました。ログを確認してください。")
        return

    # --- B. 自動応答判定 ---
    for trigger, response in cached_responses.items():
        if trigger in content:
            final_text = ""

            # 1. リスト形式（山札方式で抽選）
            if isinstance(response, list):
                if not response: continue
                if trigger not in response_pools or not response_pools[trigger]:
                    pool = list(response)
                    random.SystemRandom().shuffle(pool)
                    response_pools[trigger] = pool
                final_text = response_pools[trigger].pop()
            
            # 2. 単一文字列
            elif isinstance(response, str):
                final_text = response
            
            # 3. その他
            else:
                final_text = str(response)

            # --- [userName] 置換 ---
            if "[userName]" in final_text:
                final_text = final_text.replace("[userName]", message.author.display_name)

            # 送信
            try:
                await message.channel.send(final_text)
            except Exception as e:
                print(f"❌ 送信エラー: {e}")
            
            break # 1メッセージに1反応

# --- 4. 実行 ---
if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN is missing!")
        sys.exit(1)
    client.run(TOKEN)