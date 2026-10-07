import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os
import re
import time
import random
import threading

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

# --- Firebase ---------------------------------------------------------------
# 接続できない場合もクイズ自体は動かす（記録なしモード）ことで、
# ローカルでの動作確認やFirestore障害時にアプリ全体が止まるのを防ぐ。
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

# ==========================================
# 2. 知識データベース（即時出題バンク）
# ==========================================
PRE_MADE_QUESTIONS = [
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
    {"sub_domain": "聴診", "question": "COPDや気管支喘息の患者では、気道の狭窄により「呼気延長」が聴取されることがある。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。気道が狭くなる疾患（COPDや気管支喘息など）では、息を吐き出すのに時間がかかるため呼気が延長します。"}
]

FILE_MAP = {
    "呼吸器系のフィジカルアセスメント": "kokyu.txt",
    "循環器系のフィジカルアセスメント": "junkanki.txt",
}

# kokyu.txt の【見出し】番号と、UI上の「出題範囲」の対応表。
# 出題範囲が選ばれている場合、その範囲の本文だけをAIに渡すことで
# 「資料全体から3問」よりも重複しにくく、範囲に忠実な問題になる。
SECTION_MAP = {
    "呼吸器系のフィジカルアセスメント": {
        "解剖・生理": [1, 2],
        "体表解剖": [3],
        "問診・視診": [4, 5],
        "触診・打診": [6, 7],
        "聴診": [8],
    }
}

AVAILABLE_DOMAINS = [
    "呼吸器系のフィジカルアセスメント",
    "循環器系のフィジカルアセスメント",
    "脳神経系のフィジカルアセスメント"
]

_KB_CACHE = {}


def load_knowledge_base(domain):
    """資料を読み込む。

    ★修正点: 以前は @st.cache_data を付けていたため、何らかの理由で
    読み込みに失敗した場合の「準備中」までキャッシュされ、
    ファイルを置いても復帰しなくなる状態があった。
    ここでは「成功した内容だけ」を更新時刻付きでキャッシュする。
    """
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
# 3-A. AI問題生成（★Streamlit に一切依存しない純粋な処理）
# ==========================================
# ここから下の関数は、バックグラウンドスレッドから呼ばれる。
# スレッド内で st.* に触ると ScriptRunContext が無いため警告・不具合になるので、
# このブロックでは st を一切使わないこと。
MODEL_PREFERENCE = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
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

# モデル名の解決結果はプロセス全体で共有する（session_state を使わない）
_MODEL_LOCK = threading.Lock()
RESOLVED_MODEL = {"name": ""}


def _resolve_model(client):
    """実際に使えるモデル名を1つ決める。優先リスト → 一覧から flash を探す。"""
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
    """プロンプトを投げて生のテキスト（JSON文字列）を返す。

    google-generativeai（旧SDK）は非推奨になり google-genai に統合されたため、
    新SDKの2つの呼び出し形 → 旧SDK の順に試す。
    すべて失敗した場合は、理由をまとめて例外として投げる。
    """
    errors = []

    # (1) 新SDK: google-genai
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        model = _resolve_model(client)

        # (1-a) Interactions API（現行の推奨形）
        try:
            interaction = client.interactions.create(
                model=model,
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": QUIZ_SCHEMA,
                },
            )
            text = getattr(interaction, "output_text", None)
            if text:
                return text
            errors.append("interactions: output_text が空")
        except Exception as e:
            errors.append(f"interactions: {type(e).__name__}: {e}")

        # (1-b) generate_content
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": QUIZ_SCHEMA,
                    "temperature": 1.1,
                },
            )
            text = getattr(resp, "text", None)
            if text:
                return text
            errors.append("generate_content: text が空（安全フィルタの可能性）")
        except Exception as e:
            errors.append(f"generate_content: {type(e).__name__}: {e}")

    except Exception as e:
        errors.append(f"google-genai 利用不可: {type(e).__name__}: {e}")

    # (2) 旧SDK: google-generativeai（入っていれば）
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
                        "temperature": 1.1,
                    },
                )
                resp = model.generate_content(prompt)
                if getattr(resp, "text", None):
                    with _MODEL_LOCK:
                        RESOLVED_MODEL["name"] = name + "（旧SDK）"
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
    """AIの応答（文字列/辞書/配列）を、出題できる形だけに整えて返す。

    ★修正点: 以前は AI の返り値をそのまま使っていたため、
    answer_index が文字列 "0" だと正誤判定が常に不正解になり、
    キー欠落では画面が KeyError で落ちる余地があった。
    """
    data = raw

    # 文字列ならJSONとして読む。```json ... ``` で囲まれている場合も救う。
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

    # {"questions": [...]} / 単一オブジェクト / 配列 のいずれも受ける。
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
            "options": ["○", "×"],   # ○× 以外の選択肢が返っても固定する
            "answer_index": idx,
            "explanation": explanation or "（解説なし）",
        })
    return cleaned


def drop_already_asked(items, asked_keys):
    """出題済み・セット内重複を落とす。"""
    out = []
    seen = set(asked_keys)
    for item in items:
        key = normalize_text(item["question"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_prompt(domain, sub_domain, difficulty, source_text, avoid_questions, n=3):
    avoid_block = ""
    if avoid_questions:
        recent = avoid_questions[-25:]
        listed = "\n".join(f"- {q}" for q in recent)
        avoid_block = (
            "\n【すでに出題した問題（内容が重なるものは絶対に避け、"
            "別の文・別の着眼点で作ること）】\n" + listed
        )

    return f"""あなたは優秀な看護教育の専門家です。以下の【参考資料】に記載されている事実のみに基づいて、○×問題（正誤問題）を「{n}問」作成してください。
外部の知識は絶対に混ぜないでください。

【条件】
- 出題領域: {domain}
- 出題範囲: {sub_domain}
- 難易度: {difficulty}
- 選択肢は「○」と「×」の2つで固定です。answer_index は 0（○が正しい）または 1（×が正しい）の整数で答えてください。
- {n}問のうち、少なくとも1問は answer_index を 1（誤りの文）にしてください。
- 解説は要点を絞って2〜3文で簡潔に。
- 資料の文章量が少ない場合は、同じ記述でも着眼点（数値・部位・対比・順序・適応など）を変えて、必ず{n}問作成してください。
- すでに出題した問題と文意が重なるものは作らないでください。
{avoid_block}

【参考資料】
{source_text}
"""


def generate_quiz_core(api_key, domain, sub_domain, difficulty,
                       source_text, avoid_keys, avoid_questions,
                       n=3, attempts=3):
    """問題セットを作る純粋な処理。成功なら (問題リスト, "")、失敗なら ([], 理由)。

    ★この関数は st.* を使わないため、バックグラウンドスレッドから安全に呼べる。
    """
    if source_text == "準備中":
        return [], "資料ファイルが見つかりません。"
    if not api_key:
        return [], "GEMINI_API_KEY が設定されていません（Streamlit の Secrets を確認してください）。"

    prompt = build_prompt(domain, sub_domain, difficulty, source_text, avoid_questions, n=n)

    last_error = ""
    for i in range(attempts):
        try:
            raw = _call_gemini(api_key, prompt)
            items = drop_already_asked(coerce_quiz_items(raw), avoid_keys)
            if items:
                return items, ""
            last_error = "AIの応答から有効な問題を取り出せませんでした（重複のみ、または形式不正）。"
        except Exception as e:
            last_error = str(e)
        if i < attempts - 1:
            time.sleep(1.2 * (i + 1))   # 混雑時に少し待って再試行
    return [], last_error


# ==========================================
# 3-B. セッション管理・バックグラウンド先読み
# ==========================================
DEFAULTS = {
    "current_question": None,
    "answered": False,
    "student_id": "",
    "is_correct": False,
    "question_pool": [],
    "used_pre_made": [],
    "asked_questions": [],   # 重複出題を避けるための出題履歴（AI生成分も含む）
    "last_settings": {},
    "q_key": 0,
    "last_ai_error": "",     # 失敗理由を画面に出せるように保持
    "pending_next": False,
}
for _k, _v in DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# 先読みの目標在庫。ストックがこれ以下になったら裏で作り足す。
PREFETCH_LOW = 3
# 1回の裏生成で作る問題数
PREFETCH_BATCH = 3
# ストックの上限（これ以上は作らない＝APIの無駄打ちを防ぐ）
PREFETCH_MAX = 9


def get_prefetch_box():
    """バックグラウンドスレッドとメインスレッドが共有する箱。

    ★重要: スレッドは st.session_state に触れない（ScriptRunContext が無いため）。
    代わりに、ここで作った「ただの dict」への参照をスレッドに渡し、
    スレッドはその dict だけを書き換える。dict は session_state が
    保持しているので、次の再実行でメインスレッドから中身を回収できる。
    """
    if "prefetch" not in st.session_state:
        st.session_state.prefetch = {
            "lock": threading.Lock(),
            "items": [],      # 出来上がった問題の受け渡し場所
            "error": "",      # 失敗理由
            "running": False,  # 生成中フラグ（二重起動の防止）
            "epoch": 0,       # 設定世代。変わったら古い結果は捨てる
            "started_at": 0.0,
        }
    return st.session_state.prefetch


def _prefetch_worker(box, epoch, api_key, domain, sub_domain, difficulty,
                     source_text, avoid_keys, avoid_questions):
    """バックグラウンドで問題を作り、box に入れるだけのワーカー。

    この関数の中では Streamlit の API を一切呼ばないこと。
    """
    try:
        items, error = generate_quiz_core(
            api_key, domain, sub_domain, difficulty,
            source_text, avoid_keys, avoid_questions,
            n=PREFETCH_BATCH,
        )
    except Exception as e:
        items, error = [], f"{type(e).__name__}: {e}"

    with box["lock"]:
        # 出題範囲などが切り替わっていたら、この結果はもう使わない
        if box["epoch"] == epoch:
            box["items"].extend(items)
            box["error"] = error
        box["running"] = False


def harvest_prefetch():
    """裏で出来上がった問題をストックへ回収する（メインスレッドで毎回呼ぶ）。"""
    box = get_prefetch_box()
    with box["lock"]:
        got = box["items"]
        box["items"] = []
        error = box["error"]
        box["error"] = ""

    if got:
        # 出題済み・ストック内と重複しないものだけ採用する
        seen = {normalize_text(q) for q in st.session_state.asked_questions}
        seen |= {normalize_text(q["question"]) for q in st.session_state.question_pool}
        for item in got:
            key = normalize_text(item["question"])
            if key in seen:
                continue
            seen.add(key)
            st.session_state.question_pool.append(item)

    if error and not got:
        st.session_state.last_ai_error = error
    elif got:
        st.session_state.last_ai_error = ""


def is_prefetching():
    box = get_prefetch_box()
    with box["lock"]:
        return box["running"]


def maybe_start_prefetch(domain, sub_domain, difficulty):
    """ストックが少なければ、裏で問題の作成を始める（待たずにすぐ返る）。

    学生が今の問題を読んで回答している数十秒の間に生成が終わるため、
    次の問題へ進むときにローディングが出なくなる。
    """
    if not GEMINI_API_KEY:
        return
    source_text = build_source_text(domain, sub_domain)
    if source_text == "準備中":
        return

    box = get_prefetch_box()
    with box["lock"]:
        if box["running"]:
            return
        if len(st.session_state.question_pool) + len(box["items"]) > PREFETCH_LOW:
            return
        if len(st.session_state.question_pool) >= PREFETCH_MAX:
            return
        box["running"] = True
        box["started_at"] = time.time()
        epoch = box["epoch"]

    avoid_questions = list(st.session_state.asked_questions)
    avoid_keys = [normalize_text(q) for q in avoid_questions]
    avoid_keys += [normalize_text(q["question"]) for q in st.session_state.question_pool]

    thread = threading.Thread(
        target=_prefetch_worker,
        args=(box, epoch, GEMINI_API_KEY, domain, sub_domain, difficulty,
              source_text, avoid_keys, avoid_questions),
        daemon=True,
    )
    thread.start()


def wait_for_prefetch(timeout=30):
    """ストックが切れていて裏生成が走っている場合だけ、完了を待つ。"""
    box = get_prefetch_box()
    with box["lock"]:
        if not box["running"]:
            return
    with st.spinner("AIが問題を作成中です...（まもなく表示されます）"):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with box["lock"]:
                done = (not box["running"]) or bool(box["items"])
            if done:
                break
            time.sleep(0.25)
    harvest_prefetch()


def reset_for_new_settings(current_settings):
    """出題範囲などが変わったら、ストックと履歴をリセットする。"""
    st.session_state.question_pool = []
    st.session_state.used_pre_made = []
    st.session_state.asked_questions = []
    st.session_state.last_settings = current_settings
    box = get_prefetch_box()
    with box["lock"]:
        box["epoch"] += 1     # 走っている生成の結果は破棄される
        box["items"] = []
        box["error"] = ""


def _set_question(q):
    st.session_state.current_question = q
    st.session_state.answered = False
    st.session_state.is_correct = False
    st.session_state.q_key += 1
    st.session_state.asked_questions.append(q["question"])


def advance_to_next_question(domain, sub_domain, difficulty):
    """次の問題を決めて session_state に入れる。

    ★修正点: 以前は先に current_question = None にしてから生成していたため、
    生成に失敗すると画面が空になり、そこから復帰できなかった。
    ここでは「新しい問題が用意できたときだけ」画面を差し替える。
    """
    # (1) 事前作成問題が残っていればそれを使う（待ち時間ゼロ）
    available_pre_made = [
        q for q in PRE_MADE_QUESTIONS
        if (sub_domain == "すべて" or q.get("sub_domain") == sub_domain)
        and q["question"] not in st.session_state.used_pre_made
    ]
    if available_pre_made:
        selected = random.choice(available_pre_made)
        st.session_state.used_pre_made.append(selected["question"])
        _set_question({
            "question": selected["question"],
            "options": selected["options"],
            "answer_index": selected["answer_index"],
            "explanation": selected["explanation"],
        })
        return True, ""

    # (2) 裏で出来ているものを回収
    harvest_prefetch()

    # (3) ストックが空のときだけ待つ（通常はここに来ない）
    if not st.session_state.question_pool:
        if is_prefetching():
            wait_for_prefetch()
        else:
            # 裏生成が走っていない＝起動直後や前回失敗時。ここは同期で作る。
            source_text = build_source_text(domain, sub_domain)
            with st.spinner("AIが新しい問題セットを作成中..."):
                avoid_questions = list(st.session_state.asked_questions)
                items, error = generate_quiz_core(
                    GEMINI_API_KEY, domain, sub_domain, difficulty, source_text,
                    [normalize_text(q) for q in avoid_questions], avoid_questions,
                    n=PREFETCH_BATCH,
                )
            if items:
                st.session_state.question_pool.extend(items)
                st.session_state.last_ai_error = ""
            else:
                st.session_state.last_ai_error = error

    # (4) ストックから出題。空なら今の問題を残したままエラーだけ返す。
    if st.session_state.question_pool:
        _set_question(st.session_state.question_pool.pop(0))
        return True, ""
    return False, st.session_state.last_ai_error


# ==========================================
# 4. UI（画面構成）
# ==========================================
mode = st.sidebar.radio("モード選択", ["学生用（クイズ演習）", "管理者用（モニタリング）"])

if RESOLVED_MODEL["name"]:
    st.sidebar.caption(f"使用モデル: {RESOLVED_MODEL['name']}")
if not DB_AVAILABLE:
    st.sidebar.warning("成績の保存先（Firebase）に接続できていません。")

if mode == "学生用（クイズ演習）":
    st.title("🩺 基礎看護技術論Ⅲ 無限問題集")

    if not DB_AVAILABLE:
        st.warning(
            "⚠️ 成績記録用のデータベースに接続できないため、**記録なしモード**で動作しています。"
            "問題演習は可能ですが、連続正解数は保存されません。"
        )
        with st.expander("詳細（担当者向け）"):
            st.code(DB_ERROR or "不明", language="text")

    col_id, col_name = st.columns([1, 1])
    with col_id:
        student_id = st.text_input("学籍番号を入力してください（半角数字7桁）", value=st.session_state.student_id)
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
    user_data = {"name": student_name, "current_streak": 0, "max_streak": 0,
                 "has_title": False, "total_answers": 0, "correct_answers": 0}
    if DB_AVAILABLE:
        try:
            user_ref = db.collection("students").document(student_id)
            user_doc = user_ref.get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
                # 氏名が後から入力・変更された場合も反映する
                if student_name and user_data.get("name") != student_name:
                    user_data["name"] = student_name
                    user_ref.set({"name": student_name}, merge=True)
            else:
                user_ref.set(user_data)
        except Exception as e:
            st.warning(f"成績データの読み込みに失敗しました（演習は継続できます）: {type(e).__name__}")
            user_ref = None

    st.divider()
    if user_data.get("has_title", False):
        st.success("🎖️ **称号保持者：【フィジカルアセスメント・マスター】**（連続30問正解達成！）")
    st.metric(label="現在の連続正解数", value=f"{user_data.get('current_streak', 0)} 問", delta="目標: 30問連続")

    c1, c2, c3, c4 = st.columns([3, 3, 3, 3])
    with c1:
        domain = st.selectbox("学習する領域", AVAILABLE_DOMAINS)
    with c2:
        if domain == "呼吸器系のフィジカルアセスメント":
            sub_domain = st.selectbox("出題範囲", ["すべて", "解剖・生理", "体表解剖", "問診・視診", "触診・打診", "聴診"])
        elif domain == "循環器系のフィジカルアセスメント":
            sub_domain = st.selectbox("出題範囲", ["すべて", "解剖・生理", "刺激伝導系と心電図", "問診・視診・触診", "聴診", "末梢動脈・静脈", "浮腫と心不全"])
        else:
            sub_domain = st.selectbox("出題範囲", ["すべて"])
    with c3:
        difficulty = st.selectbox("難易度", ["標準（基本知識）", "応用（病態との関連・臨床的判断）"])
    with c4:
        st.write("")
        st.write("")
        next_clicked = st.button("次の問題を生成する 🎲", use_container_width=True)

    # --- ここが先読みの中枢 -------------------------------------------------
    # 設定が変わっていたらリセット、そのうえで毎回「回収 → 必要なら裏で開始」。
    # 画面が描かれるたびに呼ばれるので、学生が問題を読んでいる間も
    # 裏で次のセットが作られていく。
    current_settings = {"domain": domain, "sub_domain": sub_domain, "difficulty": difficulty}
    if st.session_state.last_settings != current_settings:
        reset_for_new_settings(current_settings)
    harvest_prefetch()
    maybe_start_prefetch(domain, sub_domain, difficulty)
    # ----------------------------------------------------------------------

    # 解説の下の「次の問題へ」ボタンからも同じ処理を呼べるようにする
    if st.session_state.pending_next:
        st.session_state.pending_next = False
        next_clicked = True

    if next_clicked:
        if build_source_text(domain, sub_domain) == "準備中":
            st.error("この領域の問題は現在準備中です。別の領域を選択してください。")
        else:
            ok, err = advance_to_next_question(domain, sub_domain, difficulty)
            # 出題した直後にも、次のストックを裏で作り始める
            maybe_start_prefetch(domain, sub_domain, difficulty)
            if not ok:
                st.error("⚠️ 新しい問題を作成できませんでした。もう一度お試しください。")
                with st.expander("詳細（担当者向け）"):
                    st.code(err or "原因不明", language="text")
                    st.caption(
                        "モデル名の廃止やAPIキー未設定が原因の場合、ここに具体的なエラーが表示されます。"
                    )

    if st.session_state.current_question:
        q = st.session_state.current_question
        st.markdown(f"### Q. {q['question']}")

        is_disabled = st.session_state.answered
        st.markdown("### 選択肢を選んでください：")
        choice = st.radio("選択肢", q["options"], key=f"quiz_choice_{st.session_state.q_key}",
                          index=None, disabled=is_disabled, horizontal=True, label_visibility="collapsed")

        if not is_disabled:
            if st.button("回答を送信する"):
                if choice is None:
                    # ★修正点: 未選択のまま送信すると以前は無反応だった
                    st.warning("「○」または「×」を選んでから送信してください。")
                else:
                    st.session_state.answered = True
                    selected_idx = q["options"].index(choice)
                    is_correct = (selected_idx == int(q["answer_index"]))
                    st.session_state.is_correct = is_correct

                    user_data["total_answers"] = user_data.get("total_answers", 0) + 1
                    if is_correct:
                        user_data["correct_answers"] = user_data.get("correct_answers", 0) + 1
                        user_data["current_streak"] = user_data.get("current_streak", 0) + 1
                        if user_data["current_streak"] > user_data.get("max_streak", 0):
                            user_data["max_streak"] = user_data["current_streak"]
                        if user_data["current_streak"] >= 30 and not user_data.get("has_title", False):
                            user_data["has_title"] = True
                            st.balloons()
                    else:
                        user_data["current_streak"] = 0

                    if user_ref is not None:
                        try:
                            user_ref.set(user_data, merge=True)
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

            # ストック状況の表示（裏で作成中かどうかも分かるようにする）
            pool_count = len(st.session_state.question_pool)
            pre_made_left = len([
                q2 for q2 in PRE_MADE_QUESTIONS
                if (sub_domain == "すべて" or q2.get("sub_domain") == sub_domain)
                and q2["question"] not in st.session_state.used_pre_made
            ])
            stock = pool_count + pre_made_left
            if stock > 0:
                note = f"ストック {stock} 問：待ち時間ゼロで出題されます。"
            else:
                note = "次の問題を準備しています。"
            if is_prefetching():
                note += " 🔄 裏で次のセットを作成中…"
            st.caption(note)

elif mode == "管理者用（モニタリング）":
    st.title("📊 学生モニタリングダッシュボード")

    # ★修正点: パスワードはソースに直書きせず Secrets から読む
    admin_password = get_secret("ADMIN_PASSWORD", "admin1234")
    admin_pass = st.text_input("管理者パスワードを入力してください", type="password")
    if admin_pass != admin_password:
        st.info("正しいパスワードを入力すると成績データが表示されます。")
        if admin_password == "admin1234":
            st.caption(
                "※ 現在は初期パスワードのままです。Streamlit の Secrets に "
                "ADMIN_PASSWORD を設定して変更してください。"
            )
        st.stop()

    if not DB_AVAILABLE:
        st.error("データベースに接続できないため、成績データを表示できません。")
        st.code(DB_ERROR or "不明", language="text")
        st.stop()

    try:
        students = db.collection("students").stream()
        records = []
        for s in students:
            d = s.to_dict()
            total = d.get("total_answers", 0)
            correct = d.get("correct_answers", 0)
            records.append({
                "学籍番号": s.id,
                "氏名": d.get("name", "未設定"),
                "総解答数": total,
                "正答数": correct,
                "正答率(%)": round((correct / total) * 100, 1) if total > 0 else 0,
                "最大連続正解数": d.get("max_streak", 0),
                "称号獲得": "達成 🏆" if d.get("has_title", False) else "未達成"
            })
    except Exception as e:
        st.error(f"成績データの取得に失敗しました: {type(e).__name__}: {e}")
        st.stop()

    if records:
        st.dataframe(records, use_container_width=True)
    else:
        st.write("まだ演習に取り組んだ学生のデータはありません。")
