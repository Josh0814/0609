import os
import json
import random
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, make_response, jsonify

# Google GenAI
from google import genai
from google.genai import types

# Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# ----------------- 1. 初始化設定 -----------------

if os.path.exists('serviceAccountKey.json'):
    cred = credentials.Certificate('serviceAccountKey.json')
else:
    firebase_config = os.getenv('FIREBASE_CONFIG')
    cred_dict = json.loads(firebase_config)
    cred = credentials.Certificate(cred_dict)

firebase_admin.initialize_app(cred)

app = Flask(__name__)

# 帶入建鴻的專屬 Gemini API Key 🔑
client = genai.Client()


# ----------------- 2. 首頁控制項 -----------------

@app.route("/")
def index():
    R = "<h1>沙鹿美食 LINE 機器人後台 (動態隨機版)</h1><hr>"
    R += "<a href='/scrape_shalu' style='font-size:18px; font-weight:bold; color:green;'>"
    R += "👉 點擊這裡：執行沙鹿美食爬蟲（或由 AI 隨機生成新店家）</a><br><br>"
    R += "<p><b>Dialogflow Webhook 網址請填入：</b><br>"
    R += "<code style='background:#eee; padding:3px 8px; border-radius:4px;'>"
    R += "https://你的網址/webhook</code></p>"
    return R


# ----------------- 3. 沙鹿美食爬蟲 + AI 隨機補充 -----------------

@app.route("/scrape_shalu")
def scrape_shalu():
    """爬取沙鹿美食，若被阻擋則叫 Gemini 隨機推薦真店家寫入 Firestore"""
    url = "https://www.pixnet.net/tags/%E6%B2%99%E9%B9%B9%E7%BE%8E%E9%A3%9F" 
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    db = firestore.client()
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = "utf-8"
        sp = BeautifulSoup(res.text, "html.parser")
        articles = sp.select(".search-feed-item, .sc-brief") 
        
        # 🟢 狀況 A：網頁正常，成功爬到資料
        if articles:
            total = 0
            for item in articles[:10]: 
                try:
                    title = item.find("a").text.strip()
                    hyperlink = item.find("a").get("href")
                    intro = item.find(".content").text.strip() if item.find(".content") else "美味的沙鹿在地小吃推薦！"
                    doc_id = title.replace("/", "").replace(".", "").replace(" ", "")[:20] 
                    
                    doc = {
                        "title": title,
                        "intro": intro,
                        "hyperlink": hyperlink,
                        "lastUpdate": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    db.collection("沙鹿美食").document(doc_id).set(doc)
                    total += 1
                except:
                    continue
            return f"🎉 [網路爬蟲] 成功從網頁更新 {total} 筆沙鹿美食至資料庫！"

    except Exception as e:
        print(f"爬蟲微調或超時，自動切換至 AI 模式: {e}")

    # 🔵 狀況 B：網頁防爬或失敗 ➔ 叫 Gemini 隨機推薦 5 家沙鹿真實存在的店！
    ai_prompt = (
        "請幫我隨機推薦 5 家位於台灣台中市沙鹿區（或靜宜大學、弘光科大商圈）真實存在的知名美食店家或小吃。\n"
        "每次請盡量挑選不同的種類（如：肉圓、鴨肉飯、火鍋、手搖飲、宵夜、拉麵等），讓名單充滿隨機性與驚喜。\n"
        "請嚴格以 JSON 陣列格式回覆我，不要包含任何 Markdown 標籤（不要寫 ```json），格式如下：\n"
        "[\n"
        "  {\"title\": \"店名\", \"intro\": \"一句話介紹其特色菜或招牌\", \"hyperlink\": \"對應的 Google 地圖搜尋連結\"}\n"
        "]"
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=ai_prompt,
        )
        # 解析 AI 吐回來的隨機 JSON 資料
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        dynamic_foods = json.loads(raw_text)
        
        for idx, food in enumerate(dynamic_foods):
            doc_id = f"shalu_ai_{random.randint(1000, 9999)}_{idx}"
            food["lastUpdate"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.collection("沙鹿美食").document(doc_id).set(food)
            
        return f"🎲 [AI 隨機生成] 偵測到網頁防爬，Gemini 已為您隨機挑選 5 家沙鹿在地美食灌入資料庫！"
        
    except Exception as ai_err:
        return f"糟糕，爬蟲與 AI 同時開小差了：{str(ai_err)}"


# ----------------- 4. Dialogflow Webhook -----------------

@app.route("/webhook", methods=["POST"])
def webhook():
    """接收 Dialogflow 轉發過來的 LINE 訊息"""
    req = request.get_json(force=True)
    action = req["queryResult"]["action"]
    user_text = req["queryResult"]["queryText"]
    
    info = ""

    # 當觸發沙鹿美食動作 (shaluFood)
    if action == "shaluFood":
        db = firestore.client()
        collection_ref = db.collection("沙鹿美食")
        docs = collection_ref.stream()
        
        food_data = []
        for doc in docs:
            d = doc.to_dict()
            food_data.append(f"店名：{d.get('title')}\n簡介：{d.get('intro')}\n連結：{d.get('hyperlink')}")
        
        if food_data:
            # 🔀 關鍵核心：把資料庫撈出來的所有美食順序「隨機洗牌」！
            random.shuffle(food_data)
            
            # 取出前面幾個塞給 Gemini 潤飾，這樣每次對話的內容都會完全不同
            context = "\n\n".join(food_data[:4]) 
            prompt = (
                f"使用者在 LINE 問了：'{user_text}'。\n"
                f"以下是從資料庫中隨機抽出的沙鹿美食：\n{context}\n\n"
                f"請根據上述資料，用活潑、親切的口吻向使用者推薦（挑選2-3家即可），"
                f"適當加入一些 Emoji，並附上連結。字數控制在 180 字內。"
            )
        else:
            prompt = f"使用者想了解沙鹿美食，但目前資料庫沒資料。請直接用你的 AI 知識庫，隨機親切推薦 3 個不同的沙鹿在地小吃。"
            
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
            )
            ai_reply = response.text if response.text else "抱歉，我現在無法生成美食推薦。"
            info = f"我是黃建鴻設計的機器人，為您推薦的沙鹿美食如下：\n\n{ai_reply}"
        except Exception as e:
            info = f"我是黃建鴻設計的機器人，美食小幫手暫時故障：{str(e)}"

    # 預設聽不懂的對話（Default Fallback Intent）
    elif action == "input.unknown":
        instruction_text = (
            "你是一個生活在台中沙鹿的專業美食導遊智慧助理。"
            "請用一句話、極其幽默且簡短的口吻回答，並一定要把話題拉回沙鹿美食上。"         
        )
        ai_config = types.GenerateContentConfig(
            max_output_tokens=150, 
            system_instruction=instruction_text
        )
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash', 
                contents=user_text,
                config=ai_config,
            )
            ai_reply = response.text if response.text else "不好意思，我剛剛恍神了，請再說一次？"
            info = f"我是黃建鴻設計的機器人，{ai_reply}"
        except Exception as e:
            info = f"我是黃建鴻設計的機器人，AI 似乎累了：{str(e)}"

    return make_response(jsonify({"fulfillmentText": info}))


if __name__ == "__main__":
    app.run()