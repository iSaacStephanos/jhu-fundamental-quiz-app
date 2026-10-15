"""基礎看護技術論Ⅲ 無限問題集アプリ

【設計方針】
- 問題は Firestore の "questions" コレクション（＝問題バンク）に蓄積する
- 学生画面はバンクから「未出題の問題」をランダムに読むだけ → 待ち時間ゼロ
- 出題と同時にバックグラウンドでAIが新作問題を少量ずつ生成し、即時承認（approved / ai-live）
  としてバンクに自動追加する（事後パトロール方式）。使えば使うほどバンクが自動で増える。
- 管理者は管理画面からいつでもAI生成問題を事後確認・修正・却下できる。
"""

import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os
import re
import time
import random
import threading
import datetime

st.set_page_config(page_title="基礎看護技術論Ⅲ 無限問題集", layout="wide")

# ★ラジオボタンの文字サイズを問題文(H3)と同じ大きさに拡大するCSS
st.markdown("""
<style>
div[role="radiogroup"] p {
    font-size: 1.5rem !important;
    font-weight: bold !important;
}
</style>
""", unsafe_allow_html=True)

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ==========================================
# 1. 初期設定（APIキー・Firebase接続）
# ==========================================
def get_secret(name, default=""):
    """secrets.toml が無い環境（ローカル等）でも落ちないように安全に読む。"""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


GEMINI_API_KEY = get_secret("GEMINI_API_KEY")

DB_AVAILABLE = False
DB_ERROR = ""
db = None
try:
    if not firebase_admin._apps:
        firebase_json = get_secret("FIREBASE_JSON")
        local_key = os.path.join(APP_DIR, "firebase_key.json")
        if firebase_json:
            cred = credentials.Certificate(json.loads(firebase_json))
        elif os.path.exists(local_key):
            cred = credentials.Certificate(local_key)
        else:
            raise RuntimeError("FIREBASE_JSON も firebase_key.json も見つかりません")
        firebase_admin.initialize_app(cred)
    db = firestore.client()
    DB_AVAILABLE = True
except Exception as e:
    DB_ERROR = f"{type(e).__name__}: {e}"

QUESTIONS_COLLECTION = "questions"
SETTINGS_DOC = ("settings", "app")

# ==========================================
# 2. 称号システムの定義（2軸 × 3段階）
# ==========================================
STREAK_TITLES = [
    (100, "👑 マスター"),
    (50, "🏅 エキスパート"),
    (30, "🎖️ ルーキー"),
]

TOTAL_TITLES = [
    (1000, "🏆 レジェンド"),
    (500, "🔥 ハードワーカー"),
    (100, "🌱 チャレンジャー"),
]


def get_streak_title(max_streak):
    for threshold, title in STREAK_TITLES:
        if max_streak >= threshold:
            return title
    return "なし"


def get_next_streak_goal(current_streak):
    for threshold, title in sorted(STREAK_TITLES, key=lambda x: x[0]):
        if current_streak < threshold:
            return f"次の目標: {threshold}問連続（{title}）"
    return "最高称号【👑 マスター】達成！"


def get_total_title(total_answers):
    for threshold, title in TOTAL_TITLES:
        if total_answers >= threshold:
            return title
    return "なし"


def get_next_total_goal(total_answers):
    for threshold, title in sorted(TOTAL_TITLES, key=lambda x: x[0]):
        if total_answers < threshold:
            return f"次の目標: {threshold}問（{title}）"
    return "最高称号【🏆 レジェンド】達成！"


# ==========================================
# 3. 資料（知識データベース）
# ==========================================
FILE_MAP = {
    "呼吸器系のフィジカルアセスメント": "kokyu.txt",
    "循環器系のフィジカルアセスメント": "junkanki.txt",
}

# 資料内の【見出し】番号と、UI上の「出題範囲」の対応表
SECTION_MAP = {
    "呼吸器系のフィジカルアセスメント": {
        "解剖・生理": [1, 2],
        "体表解剖": [3],
        "問診・視診": [4, 5],
        "触診・打診": [6, 7],
        "聴診": [8],
    },
    "循環器系のフィジカルアセスメント": {
        "解剖・生理": [1, 2],
        "刺激伝導系と心電図": [3],
        "問診・視診・触診": [4, 5],
        "聴診": [6],
        "末梢動脈・静脈": [7],
        "浮腫と心不全": [8],
    },
}

AVAILABLE_DOMAINS = [
    "呼吸器系のフィジカルアセスメント",
    "循環器系のフィジカルアセスメント",
    "脳神経系のフィジカルアセスメント",
]

SUB_DOMAINS = {
    "呼吸器系のフィジカルアセスメント":
        ["すべて", "解剖・生理", "体表解剖", "問診・視診", "触診・打診", "聴診"],
    "循環器系のフィジカルアセスメント":
        ["すべて", "解剖・生理", "刺激伝導系と心電図", "問診・視診・触診",
         "聴診", "末梢動脈・静脈", "浮腫と心不全"],
}

DIFFICULTIES = ["標準（基本知識）", "応用（病態との関連・臨床的判断）"]

_KB_CACHE = {}


def load_knowledge_base(domain):
    """資料を読み込む。成功した内容だけを更新時刻付きでキャッシュする。"""
    file_name = FILE_MAP.get(domain)
    if not file_name:
        return "準備中"
    path = os.path.join(APP_DIR, file_name)
    if not os.path.exists(path):
        return "準備中"
    try:
        mtime = os.path.getmtime(path)
        cached = _KB_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        _KB_CACHE[path] = (mtime, text)
        return text
    except Exception:
        return "準備中"


def extract_sections(text, numbers):
    """【1. 〜】形式の見出しで区切られた本文から、指定番号の節だけを取り出す。"""
    if not numbers:
        return text
    parts = re.split(r"(?m)^(?=【\s*\d+\s*\.)", text)
    picked = []
    for part in parts:
        m = re.match(r"^【\s*(\d+)\s*\.", part)
        if m and int(m.group(1)) in numbers:
            picked.append(part.strip())
    return "\n\n".join(picked) if picked else text


def build_source_text(domain, sub_domain):
    full = load_knowledge_base(domain)
    if full == "準備中":
        return full
    if sub_domain and sub_domain != "すべて":
        numbers = SECTION_MAP.get(domain, {}).get(sub_domain)
        if numbers:
            return extract_sections(full, numbers)
    return full


# ==========================================
# 4. 初期問題（バンクへの取り込み用・DB障害時のフォールバック）
# ==========================================
SEED_QUESTIONS = [
    {"sub_domain": "解剖・生理", "question": "右主気管支は左主気管支よりも細く長いため、誤嚥性肺炎は左肺に起こりやすい。", "options": ["○", "×"], "answer_index": 1, "explanation": "右主気管支の方が「太く短く」、分岐角度が小さい（約25度）ため、誤嚥したものは右に入りやすく、誤嚥性肺炎は右肺に起こりやすいです。"},
    {"sub_domain": "解剖・生理", "question": "腹式呼吸は安静時の呼吸の大部分を担い、胸式呼吸よりも1回換気量が多くなる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。横隔膜の収縮による腹式呼吸は安静時の主な呼吸であり、胸式呼吸よりも多くの換気量を得られます。"},
    {"sub_domain": "解剖・生理", "question": "背面から見える肺の大部分は上葉であるため、背部の観察では上葉を意識することが重要である。", "options": ["○", "×"], "answer_index": 1, "explanation": "背面から見える肺の大部分は「下葉」です。そのため、背部の聴診や打診では主に下葉の状態を観察しています。"},
    {"sub_domain": "解剖・生理", "question": "PaO2（動脈血酸素分圧）が60Torr以下、またはSpO2が約90%の状態は呼吸不全を意味する。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。PaO2の基準値は80〜100Torrであり、60Torrを下回る（SpO2約90%以下）と呼吸不全と定義されます。"},
    {"sub_domain": "体表解剖", "question": "胸骨角（ルイ角）は第1肋骨の付着部であり、肋骨・肋間を数える起点となる。", "options": ["○", "×"], "answer_index": 1, "explanation": "胸骨角は「第2肋骨」の付着部です。第1肋骨は鎖骨の下にあり触れないため、ここを起点として肋骨を数えます。"},
    {"sub_domain": "体表解剖", "question": "前面の基準線は、胸骨中線・胸骨線・鎖骨中線の3つである。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。これらを基準線として、肋骨や肋間の番号と組み合わせて部位を表現します。"},
    {"sub_domain": "体表解剖", "question": "首を前に曲げたときに最も突出するのは、第1胸椎棘突起である。", "options": ["○", "×"], "answer_index": 1, "explanation": "最も突出するのは「第7頸椎棘突起」です。そのすぐ下が第1胸椎になります。"},
    {"sub_domain": "体表解剖", "question": "気管分岐部は、背面から見ると第4胸椎棘突起の高さに位置している。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。前面では胸骨角の高さ、背面では第4胸椎棘突起の高さが気管分岐部の目安となります。"},
    {"sub_domain": "体表解剖", "question": "肺尖部は鎖骨内側1/3より上方約3cmの位置にある。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。肺の頂上部分は鎖骨よりも上に飛び出しているため、鎖骨上窩の聴診も重要になります。"},
    {"sub_domain": "問診・視診", "question": "起座呼吸は、横になると苦しく座ると楽になる状態で、心不全や喘息などが原因で起こる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。心不全などで肺にうっ血がある場合、横になると静脈還流量が増えて呼吸が苦しくなるため、起き上がることで呼吸を楽にしようとします。"},
    {"sub_domain": "問診・視診", "question": "チェーンストークス呼吸は、深く大きな規則的な呼吸が続く異常呼吸であり、糖尿病性ケトアシドーシスでみられる。", "options": ["○", "×"], "answer_index": 1, "explanation": "それは「クスマウル呼吸」の説明です。チェーンストークス呼吸は、無呼吸と深浅の周期を繰り返す呼吸で、心不全や脳障害などでみられます。"},
    {"sub_domain": "問診・視診", "question": "チアノーゼは口唇や爪床が青紫色になるサインであるが、貧血の患者では現れにくい。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。チアノーゼは還元ヘモグロビンが一定量以上増えることで現れるため、もともとヘモグロビンが少ない貧血状態では出現しにくくなります。"},
    {"sub_domain": "触診・打診", "question": "背部での胸郭の可動性の触診において、正常であれば深吸気時に左右の母指の間は約4cm広がる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。第10肋骨付近で左右の母指を合わせ、深吸気で対称に約4cm広がるのが正常な所見です。"},
    {"sub_domain": "触診・打診", "question": "正常な肺野を打診すると、鈍くつまったような濁音が聴こえる。", "options": ["○", "×"], "answer_index": 1, "explanation": "正常な肺野は空気を多く含むため、響きのある低い「共鳴音（清音）」が聴こえます。濁音は肺炎や胸水貯留などで聴こえます。"},
    {"sub_domain": "聴診", "question": "捻髪音（細かい断続性副雑音）は「ブクブク」と聴こえ、気道内に分泌物が溜まっている状態で聴取される。", "options": ["○", "×"], "answer_index": 1, "explanation": "分泌物が溜まっている状態で聴こえる「ブクブク」という音は「水泡音（粗い断続性副雑音）」です。捻髪音は「パリパリ」と聴こえ、間質性肺炎などで聴取されます。"},
    {"sub_domain": "聴診", "question": "ストライダーは吸気時に聴こえる高調性の「ゼーゼー」という音で、上気道の狭窄を示す緊急所見である。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。喉頭浮腫や気道異物などによる上気道の狭窄を示しており、窒息の恐れがあるため迅速な対応が必要です。"},
    {"sub_domain": "聴診", "question": "呼吸音の聴診は、低音を聴き取るためベル型聴診器を皮膚に軽く当てて行う。", "options": ["○", "×"], "answer_index": 1, "explanation": "呼吸音は高音であるため「膜型聴診器」を使用します。また、皮膚に跡が残るくらいの強さでしっかりと押し当てて聴診します。"},
    {"sub_domain": "聴診", "question": "正常な肺胞呼吸音は、大きく高い音であり、吸気と呼気の間に休止期（ポーズ）が聴取される。", "options": ["○", "×"], "answer_index": 1, "explanation": "大きく高く、ポーズがあるのは「気管呼吸音」です。肺胞呼吸音は柔らかく低い音で、呼気は初期のみ聴こえます。"},
    {"sub_domain": "聴診", "question": "COPDや気管支喘息の患者では、気道の狭窄により「呼気延長」が聴取されることがある。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。気道が狭くなる疾患（COPDや気管支喘息など）では、息を吐き出すのに時間がかかるため呼気が延長します。"},
]
SEED_DOMAIN = "呼吸器系のフィジカルアセスメント"


# ==========================================
# 5. AI問題生成（★Streamlit に一切依存しない純粋な処理）
# ==========================================
MODEL_PREFERENCE = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer_index": {"type": "integer"},
                    "explanation": {"type": "string"},
                },
                "required": ["question", "answer_index", "explanation"],
            },
        }
    },
    "required": ["questions"],
}

_MODEL_LOCK = threading.Lock()
RESOLVED_MODEL = {"name": ""}


def _resolve_model(client):
    with _MODEL_LOCK:
        if RESOLVED_MODEL["name"]:
            return RESOLVED_MODEL["name"]

    names = []
    try:
        for m in client.models.list():
            raw = getattr(m, "name", "") or ""
            names.append(raw.split("/")[-1])
    except Exception:
        names = []

    chosen = None
    for want in MODEL_PREFERENCE:
        if not names or want in names:
            chosen = want
            break
    if chosen is None:
        flashes = [n for n in names if "flash" in n and "embedding" not in n]
        chosen = sorted(flashes, reverse=True)[0] if flashes else MODEL_PREFERENCE[0]

    with _MODEL_LOCK:
        RESOLVED_MODEL["name"] = chosen
    return chosen


def _call_gemini(api_key, prompt):
    errors = []

    # (1) 新SDK: google-genai
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        model = _resolve_model(client)

        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": QUIZ_SCHEMA,
                    "temperature": 1.15,
                },
            )
            text = getattr(resp, "text", None)
            if text:
                return text
            errors.append("generate_content: text が空")
        except Exception as e:
            errors.append(f"generate_content: {type(e).__name__}: {e}")

    except Exception as e:
        errors.append(f"google-genai 利用不可: {type(e).__name__}: {e}")

    # (2) 旧SDK: google-generativeai
    try:
        import google.generativeai as old_genai
        old_genai.configure(api_key=api_key)
        last = None
        for name in MODEL_PREFERENCE:
            try:
                model = old_genai.GenerativeModel(
                    name,
                    generation_config={
                        "response_mime_type": "application/json",
                        "temperature": 1.15,
                    },
                )
                resp = model.generate_content(prompt)
                if getattr(resp, "text", None):
                    with _MODEL_LOCK:
                        RESOLVED_MODEL["name"] = name
                    return resp.text
                last = f"{name}: text が空"
            except Exception as e:
                last = f"{name}: {type(e).__name__}: {e}"
        errors.append(f"旧SDK: {last}")
    except Exception as e:
        errors.append(f"旧SDK 利用不可: {type(e).__name__}: {e}")

    raise RuntimeError(" / ".join(errors))


def normalize_text(s):
    """重複判定用に、記号や空白の揺れを落とした比較キーを作る。"""
    s = re.sub(r"\s+", "", str(s))
    s = re.sub(r"[。、，．,\.「」『』（）\(\)\[\]”\"'！!？\?]", "", s)
    return s


def coerce_quiz_items(raw):
    data = raw

    if isinstance(data, str):
        text = data.strip()
        fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
        if fence:
            text = fence.group(1).strip()
        try:
            data = json.loads(text)
        except Exception:
            m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
            if not m:
                return []
            try:
                data = json.loads(m.group(1))
            except Exception:
                return []

    if isinstance(data, dict):
        for key in ("questions", "items", "data", "quiz"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return []

    cleaned = []
    for item in data:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        explanation = str(item.get("explanation", "")).strip()
        if not question:
            continue
        try:
            idx = int(str(item.get("answer_index")).strip())
        except Exception:
            continue
        if idx not in (0, 1):
            continue
        cleaned.append({
            "question": question,
            "options": ["○", "×"],
            "answer_index": idx,
            "explanation": explanation or "（解説なし）",
        })
    return cleaned


def drop_already_asked(items, asked_keys):
    out = []
    seen = set(asked_keys)
    for item in items:
        key = normalize_text(item["question"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_prompt(domain, sub_domain, difficulty, source_text, avoid_questions, n):
    avoid_block = ""
    if avoid_questions:
        sample = avoid_questions[:40]
        listed = "\n".join(f"- {q}" for q in sample)
        avoid_block = (
            "\n【すでにバンクにある問題（文意が重なるものは絶対に作らず、"
            "別の着眼点で作ること）】\n" + listed
        )

    return f"""あなたは優秀な看護教育の専門家です。以下の【参考資料】に記載されている事実のみに基づいて、○×問題（正誤問題）を「{n}問」作成してください。
外部の知識は絶対に混ぜないでください。

【条件】
- 出題領域: {domain}
- 出題範囲: {sub_domain}
- 難易度: {difficulty}
- 選択肢は「○」と「×」の2つで固定です。answer_index は 0（○が正しい）または 1（×が正しい）の整数で答えてください。
- 正しい文と誤りの文を、おおよそ半分ずつ混ぜてください。
- 解説は要点を絞って2〜3文で簡潔に。
- 資料の記述1つから複数問作る場合は、着眼点（数値・部位・左右・対比・順序・適応・原因疾患など）を必ず変えてください。
- 同じ事実を言い換えただけの問題は作らないでください。
{avoid_block}

【参考資料】
{source_text}
"""


def generate_quiz_core(api_key, domain, sub_domain, difficulty,
                       source_text, avoid_keys, avoid_questions,
                       n=4, attempts=2):
    """問題を作る純粋な処理。成功なら (問題リスト, "")、失敗なら ([], 理由)。"""
    if source_text == "準備中":
        return [], "資料ファイルが見つかりません。"
    if not api_key:
        return [], "GEMINI_API_KEY が設定されていません（Streamlit の Secrets を確認してください）。"

    prompt = build_prompt(domain, sub_domain, difficulty, source_text, avoid_questions, n)

    last_error = ""
    for i in range(attempts):
        try:
            raw = _call_gemini(api_key, prompt)
            items = drop_already_asked(coerce_quiz_items(raw), avoid_keys)
            if items:
                return items, ""
            last_error = "AIの応答から新しい問題を取り出せませんでした（既存と重複、または形式不正）。"
        except Exception as e:
            last_error = str(e)
        if i < attempts - 1:
            time.sleep(1.0)
    return [], last_error


# ==========================================
# 6. 問題バンク（Firestore）
# ==========================================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_bank():
    """バンクの全問題を読む（120秒キャッシュ・全ユーザー共有）。"""
    if not DB_AVAILABLE:
        return []
    out = []
    for doc in db.collection(QUESTIONS_COLLECTION).stream():
        d = doc.to_dict() or {}
        d["id"] = doc.id
        out.append(d)
    return out


def invalidate_bank():
    try:
        fetch_bank.clear()
    except Exception:
        pass


@st.cache_data(ttl=120, show_spinner=False)
def fetch_settings():
    default = {"allow_live_generation": True}
    if not DB_AVAILABLE:
        return default
    try:
        doc = db.collection(SETTINGS_DOC[0]).document(SETTINGS_DOC[1]).get()
        if doc.exists:
            default.update(doc.to_dict() or {})
    except Exception:
        pass
    return default


def save_settings(values):
    if not DB_AVAILABLE:
        return
    db.collection(SETTINGS_DOC[0]).document(SETTINGS_DOC[1]).set(values, merge=True)
    try:
        fetch_settings.clear()
    except Exception:
        pass


def bank_add(items, domain, sub_domain, difficulty, status="approved", source="ai"):
    """問題をバンクに追加する。既存と重複するものは追加しない。

    戻り値: (追加された問題オブジェクトのリスト, 重複で除外した件数)
    """
    if not DB_AVAILABLE or not items:
        return [], 0

    existing = {q.get("qkey") for q in fetch_bank()}
    batch = db.batch()
    added_docs = []
    skipped = 0

    for item in items:
        qkey = normalize_text(item["question"])
        if qkey in existing:
            skipped += 1
            continue
        existing.add(qkey)

        target_sub = item.get("sub_domain") or sub_domain
        ref = db.collection(QUESTIONS_COLLECTION).document()
        doc_data = {
            "domain": domain,
            "sub_domain": target_sub,
            "difficulty": difficulty,
            "question": item["question"],
            "options": ["○", "×"],
            "answer_index": int(item["answer_index"]),
            "explanation": item.get("explanation", ""),
            "status": status,
            "qkey": qkey,
            "source": source,
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        batch.set(ref, doc_data)
        doc_with_id = dict(doc_data)
        doc_with_id["id"] = ref.id
        added_docs.append(doc_with_id)

    if added_docs:
        batch.commit()
        invalidate_bank()
    return added_docs, skipped


def ensure_seed_in_bank():
    """バンクに初期問題がまだ入っていなければ自動で承認済みとして投入する。"""
    if not DB_AVAILABLE:
        return
    bank = fetch_bank()
    if not bank:
        bank_add(SEED_QUESTIONS, SEED_DOMAIN, "すべて", DIFFICULTIES[0],
                 status="approved", source="premade")


def bank_set_status(question_id, status):
    if not DB_AVAILABLE:
        return
    db.collection(QUESTIONS_COLLECTION).document(question_id).set(
        {"status": status}, merge=True)
    invalidate_bank()


def bank_update(question_id, fields):
    if not DB_AVAILABLE:
        return
    db.collection(QUESTIONS_COLLECTION).document(question_id).set(fields, merge=True)
    invalidate_bank()


def bank_filter(bank, domain, sub_domain=None, status=None):
    out = []
    for q in bank:
        if q.get("domain") != domain:
            continue
        if status and q.get("status") != status:
            continue
        if sub_domain and sub_domain != "すべて" and q.get("sub_domain") != sub_domain:
            continue
        out.append(q)
    return out


def bank_counts(domain):
    bank = fetch_bank()
    rows = []
    for sd in SUB_DOMAINS.get(domain, ["すべて"]):
        if sd == "すべて":
            continue
        items = bank_filter(bank, domain, sd)
        rows.append({
            "出題範囲": sd,
            "承認済み（出題中）": sum(1 for q in items if q.get("status") == "approved"),
            "うちAI自動追加": sum(1 for q in items if q.get("status") == "approved" and q.get("source") == "ai-live"),
            "未確認（保留）": sum(1 for q in items if q.get("status") == "pending"),
            "却下": sum(1 for q in items if q.get("status") == "rejected"),
        })
    return rows


def pick_from_bank(domain, sub_domain, difficulty, seen_ids):
    """バンクから、その学生がまだ解いていない承認済み問題をランダムに1問返す。"""
    bank = fetch_bank()
    blocked = set(seen_ids) | set(st.session_state.session_seen)
    cands = [
        q for q in bank_filter(bank, domain, sub_domain, status="approved")
        if q["id"] not in blocked
    ]
    if not cands:
        return None
    same = [q for q in cands if q.get("difficulty") == difficulty]
    return random.choice(same or cands)


def count_unseen(domain, sub_domain, seen_ids):
    bank = fetch_bank()
    blocked = set(seen_ids) | set(st.session_state.session_seen)
    return sum(
        1 for q in bank_filter(bank, domain, sub_domain, status="approved")
        if q["id"] not in blocked
    )


# ==========================================
# 7. セッション管理・常時バックグラウンド生成
# ==========================================
DEFAULTS = {
    "current_question": None,
    "answered": False,
    "student_id": "",
    "is_correct": False,
    "session_seen": [],
    "session_keys": [],
    "fallback_pool": [],
    "last_ai_error": "",
    "pending_next": False,
    "last_settings": {},
    "celebrate_msgs": [],  # 称号達成時のお祝いメッセージ
}
for _k, _v in DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def get_prefetch_box():
    if "prefetch" not in st.session_state:
        st.session_state.prefetch = {
            "lock": threading.Lock(),
            "items": [],
            "error": "",
            "running": False,
            "epoch": 0,
            "meta": None,
        }
    return st.session_state.prefetch


def _prefetch_worker(box, epoch, api_key, domain, sub_domain, difficulty,
                     source_text, avoid_keys, avoid_questions, n):
    try:
        items, error = generate_quiz_core(
            api_key, domain, sub_domain, difficulty,
            source_text, avoid_keys, avoid_questions, n=n,
        )
    except Exception as e:
        items, error = [], f"{type(e).__name__}: {e}"
    with box["lock"]:
        if box["epoch"] == epoch:
            box["items"].extend(items)
            box["error"] = error
        box["running"] = False


def resolve_target_sub_domain(domain, sub_domain):
    """「すべて」が選ばれている場合は、具体的なサブ領域をランダムに1つ選んで作問させる。"""
    if sub_domain != "すべて":
        return sub_domain
    subs = [s for s in SUB_DOMAINS.get(domain, ["すべて"]) if s != "すべて"]
    return random.choice(subs) if subs else "すべて"


def start_background_generation(domain, sub_domain, difficulty, n=4):
    """裏でAI作問を開始する（メイン画面を止めずに即座に返る）。"""
    if not GEMINI_API_KEY:
        return
    target_sub = resolve_target_sub_domain(domain, sub_domain)
    source_text = build_source_text(domain, target_sub)
    if source_text == "準備中":
        return
    box = get_prefetch_box()
    with box["lock"]:
        if box["running"] or box["items"]:
            return
        box["running"] = True
        epoch = box["epoch"]
        box["meta"] = (domain, target_sub, difficulty)

    bank_qs = [q.get("question", "") for q in
               bank_filter(fetch_bank(), domain, target_sub)]
    avoid_keys = [normalize_text(q) for q in bank_qs]
    avoid_keys += list(st.session_state.session_keys)
    random.shuffle(bank_qs)

    threading.Thread(
        target=_prefetch_worker,
        args=(box, epoch, GEMINI_API_KEY, domain, target_sub, difficulty,
              source_text, avoid_keys, bank_qs, n),
        daemon=True,
    ).start()


def harvest_background():
    """裏で完成した問題を回収し、即時承認（approved / ai-live）としてバンクに登録する。"""
    box = get_prefetch_box()
    with box["lock"]:
        got = box["items"]
        box["items"] = []
        error = box["error"]
        box["error"] = ""
        meta = box["meta"]

    if not got:
        if error:
            st.session_state.last_ai_error = error
        return

    st.session_state.last_ai_error = ""
    if meta and DB_AVAILABLE:
        g_domain, g_sub, g_diff = meta
        try:
            # ★改善策2: 最初から status="approved" としてバンクに自動追加する
            added_docs, _ = bank_add(
                got, g_domain, g_sub, g_diff,
                status="approved", source="ai-live"
            )
            return
        except Exception:
            pass

    # DB未接続時などのフォールバック
    st.session_state.fallback_pool.extend(got)


def is_generating():
    box = get_prefetch_box()
    with box["lock"]:
        return box["running"]


def wait_for_generation(timeout=30):
    box = get_prefetch_box()
    with box["lock"]:
        if not box["running"]:
            return
    with st.spinner("AIが新しい問題を作成しています..."):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with box["lock"]:
                if (not box["running"]) or box["items"]:
                    break
            time.sleep(0.25)


# --- 出題処理 ---------------------------------------------------------------
def set_question(q, question_id=None, user_ref=None):
    st.session_state.current_question = {
        "question": q["question"],
        "options": q.get("options") or ["○", "×"],
        "answer_index": int(q["answer_index"]),
        "explanation": q.get("explanation", ""),
        "id": question_id,
    }
    st.session_state.answered = False
    st.session_state.is_correct = False
    st.session_state.session_keys.append(normalize_text(q["question"]))
    if question_id:
        st.session_state.session_seen.append(question_id)
        if user_ref is not None:
            try:
                user_ref.set(
                    {"seen_ids": firestore.ArrayUnion([question_id])}, merge=True)
            except Exception:
                pass


def advance_to_next_question(domain, sub_domain, difficulty, seen_ids, user_ref):
    """次の問題を出題する。
    裏で作られた新問があれば先にバンクへ合流させ、既存問題と混ぜてランダム抽出する。
    """
    if build_source_text(domain, sub_domain) == "準備中":
        return False, "この領域の問題は現在準備中です。別の領域を選択してください。"

    # (0) 裏で完成しているAI問題があれば回収してバンク（approved）に合流させる
    harvest_background()

    # (1) バンク（初期ストック＋AI自動追加分）からランダムに未出題の1問を選ぶ
    q = pick_from_bank(domain, sub_domain, difficulty, seen_ids)
    if q:
        set_question(q, question_id=q["id"], user_ref=user_ref)
        # ★バンク内の未出題数が30問未満なら、次の問題以降のために裏でAI補充を走らせる
        if fetch_settings().get("allow_live_generation", True):
            if count_unseen(domain, sub_domain, seen_ids) < 30:
                start_background_generation(domain, sub_domain, difficulty, n=4)
        return True, ""

    # (2) DBが無い環境では、コード内の初期問題を使う
    if not DB_AVAILABLE:
        used = set(st.session_state.session_keys)
        left = [s for s in SEED_QUESTIONS
                if (sub_domain == "すべて" or s["sub_domain"] == sub_domain)
                and normalize_text(s["question"]) not in used]
        if left and domain == SEED_DOMAIN:
            set_question(random.choice(left))
            start_background_generation(domain, sub_domain, difficulty, n=4)
            return True, ""

    # (3) バンクの未出題が0問の場合 → AI生成（許可されている場合）
    settings = fetch_settings()
    if not settings.get("allow_live_generation", True):
        return False, "この範囲の問題をすべて解き終えました。別の範囲を選んでください。"

    if is_generating():
        wait_for_generation()
        harvest_background()
        q = pick_from_bank(domain, sub_domain, difficulty, seen_ids)
        if q:
            set_question(q, question_id=q["id"], user_ref=user_ref)
            start_background_generation(domain, sub_domain, difficulty, n=4)
            return True, ""

    # まだ裏で動いていなかった場合はその場で生成してバンクに登録する
    target_sub = resolve_target_sub_domain(domain, sub_domain)
    source_text = build_source_text(domain, target_sub)
    bank_qs = [x.get("question", "") for x in
               bank_filter(fetch_bank(), domain, target_sub)]
    with st.spinner("新しい問題を作成しています..."):
        items, error = generate_quiz_core(
            GEMINI_API_KEY, domain, target_sub, difficulty, source_text,
            [normalize_text(x) for x in bank_qs] + list(st.session_state.session_keys),
            bank_qs, n=4,
        )
    if items:
        st.session_state.last_ai_error = ""
        if DB_AVAILABLE:
            added_docs, _ = bank_add(items, domain, target_sub, difficulty,
                                     status="approved", source="ai-live")
            if added_docs:
                chosen = random.choice(added_docs)
                set_question(chosen, question_id=chosen["id"], user_ref=user_ref)
                start_background_generation(domain, sub_domain, difficulty, n=4)
                return True, ""
        st.session_state.fallback_pool.extend(items)
    else:
        st.session_state.last_ai_error = error

    if st.session_state.fallback_pool:
        set_question(st.session_state.fallback_pool.pop(0))
        start_background_generation(domain, sub_domain, difficulty, n=4)
        return True, ""

    return False, st.session_state.last_ai_error or "新しい問題を作成できませんでした。"


# ==========================================
# 8. UI
# ==========================================
ensure_seed_in_bank()

mode = st.sidebar.radio("モード選択", ["学生用（クイズ演習）", "管理者用（モニタリング）"])

if RESOLVED_MODEL["name"]:
    st.sidebar.caption(f"使用モデル: {RESOLVED_MODEL['name']}")
if not DB_AVAILABLE:
    st.sidebar.warning("成績の保存先（Firebase）に接続できていません。")

# ------------------------------------------------------------------ 学生画面
if mode == "学生用（クイズ演習）":
    st.title("🩺 基礎看護技術論Ⅲ 無限問題集")

    if not DB_AVAILABLE:
        st.warning(
            "⚠️ データベースに接続できないため、**記録なしモード**で動作しています。"
            "問題演習は可能ですが、成績は保存されません。"
        )
        with st.expander("詳細（担当者向け）"):
            st.code(DB_ERROR or "不明", language="text")

    col_id, col_name = st.columns([1, 1])
    with col_id:
        student_id = st.text_input("学籍番号を入力してください（半角数字7桁）",
                                   value=st.session_state.student_id)
    with col_name:
        student_name = st.text_input("氏名（またはニックネーム）")

    if not student_id:
        st.warning("演習を開始するには学籍番号を入力してください。")
        st.stop()

    if not (len(student_id) == 7 and student_id.isdigit()):
        st.error("エラー：学籍番号は「半角数字7桁」で入力してください。")
        st.stop()

    st.session_state.student_id = student_id

    user_ref = None
    user_data = {
        "name": student_name,
        "current_streak": 0,
        "max_streak": 0,
        "has_title": False,
        "streak_title": "なし",
        "total_title": "なし",
        "total_answers": 0,
        "correct_answers": 0,
        "seen_ids": [],
    }
    if DB_AVAILABLE:
        try:
            user_ref = db.collection("students").document(student_id)
            user_doc = user_ref.get()
            if user_doc.exists:
                user_data.update(user_doc.to_dict() or {})
                if student_name and user_data.get("name") != student_name:
                    user_data["name"] = student_name
                    user_ref.set({"name": student_name}, merge=True)
            else:
                user_ref.set(user_data)
        except Exception as e:
            st.warning(f"成績データの読み込みに失敗しました（演習は継続できます）: {type(e).__name__}")
            user_ref = None

    seen_ids = user_data.get("seen_ids") or []
    current_streak = user_data.get("current_streak", 0)
    max_streak = user_data.get("max_streak", 0)
    total_answers = user_data.get("total_answers", 0)

    streak_title = get_streak_title(max_streak)
    total_title = get_total_title(total_answers)

    # 称号達成直後のお祝い演出
    if st.session_state.celebrate_msgs:
        st.balloons()
        for msg in st.session_state.celebrate_msgs:
            st.success(msg)
        st.session_state.celebrate_msgs = []

    st.divider()

    # ★ 称号バッジと進捗メトリクスの2カラム表示
    m_col1, m_col2 = st.columns(2)
    with m_col1:
        st.metric(
            label=f"現在の連続正解数（最大記録: {max_streak} 問）",
            value=f"{current_streak} 問",
            delta=get_next_streak_goal(current_streak),
        )
        if streak_title != "なし":
            st.success(f"**連続正解称号：【{streak_title}】**")
        else:
            st.caption("連続正解称号：未獲得（30問連続正解で【🎖️ ルーキー】獲得！）")

    with m_col2:
        st.metric(
            label="これまで取り組んだ問題数（総解答数）",
            value=f"{total_answers} 問",
            delta=get_next_total_goal(total_answers),
        )
        if total_title != "なし":
            st.info(f"**解答数称号：【{total_title}】**")
        else:
            st.caption("解答数称号：未獲得（累計100問解答で【🌱 チャレンジャー】獲得！）")

    st.divider()

    c1, c2, c3, c4 = st.columns([3, 3, 3, 3])
    with c1:
        domain = st.selectbox("学習する領域", AVAILABLE_DOMAINS)
    with c2:
        sub_domain = st.selectbox("出題範囲", SUB_DOMAINS.get(domain, ["すべて"]))
    with c3:
        difficulty = st.selectbox("難易度", DIFFICULTIES)
    with c4:
        st.write("")
        st.write("")
        next_clicked = st.button("次の問題へ 🎲", use_container_width=True)

    current_settings = {"domain": domain, "sub_domain": sub_domain, "difficulty": difficulty}
    if st.session_state.last_settings != current_settings:
        st.session_state.last_settings = current_settings
        st.session_state.fallback_pool = []
        box = get_prefetch_box()
        with box["lock"]:
            box["epoch"] += 1
            box["items"] = []

    if st.session_state.pending_next:
        st.session_state.pending_next = False
        next_clicked = True

    if next_clicked:
        ok, msg = advance_to_next_question(domain, sub_domain, difficulty, seen_ids, user_ref)
        if not ok:
            st.error(f"⚠️ {msg}")
            if st.session_state.last_ai_error:
                with st.expander("詳細（担当者向け）"):
                    st.code(st.session_state.last_ai_error, language="text")

    if st.session_state.current_question:
        q = st.session_state.current_question
        st.markdown(f"### Q. {q['question']}")

        is_disabled = st.session_state.answered
        st.markdown("### 選択肢を選んでください：")
        choice = st.radio("選択肢", q["options"],
                          key=f"quiz_choice_{len(st.session_state.session_keys)}",
                          index=None, disabled=is_disabled, horizontal=True,
                          label_visibility="collapsed")

        if not is_disabled:
            if st.button("回答を送信する"):
                if choice is None:
                    st.warning("「○」または「×」を選んでから送信してください。")
                else:
                    st.session_state.answered = True
                    is_correct = (q["options"].index(choice) == int(q["answer_index"]))
                    st.session_state.is_correct = is_correct

                    prev_max_streak = user_data.get("max_streak", 0)
                    prev_total = user_data.get("total_answers", 0)

                    new_total = prev_total + 1
                    user_data["total_answers"] = new_total

                    if is_correct:
                        user_data["correct_answers"] = user_data.get("correct_answers", 0) + 1
                        new_streak = user_data.get("current_streak", 0) + 1
                        user_data["current_streak"] = new_streak
                        if new_streak > prev_max_streak:
                            user_data["max_streak"] = new_streak
                    else:
                        new_streak = 0
                        user_data["current_streak"] = 0

                    # ★ 称号ランクアップ判定とお祝いメッセージ登録
                    new_max_streak = user_data.get("max_streak", 0)
                    new_streak_title = get_streak_title(new_max_streak)
                    new_total_title = get_total_title(new_total)

                    user_data["streak_title"] = new_streak_title
                    user_data["total_title"] = new_total_title
                    user_data["has_title"] = (new_streak_title != "なし" or new_total_title != "なし")

                    celebrations = []
                    for threshold, t_name in STREAK_TITLES:
                        if is_correct and new_streak == threshold:
                            celebrations.append(f"🎊 連続 {threshold} 問正解達成！称号【{t_name}】を獲得しました！")
                    for threshold, t_name in TOTAL_TITLES:
                        if new_total == threshold:
                            celebrations.append(f"🎊 累計 {threshold} 問解答達成！称号【{t_name}】を獲得しました！")
                    st.session_state.celebrate_msgs = celebrations

                    # 回答したタイミングでも裏側の自動作問をトリガーしておく
                    if fetch_settings().get("allow_live_generation", True):
                        if count_unseen(domain, sub_domain, seen_ids) < 30:
                            start_background_generation(domain, sub_domain, difficulty, n=4)

                    if user_ref is not None:
                        try:
                            payload = {k: v for k, v in user_data.items() if k != "seen_ids"}
                            user_ref.set(payload, merge=True)
                        except Exception as e:
                            st.warning(f"成績の保存に失敗しました: {type(e).__name__}")
                    st.rerun()
        else:
            if st.session_state.is_correct:
                st.success("🎉 正解です！")
            else:
                st.error("❌ 不正解です...")

            st.write("---")
            st.info(f"**【解説】**\n{q.get('explanation', '')}")
            st.write("---")

            if st.button("次の問題へ ➡️", type="primary"):
                st.session_state.pending_next = True
                st.rerun()

            unseen = count_unseen(domain, sub_domain, seen_ids)
            if unseen > 0:
                st.caption(f"この範囲のストック: あと {unseen} 問（待ち時間ゼロで出題されます / 裏でAIが随時問題を追加中）")
            else:
                st.caption("現在AIが新しい問題を裏で追加作成しています。「次の問題へ」を押すと出題されます。")

# ------------------------------------------------------------ 管理者画面
elif mode == "管理者用（モニタリング）":
    st.title("📊 管理者画面")

    admin_password = get_secret("ADMIN_PASSWORD", "admin1234")
    admin_pass = st.text_input("管理者パスワードを入力してください", type="password")
    if admin_pass != admin_password:
        st.info("正しいパスワードを入力すると管理機能が表示されます。")
        if admin_password == "admin1234":
            st.caption("※ 現在は初期パスワードのままです。Streamlit の Secrets に "
                       "ADMIN_PASSWORD を設定して変更してください。")
        st.stop()

    if not DB_AVAILABLE:
        st.error("データベースに接続できないため、管理機能を利用できません。")
        st.code(DB_ERROR or "不明", language="text")
        st.stop()

    tab_bank, tab_review, tab_scores = st.tabs(
        ["🏦 問題バンク（一括生成）", "✅ 内容の確認・パトロール", "📈 成績モニタリング"])

    # --- 生成 -------------------------------------------------------------
    with tab_bank:
        st.subheader("バンクの在庫状況")
        g_domain = st.selectbox("領域", AVAILABLE_DOMAINS, key="bank_domain")
        rows = bank_counts(g_domain)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.info("この領域にはまだ問題がありません。")

        st.divider()
        st.subheader("管理者による一括生成")
        gen_status_choice = st.radio(
            "生成した問題の初期ステータス",
            ["即時承認（すぐに学生へ出題する）", "未確認（確認タブで承認してから出題する）"],
            horizontal=True,
        )
        target_status = "approved" if "即時承認" in gen_status_choice else "pending"

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            g_sub = st.selectbox(
                "出題範囲",
                [s for s in SUB_DOMAINS.get(g_domain, ["すべて"]) if s != "すべて"],
                key="bank_sub")
        with col_b:
            g_diff = st.selectbox("難易度", DIFFICULTIES, key="bank_diff")
        with col_c:
            g_batches = st.number_input("生成回数（1回あたり約8問）",
                                        min_value=1, max_value=20, value=2)

        if build_source_text(g_domain, g_sub) == "準備中":
            st.warning("この領域の資料ファイルがまだありません。")
        elif st.button("🚀 一括生成を開始する", type="primary"):
            progress = st.progress(0.0, text="準備中...")
            total_added = total_skipped = 0
            errors = []
            for i in range(int(g_batches)):
                bank_qs = [q.get("question", "") for q in
                           bank_filter(fetch_bank(), g_domain, g_sub)]
                random.shuffle(bank_qs)
                items, err = generate_quiz_core(
                    GEMINI_API_KEY, g_domain, g_sub, g_diff,
                    build_source_text(g_domain, g_sub),
                    [normalize_text(x) for x in bank_qs], bank_qs, n=8,
                )
                if items:
                    added_docs, s = bank_add(items, g_domain, g_sub, g_diff,
                                             status=target_status, source="ai-admin")
                    total_added += len(added_docs)
                    total_skipped += s
                elif err:
                    errors.append(err)
                progress.progress((i + 1) / int(g_batches),
                                  text=f"{i + 1}/{int(g_batches)} 回目完了 / "
                                       f"追加 {total_added} 問")
            progress.empty()

            if total_added:
                st.success(f"✅ {total_added} 問を追加しました（重複除外: {total_skipped} 問）。")
            else:
                st.warning(f"新しい問題を追加できませんでした（重複除外: {total_skipped} 問）。")
            if errors:
                with st.expander("エラー詳細"):
                    for e in errors:
                        st.code(e, language="text")

        st.divider()
        st.subheader("自動補充の設定")
        settings = fetch_settings()
        allow = st.checkbox(
            "学生の演習中に、裏でAIが新しい問題を自動作成してバンク（承認済み）へ追加する",
            value=bool(settings.get("allow_live_generation", True)),
            help="オンにすると、学生が解いている裏でAIが少しずつ問題を作り足すため、"
                 "待ち時間ゼロで常に新しい問題が混ざるようになります。",
        )
        if allow != bool(settings.get("allow_live_generation", True)):
            if st.button("設定を保存する"):
                save_settings({"allow_live_generation": allow})
                st.success("保存しました。")
                st.rerun()

    # --- 事後パトロール・承認 ---------------------------------------------
    with tab_review:
        bank = fetch_bank()
        st.subheader("問題の内容確認・事後パトロール")

        f_col1, f_col2 = st.columns(2)
        with f_col1:
            r_filter = st.selectbox(
                "表示するステータス",
                [
                    "AI自動追加（承認済み・パトロール対象）",
                    "未確認（保留中）",
                    "すべての承認済み問題",
                    "却下済み（ゴミ箱）",
                ],
            )
        with f_col2:
            r_domain = st.selectbox("領域で絞る", ["すべて"] + AVAILABLE_DOMAINS, key="rev_domain")

        if r_filter == "AI自動追加（承認済み・パトロール対象）":
            filtered = [q for q in bank if q.get("status") == "approved" and q.get("source") == "ai-live"]
        elif r_filter == "未確認（保留中）":
            filtered = [q for q in bank if q.get("status") == "pending"]
        elif r_filter == "すべての承認済み問題":
            filtered = [q for q in bank if q.get("status") == "approved"]
        else:
            filtered = [q for q in bank if q.get("status") == "rejected"]

        target = [q for q in filtered if r_domain == "すべて" or q.get("domain") == r_domain]
        # 新しいものが上に来るようにソート
        target.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        st.caption(f"該当: {len(target)} 問（新しい順に一度に20問まで表示します）")

        if not target:
            st.info("該当する問題はありません。")
        else:
            if r_filter == "未確認（保留中）":
                if st.button(f"⚡ 表示中の {min(len(target), 20)} 問をまとめて承認"):
                    for q in target[:20]:
                        bank_set_status(q["id"], "approved")
                    st.success("承認しました。")
                    st.rerun()

            st.divider()
            for q in target[:20]:
                with st.container(border=True):
                    st.markdown(
                        f"**{q.get('domain', '')} / {q.get('sub_domain', '')}** "
                        f"`{q.get('difficulty', '')}` "
                        f"<small>作成元: {q.get('source', '')} / 状態: {q.get('status', '')}</small>",
                        unsafe_allow_html=True,
                    )
                    new_q = st.text_area("問題文", value=q.get("question", ""),
                                         key=f"q_{q['id']}", height=80)
                    ans = st.radio("正解", ["○", "×"],
                                   index=int(q.get("answer_index", 0)),
                                   key=f"a_{q['id']}", horizontal=True)
                    new_e = st.text_area("解説", value=q.get("explanation", ""),
                                         key=f"e_{q['id']}", height=80)

                    b1, b2, b3 = st.columns(3)
                    with b1:
                        if st.button("💾 内容を保存（承認維持）", key=f"ok_{q['id']}"):
                            bank_update(q["id"], {
                                "question": new_q,
                                "answer_index": ["○", "×"].index(ans),
                                "explanation": new_e,
                                "qkey": normalize_text(new_q),
                                "status": "approved",
                                "source": "verified",  # パトロール済みマーク
                            })
                            st.success("保存しました。")
                            st.rerun()
                    with b2:
                        if q.get("status") != "approved":
                            if st.button("✅ 承認する", key=f"ap_{q['id']}"):
                                bank_set_status(q["id"], "approved")
                                st.rerun()
                    with b3:
                        if q.get("status") != "rejected":
                            if st.button("🗑️ 却下（出題停止）", key=f"ng_{q['id']}"):
                                bank_set_status(q["id"], "rejected")
                                st.rerun()

    # --- 成績 -------------------------------------------------------------
    with tab_scores:
        try:
            students = db.collection("students").stream()
            records = []
            for s in students:
                d = s.to_dict() or {}
                total = d.get("total_answers", 0)
                correct = d.get("correct_answers", 0)
                max_st = d.get("max_streak", 0)
                records.append({
                    "学籍番号": s.id,
                    "氏名": d.get("name", "未設定"),
                    "総解答数": total,
                    "正答数": correct,
                    "正答率(%)": round((correct / total) * 100, 1) if total > 0 else 0,
                    "現在連続正解": d.get("current_streak", 0),
                    "最大連続正解": max_st,
                    "連続正解称号": get_streak_title(max_st),
                    "解答数称号": get_total_title(total),
                })
        except Exception as e:
            st.error(f"成績データの取得に失敗しました: {type(e).__name__}: {e}")
            st.stop()

        if records:
            st.dataframe(records, use_container_width=True, hide_index=True)
        else:
            st.write("まだ演習に取り組んだ学生のデータはありません。")
