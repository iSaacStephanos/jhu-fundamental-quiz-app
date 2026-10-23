"""基礎看護技術論Ⅲ 無限問題集アプリ

【設計方針】
- 問題は Firestore の "questions" コレクション（＝問題バンク）に蓄積する
- 学生画面はバンクから「未出題の問題」をランダムに読むだけ → 待ち時間ゼロ
- 出題と同時にバックグラウンドでAIが新作問題を少量ずつ生成し、即時承認（approved / ai-live）
  としてバンクに自動追加する（事後パトロール方式）。
- 回答送信時は st.rerun() を介さず、同一サイクル内で即座に結果と解説を描画する。
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
AVAILABLE_DOMAINS = [
    "呼吸器系のフィジカルアセスメント",
    "循環器系のフィジカルアセスメント",
    "脳神経系のフィジカルアセスメント",
    "筋骨格系のフィジカルアセスメント",
    "消化器系のフィジカルアセスメント",
    "看護過程",
]

FILE_MAP = {
    "呼吸器系のフィジカルアセスメント": "kokyu.txt",
    "循環器系のフィジカルアセスメント": "junkanki.txt",
    "脳神経系のフィジカルアセスメント": "noushinkei.txt",
    "筋骨格系のフィジカルアセスメント": "kinkokkaku.txt",
    "消化器系のフィジカルアセスメント": "shokaki.txt",
    "看護過程": "kangokatei.txt",
}

# 各テキストファイルの【番号. 見出し】と出題範囲の対応
SECTION_MAP = {
    "呼吸器系のフィジカルアセスメント": {
        "解剖・生理": [1, 2],
        "体表解剖": [3],
        "問診・視診": [4, 5],
        "触診・打診": [6, 7],
        "聴診": [8],
    },
    "循環器系のフィジカルアセスメント": {
        "解剖・生理": [1],
        "刺激伝導系と心電図": [2],
        "問診・視診・触診": [3, 4],
        "聴診": [5],
        "末梢動脈・静脈": [6, 7],
        "浮腫と心不全": [8, 9],
    },
    "筋骨格系のフィジカルアセスメント": {
        "筋の基礎知識": [1],
        "骨格系と関節運動": [2],
        "ROMとMMT": [3],
        "運動の実行・調節と麻痺": [4],
        "運動失調・不随意運動とアセスメント": [5],
    },
    "看護過程": {
        "看護過程の概要と情報収集": [1],
        "ゴードンの機能的健康パターン": [2],
        "情報の分析と関連図": [3],
        "看護問題の明確化と看護診断": [4],
        "看護計画の立案": [5],
        "実施・評価と看護記録": [6],
    },
}

SUB_DOMAINS = {
    "呼吸器系のフィジカルアセスメント":
        ["すべて", "解剖・生理", "体表解剖", "問診・視診", "触診・打診", "聴診"],
    "循環器系のフィジカルアセスメント":
        ["すべて", "解剖・生理", "刺激伝導系と心電図", "問診・視診・触診",
         "聴診", "末梢動脈・静脈", "浮腫と心不全"],
    "筋骨格系のフィジカルアセスメント":
        ["すべて", "筋の基礎知識", "骨格系と関節運動", "ROMとMMT",
         "運動の実行・調節と麻痺", "運動失調・不随意運動とアセスメント"],
    "看護過程":
        ["すべて", "看護過程の概要と情報収集", "ゴードンの機能的健康パターン",
         "情報の分析と関連図", "看護問題の明確化と看護診断",
         "看護計画の立案", "実施・評価と看護記録"],
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

        # テキスト内の引用タグを安全に自動除去する
        cite_pattern = r"\[" + "cite:" + r"[^\]]+\]"
        text = re.sub(cite_pattern, "", text)

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
# 4. 初期問題（バンクへの自動取り込み用・DB障害時のフォールバック）
# ==========================================
SEED_QUESTIONS = [
    # --- 呼吸器系のフィジカルアセスメント（19問） ---
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "解剖・生理", "question": "右主気管支は左主気管支よりも細く長いため、誤嚥性肺炎は左肺に起こりやすい。", "options": ["○", "×"], "answer_index": 1, "explanation": "右主気管支の方が「太く短く」、分岐角度が小さい（約25度）ため、誤嚥したものは右に入りやすく、誤嚥性肺炎は右肺に起こりやすいです。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "解剖・生理", "question": "腹式呼吸は安静時の呼吸の大部分を担い、胸式呼吸よりも1回換気量が多くなる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。横隔膜の収縮による腹式呼吸は安静時の主な呼吸であり、胸式呼吸よりも多くの換気量を得られます。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "解剖・生理", "question": "背面から見える肺の大部分は上葉であるため、背部の観察では上葉を意識することが重要である。", "options": ["○", "×"], "answer_index": 1, "explanation": "背面から見える肺の大部分は「下葉」です。そのため、背部の聴診や打診では主に下葉の状態を観察しています。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "解剖・生理", "question": "PaO2（動脈血酸素分圧）が60Torr以下、またはSpO2が約90%の状態は呼吸不全を意味する。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。PaO2の基準値は80〜100Torrであり、60Torrを下回る（SpO2約90%以下）と呼吸不全と定義されます。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "体表解剖", "question": "胸骨角（ルイ角）は第1肋骨の付着部であり、肋骨・肋間を数える起点となる。", "options": ["○", "×"], "answer_index": 1, "explanation": "胸骨角は「第2肋骨」の付着部です。第1肋骨は鎖骨の下にあり触れないため、ここを起点として肋骨を数えます。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "体表解剖", "question": "前面の基準線は、胸骨中線・胸骨線・鎖骨中線の3つである。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。これらを基準線として、肋骨や肋間の番号と組み合わせて部位を表現します。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "体表解剖", "question": "首を前に曲げたときに最も突出するのは、第1胸椎棘突起である。", "options": ["○", "×"], "answer_index": 1, "explanation": "最も突出するのは「第7頸椎棘突起」です。そのすぐ下が第1胸椎になります。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "体表解剖", "question": "気管分岐部は、背面から見ると第4胸椎棘突起の高さに位置している。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。前面では胸骨角の高さ、背面では第4胸椎棘突起の高さが気管分岐部の目安となります。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "体表解剖", "question": "肺尖部は鎖骨内側1/3より上方約3cmの位置にある。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。肺の頂上部分は鎖骨よりも上に飛び出しているため、鎖骨上窩の聴診も重要になります。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "問診・視診", "question": "起座呼吸は、横になると苦しく座ると楽になる状態で、心不全や喘息などが原因で起こる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。心不全などで肺にうっ血がある場合、横になると静脈還流量が増えて呼吸が苦しくなるため、起き上がることで呼吸を楽にしようとします。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "問診・視診", "question": "チェーンストークス呼吸は、深く大きな規則的な呼吸が続く異常呼吸であり、糖尿病性ケトアシドーシスでみられる。", "options": ["○", "×"], "answer_index": 1, "explanation": "それは「クスマウル呼吸」の説明です。チェーンストークス呼吸は、無呼吸と深浅の周期を繰り返す呼吸で、心不全や脳障害などでみられます。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "問診・視診", "question": "チアノーゼは口唇や爪床が青紫色になるサインであるが、貧血の患者では現れにくい。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。チアノーゼは還元ヘモグロビンが一定量以上増えることで現れるため、もともとヘモグロビンが少ない貧血状態では出現しにくくなります。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "触診・打診", "question": "背部での胸郭の可動性の触診において、正常であれば深吸気時に左右の母指の間は約4cm広がる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。第10肋骨付近で左右の母指を合わせ、深吸気で対称に約4cm広がるのが正常な所見です。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "触診・打診", "question": "正常な肺野を打診すると、鈍くつまったような濁音が聴こえる。", "options": ["○", "×"], "answer_index": 1, "explanation": "正常な肺野は空気を多く含むため、響きのある低い「共鳴音（清音）」が聴こえます。濁音は肺炎や胸水貯留などで聴こえます。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "聴診", "question": "捻髪音（細かい断続性副雑音）は「ブクブク」と聴こえ、気道内に分泌物が溜まっている状態で聴取される。", "options": ["○", "×"], "answer_index": 1, "explanation": "分泌物が溜まっている状態で聴こえる「ブクブク」という音は「水泡音（粗い断続性副雑音）」です。捻髪音は「パリパリ」と聴こえ、間質性肺炎などで聴取されます。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "聴診", "question": "ストライダーは吸気時に聴こえる高調性の「ゼーゼー」という音で、上気道の狭窄を示す緊急所見である。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。喉頭浮腫や気道異物などによる上気道の狭窄を示しており、窒息の恐れがあるため迅速な対応が必要です。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "聴診", "question": "呼吸音の聴診は、低音を聴き取るためベル型聴診器を皮膚に軽く当てて行う。", "options": ["○", "×"], "answer_index": 1, "explanation": "呼吸音は高音であるため「膜型聴診器」を使用します。また、皮膚に跡が残るくらいの強さでしっかりと押し当てて聴診します。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "聴診", "question": "正常な肺胞呼吸音は、大きく高い音であり、吸気と呼気の間に休止期（ポーズ）が聴取される。", "options": ["○", "×"], "answer_index": 1, "explanation": "大きく高く、ポーズがあるのは「気管呼吸音」です。肺胞呼吸音は柔らかく低い音で、呼気は初期のみ聴こえます。"},
    {"domain": "呼吸器系のフィジカルアセスメント", "sub_domain": "聴診", "question": "COPDや気管支喘息の患者では、気道の狭窄により「呼気延長」が聴取されることがある。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。気道が狭くなる疾患（COPDや気管支喘息など）では、息を吐き出すのに時間がかかるため呼気が延長します。"},

    # --- 循環器系のフィジカルアセスメント（24問：浮腫・心不全・問診等を増量） ---
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "解剖・生理", "question": "心房と心室の間にある房室弁は、右心系が僧帽弁、左心系が三尖弁である。", "options": ["○", "×"], "answer_index": 1, "explanation": "右心系の房室弁は「三尖弁」、左心系の房室弁は「僧帽弁」です。弁は血液の逆流を防いでいます。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "解剖・生理", "question": "肺動脈には静脈血が流れ、肺静脈には動脈血が流れる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。右心室から肺へ向かう肺動脈には酸素の少ない静脈血が流れ、肺でガス交換された後の肺静脈には酸素の多い動脈血が流れます。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "解剖・生理", "question": "頸静脈（内頸・外頸静脈）は右心房に直接つながっており、右心系の状態を反映する。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。一方、頸動脈（総頸動脈）は左心系の状態を反映し、急変時の循環確認にも用いられます。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "解剖・生理", "question": "心臓の上部にある心基部は第5肋間左鎖骨中線上に位置し、心尖部は第2肋間の高さに位置する。", "options": ["○", "×"], "answer_index": 1, "explanation": "逆です。心基部は「第2肋間」の高さ、心尖部は「第5肋間左鎖骨中線上」に位置します。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "刺激伝導系と心電図", "question": "心臓のペースメーカーである洞結節は左心房に位置し、ここで電気刺激が規則正しく発生する。", "options": ["○", "×"], "answer_index": 1, "explanation": "洞結節は「右心房」にあります。洞結節→房室結節→ヒス束→左脚・右脚→プルキンエ線維→心室筋の順に電気刺激が伝わります。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "刺激伝導系と心電図", "question": "心電図の基本波形において、P波は心房の興奮（収縮）を、QRS波は心室の興奮（収縮）を意味する。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。T波は心室の興奮からの回復を意味し、1拍ごとにP→QRS→Tの順に繰り返します。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "刺激伝導系と心電図", "question": "モニター心電図は10個の電極を用いて12通りの方向から記録し、心筋梗塞の診断を主な目的とする。", "options": ["○", "×"], "answer_index": 1, "explanation": "それは「12誘導心電図」の説明です。モニター心電図は3個の電極で主に第Ⅱ誘導を長時間・連続記録し、急変の早期発見を目的とします。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "問診・視診・触診", "question": "心尖拍動は心尖部を「手掌と指腹」で触れ、振戦（スリル）は4つの弁の領域を「指のつけ根」で触れる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。触診の前には目的と方法を説明して同意を得て、手を温めておきます。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "問診・視診・触診", "question": "心尖拍動の正常な位置は胸骨中線左側10cm以内であり、範囲は2cm以内（示指先端くらい）である。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。心不全などで左心室が拡大すると、心尖拍動は左方（10cm以上）へ移動し、範囲も2cm以上に広がります。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "問診・視診・触診", "question": "仰臥位で心尖拍動が触れにくいときは、患者を右側臥位にすると触れやすくなる。", "options": ["○", "×"], "answer_index": 1, "explanation": "仰臥位で触れにくいときは「左側臥位」にします。心臓が胸壁に近づくため拍動を触れやすくなります。"},
    {"domain": "循環器系のフィジカルアセスメント", "sub_domain": "問診・視診・触診", "question": "胸痛の
