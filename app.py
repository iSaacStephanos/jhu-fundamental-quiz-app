"""基礎看護技術論Ⅲ 無限問題集アプリ"""

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

st.set_page_config(
    page_title="基礎看護技術論Ⅲ 無限問題集",
    layout="wide",
)

st.markdown(
    """
<style>
div[role="radiogroup"] p {
    font-size: 1.5rem !important;
    font-weight: bold !important;
}
</style>
""",
    unsafe_allow_html=True,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))


# ==========================================
# 1. 初期設定（APIキー・Firebase接続）
# ==========================================
def get_secret(name, default=""):
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
            raise RuntimeError("Firebase鍵が見つかりません")
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

FILE_KEYWORDS = {
    "呼吸器系のフィジカルアセスメント": ["kokyu", "呼吸"],
    "循環器系のフィジカルアセスメント": ["junkanki", "循環"],
    "脳神経系のフィジカルアセスメント": ["noushinkei", "脳神経", "神経"],
    "筋骨格系のフィジカルアセスメント": ["kinkokkaku", "筋骨格", "筋・骨格"],
    "消化器系のフィジカルアセスメント": ["shokaki", "消化"],
    "看護過程": ["kangokatei", "看護過程"],
}

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
    "呼吸器系のフィジカルアセスメント": [
        "すべて",
        "解剖・生理",
        "体表解剖",
        "問診・視診",
        "触診・打診",
        "聴診",
    ],
    "循環器系のフィジカルアセスメント": [
        "すべて",
        "解剖・生理",
        "刺激伝導系と心電図",
        "問診・視診・触診",
        "聴診",
        "末梢動脈・静脈",
        "浮腫と心不全",
    ],
    "筋骨格系のフィジカルアセスメント": [
        "すべて",
        "筋の基礎知識",
        "骨格系と関節運動",
        "ROMとMMT",
        "運動の実行・調節と麻痺",
        "運動失調・不随意運動とアセスメント",
    ],
    "看護過程": [
        "すべて",
        "看護過程の概要と情報収集",
        "ゴードンの機能的健康パターン",
        "情報の分析と関連図",
        "看護問題の明確化と看護診断",
        "看護計画の立案",
        "実施・評価と看護記録",
    ],
}

DIFFICULTIES = [
    "標準（基本知識）",
    "応用（病態との関連・臨床的判断）",
]

_KB_CACHE = {}


def _find_domain_file(domain):
    file_name = FILE_MAP.get(domain)
    if file_name:
        exact = os.path.join(APP_DIR, file_name)
        if os.path.exists(exact):
            return exact
    keywords = FILE_KEYWORDS.get(domain, [])
    try:
        for fname in os.listdir(APP_DIR):
            if not fname.lower().endswith(".txt"):
                continue
            for kw in keywords:
                if kw.lower() in fname.lower():
                    return os.path.join(APP_DIR, fname)
    except Exception:
        pass
    return None


def load_knowledge_base(domain):
    path = _find_domain_file(domain)
    if not path or not os.path.exists(path):
        return "準備中"
    try:
        mtime = os.path.getmtime(path)
        cached = _KB_CACHE.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

        cite_pat = r"\[" + "cite:" + r"[^\]]+\]"
        text = re.sub(cite_pat, "", text)

        _KB_CACHE[path] = (mtime, text)
        return text
    except Exception:
        return "準備中"


def extract_sections(text, numbers):
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
# 4. 初期問題データ（改行されても壊れないテキスト形式）
# ==========================================
RAW_SEEDS = """
===
呼吸器系のフィジカルアセスメント | 解剖・生理 | 1
Q: 右主気管支は左主気管支よりも細く長いため、誤嚥性肺炎は左肺に起こりやすい。
E: 右主気管支の方が「太く短く」、分岐角度が小さい（約25度）ため、誤嚥性肺炎は右肺に起こりやすいです。
===
呼吸器系のフィジカルアセスメント | 解剖・生理 | 0
Q: 腹式呼吸は安静時の呼吸の大部分を担い、胸式呼吸よりも1回換気量が多くなる。
E: その通りです。横隔膜の収縮による腹式呼吸は安静時の主な呼吸であり、多くの換気量を得られます。
===
呼吸器系のフィジカルアセスメント | 解剖・生理 | 1
Q: 背面から見える肺の大部分は上葉であるため、背部の観察では上葉を意識することが重要である。
E: 背面から見える肺の大部分は「下葉」です。背部の聴診や打診では主に下葉の状態を観察しています。
===
呼吸器系のフィジカルアセスメント | 解剖・生理 | 0
Q: PaO2（動脈血酸素分圧）が60Torr以下、またはSpO2が約90%の状態は呼吸不全を意味する。
E: その通りです。PaO2の基準値は80〜100Torrであり、60Torr以下は呼吸不全と定義されます。
===
呼吸器系のフィジカルアセスメント | 体表解剖 | 1
Q: 胸骨角（ルイ角）は第1肋骨の付着部であり、肋骨・肋間を数える起点となる。
E: 胸骨角は「第2肋骨」の付着部です。第1肋骨は鎖骨の下にあり触れないため、ここを起点に数えます。
===
呼吸器系のフィジカルアセスメント | 体表解剖 | 0
Q: 前面の基準線は、胸骨中線・胸骨線・鎖骨中線の3つである。
E: その通りです。これらを基準線として、肋骨や肋間の番号と組み合わせて部位を表現します。
===
呼吸器系のフィジカルアセスメント | 体表解剖 | 1
Q: 首を前に曲げたときに最も突出するのは、第1胸椎棘突起である。
E: 最も突出するのは「第7頸椎棘突起」です。そのすぐ下が第1胸椎になります。
===
呼吸器系のフィジカルアセスメント | 体表解剖 | 0
Q: 気管分岐部は、背面から見ると第4胸椎棘突起の高さに位置している。
E: その通りです。前面では胸骨角の高さ、背面では第4胸椎棘突起の高さが目安となります。
===
呼吸器系のフィジカルアセスメント | 体表解剖 | 0
Q: 肺尖部は鎖骨内側1/3より上方約3cmの位置にある。
E: その通りです。肺の頂上部分は鎖骨よりも上に飛び出しているため、鎖骨上窩の聴診も重要です。
===
呼吸器系のフィジカルアセスメント | 問診・視診 | 0
Q: 起座呼吸は、横になると苦しく座ると楽になる状態で、心不全や喘息などが原因で起こる。
E: その通りです。横になると静脈還流量が増えて肺うっ血が悪化するため、起き上がると楽になります。
===
呼吸器系のフィジカルアセスメント | 問診・視診 | 1
Q: チェーンストークス呼吸は、深く大きな規則的な呼吸が続く異常呼吸であり、糖尿病性ケトアシドーシスでみられる。
E: それは「クスマウル呼吸」の説明です。チェーンストークス呼吸は無呼吸と深浅の周期を繰り返す呼吸です。
===
呼吸器系のフィジカルアセスメント | 問診・視診 | 0
Q: チアノーゼは口唇や爪床が青紫色になるサインであるが、貧血の患者では現れにくい。
E: その通りです。還元ヘモグロビンが増えることで現れるため、貧血状態では出現しにくくなります。
===
呼吸器系のフィジカルアセスメント | 触診・打診 | 0
Q: 背部での胸郭の可動性の触診において、正常であれば深吸気時に左右の母指の間は約4cm広がる。
E: その通りです。第10肋骨付近で左右の母指を合わせ、深吸気で対称に約4cm広がるのが正常です。
===
呼吸器系のフィジカルアセスメント | 触診・打診 | 1
Q: 正常な肺野を打診すると、鈍くつまったような濁音が聴こえる。
E: 正常な肺野は空気を多く含むため、響きのある低い「共鳴音（清音）」が聴こえます。
===
呼吸器系のフィジカルアセスメント | 聴診 | 1
Q: 捻髪音（細かい断続性副雑音）は「ブクブク」と聴こえ、気道内に分泌物が溜まっている状態で聴取される。
E: 「ブクブク」は「水泡音（粗い断続性副雑音）」です。捻髪音は「パリパリ」と聴こえます。
===
呼吸器系のフィジカルアセスメント | 聴診 | 0
Q: ストライダーは吸気時に聴こえる高調性の「ゼーゼー」という音で、上気道の狭窄を示す緊急所見である。
E: その通りです。喉頭浮腫や気道異物などによる上気道狭窄を示し、迅速な対応が必要です。
===
呼吸器系のフィジカルアセスメント | 聴診 | 1
Q: 呼吸音の聴診は、低音を聴き取るためベル型聴診器を皮膚に軽く当てて行う。
E: 呼吸音は高音であるため「膜型聴診器」を使用し、跡が残るくらいの強さで押し当てます。
===
呼吸器系のフィジカルアセスメント | 聴診 | 1
Q: 正常な肺胞呼吸音は、大きく高い音であり、吸気と呼気の間に休止期（ポーズ）が聴取される。
E: 大きく高くポーズがあるのは「気管呼吸音」です。肺胞呼吸音は柔らかく低い音です。
===
呼吸器系のフィジカルアセスメント | 聴診 | 0
Q: COPDや気管支喘息の患者では、気道の狭窄により「呼気延長」が聴取されることがある。
E: その通りです。気道が狭くなる疾患では、息を吐き出すのに時間がかかるため呼気が延長します。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 1
Q: 心房と心室の間にある房室弁は、右心系が僧帽弁、左心系が三尖弁である。
E: 右心系の房室弁は「三尖弁」、左心系の房室弁は「僧帽弁」です。弁は逆流を防ぎます。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 0
Q: 肺動脈には静脈血が流れ、肺静脈には動脈血が流れる。
E: その通りです。肺へ向かう肺動脈には静脈血、ガス交換後の肺静脈には動脈血が流れます。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 0
Q: 頸静脈（内頸・外頸静脈）は右心房に直接つながっており、右心系の状態を反映する。
E: その通りです。一方、頸動脈（総頸動脈）は左心系の状態を反映します。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 1
Q: 心臓の上部にある心基部は第5肋間左鎖骨中線上に位置し、心尖部は第2肋間の高さに位置する。
E: 逆です。心基部は「第2肋間」の高さ、心尖部は「第5肋間左鎖骨中線上」に位置します。
===
循環器系のフィジカルアセスメント | 刺激伝導系と心電図 | 1
Q: 心臓のペースメーカーである洞結節は左心房に位置し、ここで電気刺激が規則正しく発生する。
E: 洞結節は「右心房」にあります。洞結節→房室結節→ヒス束→脚→プルキンエ線維の順に伝わります。
===
循環器系のフィジカルアセスメント | 刺激伝導系と心電図 | 0
Q: 心電図の基本波形において、P波は心房の興奮（収縮）を、QRS波は心室の興奮（収縮）を意味する。
E: その通りです。T波は心室の興奮からの回復を意味し、P→QRS→Tの順に繰り返します。
===
循環器系のフィジカルアセスメント | 刺激伝導系と心電図 | 1
Q: モニター心電図は10個の電極を用いて12通りの方向から記録し、心筋梗塞の診断を主な目的とする。
E: それは「12誘導心電図」です。モニター心電図は3個の電極で主に第Ⅱ誘導を連続記録します。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 0
Q: 心尖拍動は心尖部を「手掌と指腹」で触れ、振戦（スリル）は4つの弁の領域を「指のつけ根」で触れる。
E: その通りです。触診の前には目的と方法を説明して同意を得て、手を温めておきます。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 0
Q: 心尖拍動の正常な位置は胸骨中線左側10cm以内であり、範囲は2cm以内（示指先端くらい）である。
E: その通りです。左心室が拡大すると、心尖拍動は左方（10cm以上）へ移動し範囲も広がります。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 1
Q: 仰臥位で心尖拍動が触れにくいときは、患者を右側臥位にすると触れやすくなる。
E: 仰臥位で触れにくいときは「左側臥位」にします。心臓が胸壁に近づくため触れやすくなります。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 0
Q: 胸痛の問診では、部位・範囲、痛みの性質、持続時間、労作との関係、放散痛の有無を確認する。
E: その通りです。「締めつけられるような」等の患者さんの表現を引き出しながら具体的に確認します。
===
循環器系のフィジカルアセスメント | 聴診 | 0
Q: 心音のⅠ音は僧帽弁・三尖弁（房室弁）が閉じる音であり、収縮期の始まりを示す。
E: その通りです。Ⅱ音は大動脈弁・肺動脈弁が閉じる音で、拡張期の始まりを示します。
===
循環器系のフィジカルアセスメント | 聴診 | 1
Q: 心音の聴診部位において、僧帽弁領域（心尖部）は第2肋間胸骨右縁である。
E: 第2肋間胸骨右縁は「大動脈弁領域」です。僧帽弁領域（心尖部）は「第5肋間左鎖骨中線」です。
===
循環器系のフィジカルアセスメント | 聴診 | 0
Q: 心雑音の大きさを6段階で表すLevine（レバイン）分類では、Ⅳ度以上で振戦（スリル）を伴う。
E: その通りです。振戦とは強い心雑音による胸壁の振動であり、LevineⅣ度以上で触知されます。
===
循環器系のフィジカルアセスメント | 聴診 | 1
Q: 心音の聴き分けにおいて、心基部ではⅠ音が大きく聴こえ、心尖部ではⅡ音が大きく聴こえる。
E: 逆です。心尖部では「Ⅰ音」が大きく、心基部では「Ⅱ音」が大きく聴こえます。
===
循環器系のフィジカルアセスメント | 末梢動脈・静脈 | 1
Q: 中心性チアノーゼは四肢末端のみに出現し、酸素投与では改善しない。
E: 中心性チアノーゼは全身や口腔粘膜にも出現し、酸素投与で改善することがあります。
===
循環器系のフィジカルアセスメント | 末梢動脈・静脈 | 0
Q: 爪床圧迫テスト（CRT）は、爪床を圧迫して解除した後、赤みが2秒以内に戻れば正常である。
E: その通りです。2秒以上かかる場合はショックや脱水などの末梢循環不全を疑います。
===
循環器系のフィジカルアセスメント | 末梢動脈・静脈 | 0
Q: Homans（ホーマンズ）徴候は深部静脈血栓症（DVT）の検査で、足関節を背屈させて腓腹部に疼痛が出れば陽性となる。
E: その通りです。ただし陰性でもDVTは否定できないため、他の所見と組み合わせて判断します。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 1
Q: 毛細血管において、静水圧は水分を血管内へ引き戻し、膠質浸透圧（アルブミン）は水分を間質へ押し出す。
E: 逆です。「静水圧」が水分を間質へ押し出し、「膠質浸透圧」が水分を血管内へ引き戻します。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 0
Q: 浮腫の触診では、脛骨前面・足背・内果を母指で5秒以上圧迫し、圧痕の深さと戻るまでの時間をみる。
E: その通りです。圧痕の深さで1+（2mm）〜4+（8mm）に評価します。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 0
Q: 左心不全では肺うっ血が生じるため、呼吸困難や起座呼吸がみられ、聴診では肺の水泡音が聴かれる。
E: その通りです。一方、右心不全では体うっ血が生じ、下腿の浮腫や体重増加がみられます。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 0
Q: ネフローゼ症候群や肝硬変による浮腫は、主に低アルブミン血症に伴う「膠質浸透圧の低下」が原因である。
E: その通りです。心不全や腎不全、DVTによる浮腫は主に「静水圧の上昇」によって生じます。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 1
Q: 全身性の浮腫（心不全・腎不全など）では左右非対称の浮腫がみられ、局所性の浮腫（DVTなど）では左右対称となる。
E: 逆です。局所性の浮腫は「左右非対称（左右差あり）」、全身性の浮腫は「左右対称」となります。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 0
Q: 心不全患者の起座呼吸は、臥位になると下半身からの静脈還流量が増え、肺うっ血が悪化するために起こる。
E: その通りです。座位になると静脈還流量が減り、肺うっ血が軽減するため呼吸が楽になります。
===
筋骨格系のフィジカルアセスメント | 筋の基礎知識 | 0
Q: 骨格筋は横紋筋であり自分の意思で動かせる随意筋であるが、心筋は横紋筋でありながら不随意筋である。
E: その通りです。なお、消化管や血管の壁にある平滑筋は不随意筋です。
===
筋骨格系のフィジカルアセスメント | 筋の基礎知識 | 1
Q: 神経筋接合部では、下位運動ニューロンからアドレナリンが放出されることで骨格筋線維に興奮が伝わる。
E: 神経筋接合部で放出されるのは「アセチルコリン」です。その後カルシウムイオンが働き筋収縮が起こります。
===
筋骨格系のフィジカルアセスメント | 筋の基礎知識 | 0
Q: 骨格筋の付着部のうち、固定されている（動きの小さい）側を「起始」といい、動きの大きい側を「停止」という。
E: その通りです。骨格筋は関節をはさんで腱や腱膜となって骨格に付着しています。
===
筋骨格系のフィジカルアセスメント | 骨格系と関節運動 | 1
Q: 人体を左右に分ける面を「前額面」といい、人体を前後に分ける面を「矢状面」という。
E: 逆です。左右に分ける面が「矢状面」、前後に分ける面が「前額面」、上下に分ける面が「水平面」です。
===
筋骨格系のフィジカルアセスメント | 骨格系と関節運動 | 0
Q: 前腕を軸とした回旋運動において、手掌を上に向ける動きを「回外」、手掌を下に向ける動きを「回内」という。
E: その通りです。また、体幹から遠ざかる動きを外転、近づく動きを内転といいます。
===
筋骨格系のフィジカルアセスメント | 骨格系と関節運動 | 1
Q: 足関節において、つま先を上に持ち上げる（伸展）動きを「底屈」といい、つま先を下に向ける（屈曲）動きを「背屈」という。
E: 逆です。足関節の伸展は「背屈」、屈曲は「底屈」といいます。
===
筋骨格系のフィジカルアセスメント | ROMとMMT | 0
Q: 関節可動域（ROM）の測定では、基本軸と移動軸の交点に角度計（ゴニオメーター）の中心を合わせて測る。
E: その通りです。自分で動かせる範囲を自動的ROM、他者の力で動かせる範囲を他動的ROMといいます。
===
筋骨格系のフィジカルアセスメント | ROMとMMT | 1
Q: 徒手筋力検査（MMT）において、スコア「3（Fair）」は「重力を除去すれば全可動域の運動が可能」な状態を指す。
E: それは「MMT 2（Poor）」です。「MMT 3（Fair）」は抵抗を加えなければ重力に反して全可動域の運動が可能な状態です。
===
筋骨格系のフィジカルアセスメント | ROMとMMT | 1
Q: MMTで徒手抵抗を加える際は、検査する関節よりも末梢側を固定し、素早く強い抵抗を加える。
E: 徒手抵抗を加える際は、検査する関節より「中枢」を固定し、「ゆっくり」と抵抗を加えていきます。
===
筋骨格系のフィジカルアセスメント | 運動の実行・調節と麻痺 | 0
Q: 上肢のバレー徴候では、手掌を上に向けて両腕を前方水平に挙上し、閉眼して20秒間保持できるかを観察する。
E: その通りです。錐体路障害（運動麻痺）があると、麻痺側の肘の屈曲、前腕の回内、腕の下降がみられます。
===
筋骨格系のフィジカルアセスメント | 運動の実行・調節と麻痺 | 1
Q: ミンガッツィーニ試験は腹臥位で実施する下肢の運動麻痺検査であり、下腿をベッドから45度挙上して保持してもらう。
E: 腹臥位で下腿を45度挙上するのは「下肢のバレー徴候」です。ミンガッツィーニ試験は「仰臥位」で行います。
===
筋骨格系のフィジカルアセスメント | 運動の実行・調節と麻痺 | 0
Q: 大脳皮質の運動野から生じた運動指令は、内包や延髄（錐体交叉）を通る「錐体路」を下行して脊髄へと向かう。
E: その通りです。この経路（上位・下位運動ニューロン）が障害されると運動麻痺が生じます。
===
筋骨格系のフィジカルアセスメント | 運動失調・不随意運動とアセスメント | 0
Q: 小脳障害による運動失調では、目標に近づくほど手の震えが大きくなる「企図振戦」がみられる。
E: その通りです。他に歩行失調、測定障害、反復拮抗運動不能などがみられます。
===
筋骨格系のフィジカルアセスメント | 運動失調・不随意運動とアセスメント | 0
Q: ロンベルグ試験では、つま先をそろえて直立し、開眼時よりも閉眼時に身体の動揺が顕著になる場合を陽性とする。
E: その通りです。転倒を防ぐため、常に対象者のすぐそばに立ち支えられるようにして実施します。
===
筋骨格系のフィジカルアセスメント | 運動失調・不随意運動とアセスメント | 1
Q: 大脳基底核の障害などで生じる不随意運動のうち、突然、瞬間的にピクッと動くものを「ジストニア」という。
E: 突然ピクッと動くのは「ミオクローヌス」です。ジストニアは持続的な筋収縮で身体がねじれる等の異常姿勢をとります。
===
看護過程 | 看護過程の概要と情報収集 | 1
Q: 看護過程は、アセスメント・看護問題の明確化・計画立案・実施・評価の5段階からなり、一方向で完結する直線的なプロセスである。
E: 一方向で終わるのではなく、評価から再びアセスメントや計画修正へ戻る「連続的かつ循環的なプロセス」です。
===
看護過程 | 看護過程の概要と情報収集 | 0
Q: 患者と初めて接するとき（入院時など）にスクリーニングの目的で収集する情報を「データベースアセスメント」という。
E: その通りです。気がかりな点を補足するフォーカスアセスメントや経時的アセスメントと使い分けます。
===
看護過程 | ゴードンの機能的健康パターン | 1
Q: ゴードンの機能的健康パターンにおいて、BMIや血清アルブミン値、褥瘡の有無は「活動―運動パターン」に分類される。
E: 栄養状態（BMI、Albなど）や皮膚状態（創傷、褥瘡、浮腫）は「栄養―代謝パターン」でアセスメントします。
===
看護過程 | 情報の分析と関連図 | 0
Q: 人間の反応の分析において、反応を引き起こす直接の要因を「原因」といい、反応を維持・改善・促進できる因子を「強み」という。
E: その通りです。患者の「強み」は看護計画の介入方法で活かすことができ、個別性のある看護につながります。
===
看護過程 | 看護問題の明確化と看護診断 | 1
Q: 看護問題の優先順位を決定する際、マズローの基本的欲求階層説では「安全のニード」が最優先される。
E: 最優先されるのは生命維持に関わる「生理的ニード」です。その次に「安全のニード」が続きます。
===
看護過程 | 看護計画の立案 | 1
Q: 期待される成果（目標）を表記する際は、「看護師が〜を介助する」のように看護師を主語にして記述する。
E: 期待される成果の主語は必ず「患者」にします（原則として主語は省略して表記します）。
===
看護過程 | 実施・評価と看護記録 | 1
Q: SOAP形式による経過記録において、「A（アセスメント）」には観察した客観的事実や実施したケアの手順のみを記録する。
E: 観察事実や介入内容は「O（客観的情報）」に記録し、「A」には介入前後の変化や達成度の考察を記録します。
"""


def parse_seed_questions(raw_text):
    items = []
    for block in raw_text.strip().split("==="):
        block = block.strip()
        if not block:
            continue
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        header = [h.strip() for h in lines[0].split("|")]
        if len(header) < 3:
            continue
        domain, sub_domain, ans_str = header[0], header[1], header[2]
        body = "".join(lines[1:])
        if "Q:" in body and "E:" in body:
            q_part, e_part = body.split("E:", 1)
            q_text = q_part.replace("Q:", "", 1).strip()
            e_text = e_part.strip()
            try:
                ans_idx = int(ans_str)
            except Exception:
                ans_idx = 0
            items.append(
                {
                    "domain": domain,
                    "sub_domain": sub_domain,
                    "question": q_text,
                    "options": ["○", "×"],
                    "answer_index": ans_idx,
                    "explanation": e_text,
                }
            )
    return items


SEED_QUESTIONS = parse_seed_questions(RAW_SEEDS)


# ==========================================
# 5. AI問題生成（Streamlit非依存）
# ==========================================
MODEL_PREFERENCE = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
    "gemini-1.5-flash",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
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
        chosen = (
            sorted(flashes, reverse=True)[0]
            if flashes
            else MODEL_PREFERENCE[0]
        )

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
                    "temperature": 1.0,
                },
            )
            text = getattr(resp, "text", None)
            if text:
                return text
        except Exception as e:
            errors.append(f"generate_content: {type(e).__name__}: {e}")

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
        except Exception as e:
            errors.append(f"interactions: {type(e).__name__}: {e}")

    except Exception as e:
        errors.append(f"google-genai未導入: {type(e).__name__}")

    # (2) 旧SDK: google-generativeai
    try:
        import google.generativeai as old_genai

        old_genai.configure(api_key=api_key)

        avail_old = []
        try:
            for m in old_genai.list_models():
                methods = getattr(m, "supported_generation_methods", []) or []
                if "generateContent" in methods:
                    avail_old.append(m.name.split("/")[-1])
        except Exception:
            pass

        candidates = []
        for want in MODEL_PREFERENCE:
            if not avail_old or want in avail_old:
                candidates.append(want)
        for a in avail_old:
            if ("flash" in a or "gemini" in a) and a not in candidates:
                candidates.append(a)
        if not candidates:
            candidates = list(MODEL_PREFERENCE)

        last = None
        for name in candidates:
            for use_json_cfg in (True, False):
                try:
                    if use_json_cfg:
                        model = old_genai.GenerativeModel(
                            name,
                            generation_config={
                                "response_mime_type": "application/json",
                                "temperature": 1.0,
                            },
                        )
                    else:
                        model = old_genai.GenerativeModel(name)
                    resp = model.generate_content(prompt)
                    if getattr(resp, "text", None):
                        with _MODEL_LOCK:
                            RESOLVED_MODEL["name"] = name
                        return resp.text
                except Exception as e:
                    last = f"{name}: {type(e).__name__}: {e}"
        errors.append(f"旧SDK: {last}")
    except Exception as e:
        errors.append(f"旧SDK未導入: {type(e).__name__}: {e}")

    raise RuntimeError(" / ".join(errors))


def normalize_text(s):
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
        question = str(
            item.get("question")
            or item.get("問題")
            or item.get("statement")
            or ""
        ).strip()
        explanation = str(
            item.get("explanation")
            or item.get("解説")
            or item.get("reason")
            or item.get("rationale")
            or ""
        ).strip()
        if not question:
            continue
        ans_raw = item.get("answer_index")
        if ans_raw is None:
            ans_raw = item.get("answer")
        if str(ans_raw).strip() in ("0", "○", "true", "True"):
            idx = 0
        elif str(ans_raw).strip() in ("1", "×", "false", "False"):
            idx = 1
        else:
            try:
                idx = int(str(ans_raw).strip())
            except Exception:
                continue
        if idx not in (0, 1):
            continue
        cleaned.append(
            {
                "question": question,
                "options": ["○", "×"],
                "answer_index": idx,
                "explanation": explanation or "資料の記載内容を確認しましょう。",
            }
        )
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


def build_prompt(
    domain, sub_domain, difficulty, source_text, avoid_questions, n
):
    avoid_block = ""
    if avoid_questions:
        sample = avoid_questions[:25]
        listed = "\n".join(f"- {q}" for q in sample)
        avoid_block = (
            "\n【既存の問題（同じ文意は避け、別の観点・逆の正誤等で作ること）】\n"
            + listed
        )

    return f"""あなたは看護教育の専門家です。
以下の【参考資料】の事実のみに基づき、○×問題を「{n}問」作成してください。

【条件】
- 領域: {domain} / 範囲: {sub_domain} / 難易度: {difficulty}
- answer_index は 0（○が正解）または 1（×が正解）の整数
- ○が正解の問題と×が正解の問題を混ぜること
- explanation には2〜3文で簡潔な解説を書くこと
{avoid_block}

【出力フォーマット（JSONのみを出力）】
{{
  "questions": [
    {{
      "question": "問題文",
      "answer_index": 0,
      "explanation": "解説文"
    }}
  ]
}}

【参考資料】
{source_text}
"""


def generate_quiz_core(
    api_key,
    domain,
    sub_domain,
    difficulty,
    source_text,
    avoid_keys,
    avoid_questions,
    n=4,
    attempts=2,
):
    if source_text == "準備中":
        return [], "資料ファイルが見つかりません。"
    if not api_key:
        return [], "GEMINI_API_KEY が設定されていません。"

    last_error = ""
    for i in range(attempts):
        # 2回目の試行では除外リストを減らして生成しやすくする
        av_q = avoid_questions if i == 0 else avoid_questions[:8]
        prompt = build_prompt(
            domain, sub_domain, difficulty, source_text, av_q, n
        )
        try:
            raw = _call_gemini(api_key, prompt)
            parsed = coerce_quiz_items(raw)
            items = drop_already_asked(parsed, avoid_keys)
            if items:
                return items, ""
            last_error = "新しい問題を取り出せませんでした（既存問題との重複）。"
        except Exception as e:
            last_error = str(e)
        if i < attempts - 1:
            time.sleep(0.8)
    return [], last_error


# ==========================================
# 6. 問題バンク（Firestore）
# ==========================================
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_bank_from_db():
    if not DB_AVAILABLE:
        return []
    out = []
    for doc in db.collection(QUESTIONS_COLLECTION).stream():
        d = doc.to_dict() or {}
        d["id"] = doc.id
        out.append(d)
    return out


def fetch_bank():
    if (
        "bank_items" not in st.session_state
        or st.session_state.bank_items is None
    ):
        st.session_state.bank_items = list(_fetch_bank_from_db())
    return st.session_state.bank_items


def invalidate_bank():
    try:
        _fetch_bank_from_db.clear()
    except Exception:
        pass
    st.session_state.bank_items = None


@st.cache_data(ttl=300, show_spinner=False)
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
    db.collection(SETTINGS_DOC[0]).document(SETTINGS_DOC[1]).set(
        values, merge=True
    )
    try:
        fetch_settings.clear()
    except Exception:
        pass


def bank_add(
    items, domain, sub_domain, difficulty, status="approved", source="ai"
):
    if not DB_AVAILABLE or not items:
        return [], 0

    current_bank = fetch_bank()
    existing = {q.get("qkey") for q in current_bank}
    batch = db.batch()
    added_docs = []
    skipped = 0

    for item in items:
        qkey = normalize_text(item["question"])
        if qkey in existing:
            skipped += 1
            continue
        existing.add(qkey)

        target_domain = item.get("domain") or domain
        target_sub = item.get("sub_domain") or sub_domain
        ref = db.collection(QUESTIONS_COLLECTION).document()
        doc_data = {
            "domain": target_domain,
            "sub_domain": target_sub,
            "difficulty": difficulty,
            "question": item["question"],
            "options": ["○", "×"],
            "answer_index": int(item["answer_index"]),
            "explanation": item.get("explanation", ""),
            "status": status,
            "qkey": qkey,
            "source": source,
            "created_at": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
        }
        batch.set(ref, doc_data)
        doc_with_id = dict(doc_data)
        doc_with_id["id"] = ref.id
        added_docs.append(doc_with_id)

    if added_docs:
        batch.commit()
        if st.session_state.get("bank_items") is not None:
            st.session_state.bank_items.extend(added_docs)
        try:
            _fetch_bank_from_db.clear()
        except Exception:
            pass
    return added_docs, skipped


def ensure_seed_in_bank():
    if not DB_AVAILABLE or st.session_state.get("seed_checked"):
        return
    st.session_state.seed_checked = True
    bank = fetch_bank()
    existing = {q.get("qkey") for q in bank}
    missing = [
        s for s in SEED_QUESTIONS if normalize_text(s["question"]) not in existing
    ]
    if missing:
        bank_add(
            missing,
            "",
            "すべて",
            DIFFICULTIES[0],
            status="approved",
            source="premade",
        )


def bank_set_status(question_id, status):
    if not DB_AVAILABLE:
        return
    db.collection(QUESTIONS_COLLECTION).document(question_id).set(
        {"status": status}, merge=True
    )
    invalidate_bank()


def bank_update(question_id, fields):
    if not DB_AVAILABLE:
        return
    db.collection(QUESTIONS_COLLECTION).document(question_id).set(
        fields, merge=True
    )
    invalidate_bank()


def bank_filter(bank, domain, sub_domain=None, status=None):
    out = []
    for q in bank:
        if q.get("domain") != domain:
            continue
        if status and q.get("status") != status:
            continue
        if (
            sub_domain
            and sub_domain != "すべて"
            and q.get("sub_domain") != sub_domain
        ):
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
        rows.append(
            {
                "出題範囲": sd,
                "承認済み（出題中）": sum(
                    1 for q in items if q.get("status") == "approved"
                ),
                "うちAI自動追加": sum(
                    1
                    for q in items
                    if q.get("status") == "approved"
                    and q.get("source") == "ai-live"
                ),
                "未確認（保留）": sum(
                    1 for q in items if q.get("status") == "pending"
                ),
                "却下": sum(
                    1 for q in items if q.get("status") == "rejected"
                ),
            }
        )
    return rows


def pick_from_bank(domain, sub_domain, difficulty, seen_ids):
    bank = fetch_bank()
    blocked = set(seen_ids) | set(st.session_state.session_seen)
    cands = [
        q
        for q in bank_filter(bank, domain, sub_domain, status="approved")
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
        1
        for q in bank_filter(bank, domain, sub_domain, status="approved")
        if q["id"] not in blocked
    )


# ==========================================
# 7. セッション管理・バックグラウンド生成
# ==========================================
DEFAULTS = {
    "current_question": None,
    "answered": False,
    "student_id": "",
    "loaded_student_id": "",
    "user_data": None,
    "is_correct": False,
    "session_seen": [],
    "session_keys": [],
    "q_counter": 0,
    "fallback_pool": [],
    "last_ai_error": "",
    "pending_next": False,
    "last_settings": {},
    "bank_items": None,
    "seed_checked": False,
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


def _prefetch_worker(
    box,
    epoch,
    api_key,
    domain,
    sub_domain,
    difficulty,
    source_text,
    avoid_keys,
    avoid_questions,
    n,
):
    try:
        items, error = generate_quiz_core(
            api_key,
            domain,
            sub_domain,
            difficulty,
            source_text,
            avoid_keys,
            avoid_questions,
            n=n,
        )
