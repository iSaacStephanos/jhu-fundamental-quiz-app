"""基礎看護技術論Ⅲ 無限問題集アプリ"""

import datetime
import json
import os
import random
import re
import threading
import time
import firebase_admin
from firebase_admin import credentials, firestore
import streamlit as st

st.set_page_config(page_title="基礎看護技術論Ⅲ 無限問題集", layout="wide")

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

DIFFICULTIES = ["標準（基本知識）", "応用（病態との関連・臨床的判断）"]

_KB_CACHE = {}


def clean_source_text(text):
  """資料内の不要な引用タグ等を除去する。"""
  return re.sub(r"\[\s*[c]ite\s*:[^\]]*\]", "", text)


def load_knowledge_base(domain):
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
      text = clean_source_text(f.read())
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
# 4. 初期問題（改行が入ってもエラーにならない複数行テキスト形式）
# ==========================================
SEED_RAW_TEXT = """
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
E: その通りです。PaO2の基準値は80〜100Torrであり、60Torrを下回ると呼吸不全と定義されます。
===
呼吸器系のフィジカルアセスメント | 体表解剖 | 1
Q: 胸骨角（ルイ角）は第1肋骨の付着部であり、肋骨・肋間を数える起点となる。
E: 胸骨角は「第2肋骨」の付着部です。第1肋骨は鎖骨の下にあり触れないため、ここを起点にします。
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
E: その通りです。前面では胸骨角の高さ、背面では第4胸椎棘突起の高さが気管分岐部の目安となります。
===
呼吸器系のフィジカルアセスメント | 体表解剖 | 0
Q: 肺尖部は鎖骨内側1/3より上方約3cmの位置にある。
E: その通りです。肺の頂上部分は鎖骨よりも上に飛び出しているため、鎖骨上窩の聴診も重要になります。
===
呼吸器系のフィジカルアセスメント | 問診・視診 | 0
Q: 起座呼吸は、横になると苦しく座ると楽になる状態で、心不全や喘息などが原因で起こる。
E: その通りです。横になると静脈還流量が増えて肺うっ血が悪化するため、起き上がることで呼吸が楽になります。
===
呼吸器系のフィジカルアセスメント | 問診・視診 | 1
Q: チェーンストークス呼吸は、深く大きな規則的な呼吸が続く異常呼吸であり、糖尿病性ケトアシドーシスでみられる。
E: それは「クスマウル呼吸」の説明です。チェーンストークス呼吸は、無呼吸と深浅の周期を繰り返す呼吸です。
===
呼吸器系のフィジカルアセスメント | 問診・視診 | 0
Q: チアノーゼは口唇や爪床が青紫色になるサインであるが、貧血の患者では現れにくい。
E: その通りです。チアノーゼは還元ヘモグロビンが5g/dL以上増えることで現れるため、貧血では出現しにくくなります。
===
呼吸器系のフィジカルアセスメント | 触診・打診 | 0
Q: 背部での胸郭の可動性の触診において、正常であれば深吸気時に左右の母指の間は約4cm広がる。
E: その通りです。第10肋骨付近で左右の母指を合わせ、深吸気で対称に約4cm広がるのが正常な所見です。
===
呼吸器系のフィジカルアセスメント | 触診・打診 | 1
Q: 正常な肺野を打診すると、鈍くつまったような濁音が聴こえる。
E: 正常な肺野は空気を多く含むため、響きのある低い「共鳴音（清音）」が聴こえます。濁音は肺炎や胸水貯留で聴こえます。
===
呼吸器系のフィジカルアセスメント | 聴診 | 1
Q: 捻髪音（細かい断続性副雑音）は「ブクブク」と聴こえ、気道内に分泌物が溜まっている状態で聴取される。
E: 「ブクブク」という音は「水泡音（粗い断続性副雑音）」です。捻髪音は「パリパリ」と聴こえます。
===
呼吸器系のフィジカルアセスメント | 聴診 | 0
Q: ストライダーは吸気時に聴こえる高調性の「ゼーゼー」という音で、上気道の狭窄を示す緊急所見である。
E: その通りです。喉頭浮腫や気道異物などによる上気道の狭窄を示しており、迅速な対応が必要です。
===
呼吸器系のフィジカルアセスメント | 聴診 | 1
Q: 呼吸音の聴診は、低音を聴き取るためベル型聴診器を皮膚に軽く当てて行う。
E: 呼吸音は高音であるため「膜型聴診器」を使用し、跡が残るくらいの強さでしっかりと押し当てます。
===
呼吸器系のフィジカルアセスメント | 聴診 | 1
Q: 正常な肺胞呼吸音は、大きく高い音であり、吸気と呼気の間に休止期（ポーズ）が聴取される。
E: 大きく高くポーズがあるのは「気管呼吸音」です。肺胞呼吸音は柔らかく低い音で、呼気は初期のみ聴こえます。
===
呼吸器系のフィジカルアセスメント | 聴診 | 0
Q: COPDや気管支喘息の患者では、気道の狭窄により「呼気延長」が聴取されることがある。
E: その通りです。気道が狭くなる疾患では、息を吐き出すのに時間がかかるため呼気が延長します。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 1
Q: 心房と心室の間にある房室弁は、右心系が僧帽弁、左心系が三尖弁である。
E: 右心系の房室弁は「三尖弁」、左心系の房室弁は「僧帽弁」です。弁は血液の逆流を防いでいます。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 0
Q: 肺動脈には静脈血が流れ、肺静脈には動脈血が流れる。
E: その通りです。右心室から肺へ向かう肺動脈には静脈血が流れ、肺から左心房へ戻る肺静脈には動脈血が流れます。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 0
Q: 頸静脈（内頸・外頸静脈）は右心房に直接つながっており、右心系の状態を反映する。
E: その通りです。一方、頸動脈（総頸動脈）は左心系の状態を反映し、急変時の循環確認にも用いられます。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 1
Q: 心臓の上部にある心基部は第5肋間左鎖骨中線上に位置し、心尖部は第2肋間の高さに位置する。
E: 逆です。心基部は「第2肋間」の高さ、心尖部は「第5肋間左鎖骨中線上」に位置します。
===
循環器系のフィジカルアセスメント | 刺激伝導系と心電図 | 1
Q: 心臓のペースメーカーである洞結節は左心房に位置し、ここで電気刺激が規則正しく発生する。
E: 洞結節は「右心房」にあります。洞結節→房室結節→ヒス束→左脚・右脚→プルキンエ線維→心室筋の順に伝わります。
===
循環器系のフィジカルアセスメント | 刺激伝導系と心電図 | 0
Q: 心電図の基本波形において、P波は心房の興奮（収縮）を、QRS波は心室の興奮（収縮）を意味する。
E: その通りです。T波は心室の興奮からの回復を意味し、1拍ごとにP→QRS→Tの順に繰り返します。
===
循環器系のフィジカルアセスメント | 刺激伝導系と心電図 | 1
Q: モニター心電図は10個の電極を用いて12通りの方向から記録し、心筋梗塞の診断を主な目的とする。
E: それは「12誘導心電図」の説明です。モニター心電図は3個の電極で主に第Ⅱ誘導を連続記録し、急変の早期発見を目的とします。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 0
Q: 心尖拍動は心尖部を「手掌と指腹」で触れ、振戦（スリル）は4つの弁の領域を「指のつけ根」で触れる。
E: その通りです。触診の前には目的と方法を説明して同意を得て、手を温めておきます。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 0
Q: 心尖拍動の正常な位置は胸骨中線左側10cm以内であり、範囲は2cm以内（示指先端くらい）である。
E: その通りです。左心室が拡大すると、心尖拍動は左方（10cm以上）へ移動し、範囲も2cm以上に広がります。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 1
Q: 仰臥位で心尖拍動が触れにくいときは、患者を右側臥位にすると触れやすくなる。
E: 仰臥位で触れにくいときは「左側臥位」にします。心臓が胸壁に近づくため拍動を触れやすくなります。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 0
Q: 胸痛の問診では、部位・範囲、痛みの性質、持続時間、労作との関係、放散痛の有無などを具体的に確認する。
E: その通りです。「締めつけられるような」「焼けるような」といった患者さんの表現を引き出しながら確認します。
===
循環器系のフィジカルアセスメント | 聴診 | 0
Q: 心音のⅠ音は僧帽弁・三尖弁（房室弁）が閉じる音であり、収縮期の始まりを示す。
E: その通りです。Ⅱ音は大動脈弁・肺動脈弁（動脈弁）が閉じる音で、拡張期の始まりを示します。
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
E: 逆です。心尖部では「Ⅰ音」が大きく、心基部では「Ⅱ音」が大きく聴こえます。エルプ領域では同じ大きさです。
===
循環器系のフィジカルアセスメント | 末梢動脈・静脈 | 1
Q: 中心性チアノーゼは四肢末端のみに出現し、酸素投与では改善しない。
E: 中心性チアノーゼは全身や口腔粘膜にも出現し、酸素投与で改善します。四肢末端のみに出るのは末梢性チアノーゼです。
===
循環器系のフィジカルアセスメント | 末梢動脈・静脈 | 0
Q: 爪床圧迫テスト（CRT）は、爪床を圧迫して解除した後、赤みが2秒以内に戻れば正常である。
E: その通りです。2秒以上かかる場合はショックや脱水などの末梢循環不全を疑います（アレンテストは5秒以内が正常）。
===
循環器系のフィジカルアセスメント | 末梢動脈・静脈 | 0
Q: Homans（ホーマンズ）徴候は下肢の深部静脈血栓症（DVT）の検査であり、足関節を背屈させて腓腹部に疼痛が出れば陽性となる。
E: その通りです。ただし陰性でもDVTは否定できないため、下肢の腫脹・疼痛・色調変化など複数の所見で判断します。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 1
Q: 毛細血管と間質の水分のやりとりにおいて、静水圧は水分を血管内へ引き戻し、膠質浸透圧（アルブミン）は水分を間質へ押し出す。
E: 逆です。「静水圧」が水分を血管外（間質）へ押し出し、「膠質浸透圧（アルブミン）」が水分を血管内へ引き戻します。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 0
Q: 浮腫の触診では、脛骨前面・足背・内果を母指で5秒以上圧迫し、圧痕の深さと戻るまでの時間をみる。
E: その通りです。圧痕の深さで1+（2mm）〜4+（8mm）に評価します。皮膚が脆弱なため愛護的に触れます。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 0
Q: 左心不全では肺うっ血が生じるため、主な症状として呼吸困難や起座呼吸がみられ、聴診では肺の水泡音が聴かれる。
E: その通りです。一方、右心不全では全身の静脈に体うっ血が生じ、下腿の圧痕性浮腫や体重増加などがみられます。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 0
Q: ネフローゼ症候群や肝硬変による浮腫は、主に低アルブミン血症に伴う「膠質浸透圧の低下」が原因となって生じる。
E: その通りです。一方、心不全や腎不全、深部静脈血栓症（DVT）による浮腫は主に「静水圧の上昇」によって生じます。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 1
Q: 全身性の浮腫（心不全・腎不全・肝不全など）では左右非対称の浮腫がみられ、局所性の浮腫（DVTなど）では左右対称の浮腫がみられる。
E: 逆です。局所性の浮腫は「左右非対称（左右差あり）」、全身性の浮腫は「左右対称（左右差なし）」となります。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 0
Q: 心不全の患者が横になると息苦しくなる（起座呼吸）のは、臥位になることで下半身からの静脈還流量が増え、肺うっ血が悪化するためである。
E: その通りです。座位になると下半身からの静脈還流量が減り、肺うっ血が軽減するため呼吸が楽になります。
===
筋骨格系のフィジカルアセスメント | 筋の基礎知識 | 0
Q: 骨格筋は横紋筋であり自分の意思で動かせる随意筋であるが、心筋は横紋筋でありながら不随意筋である。
E: その通りです。なお、消化管や血管の壁にある平滑筋（瞳孔散大筋なども含む）は不随意筋です。
===
筋骨格系のフィジカルアセスメント | 筋の基礎知識 | 1
Q: 下位運動ニューロンと骨格筋線維が接する神経筋接合部では、神経からアドレナリンが放出されることで筋線維に興奮が伝わる。
E: 神経筋接合部で放出される神経伝達物質は「アセチルコリン」です。その後カルシウムイオンが働き筋収縮が起こります。
===
筋骨格系のフィジカルアセスメント | 筋の基礎知識 | 0
Q: 骨格筋の付着部のうち、固定されている（動きの小さい）側を「起始」といい、動きの大きい側を「停止」という。
E: その通りです。骨格筋は関節をはさんで腱や腱膜となって骨格に付着しています。
===
筋骨格系のフィジカルアセスメント | 骨格系と関節運動 | 1
Q: 人体を左右に分ける面を「前額面」といい、人体を前後に分ける面を「矢状面」という。
E: 逆です。人体を左右に分ける面が「矢状面」、前後に分ける面が「前額面」、上下に分ける面が「水平面」です。
===
筋骨格系のフィジカルアセスメント | 骨格系と関節運動 | 0
Q: 前腕を軸とした回旋運動において、手掌を上に向ける動きを「回外」、手掌を下に向ける動きを「回内」という。
E: その通りです。また、体幹から遠ざかる動きを外転、近づく動きを内転といいます。
===
筋骨格系のフィジカルアセスメント | 骨格系と関節運動 | 1
Q: 足関節において、つま先を上に持ち上げる（伸展）動きを「底屈」といい、つま先を下に向ける（屈曲）動きを「背屈」という。
E: 逆です。足関節の伸展は「背屈」、屈曲は「底屈」といいます（手関節では伸展を背屈、屈曲を掌屈といいます）。
===
筋骨格系のフィジカルアセスメント | ROMとMMT | 0
Q: 関節可動域（ROM）の測定では、基本軸と移動軸の交点に角度計（ゴニオメーター）の中心を合わせて角度を測る。
E: その通りです。自分で動かせる範囲を自動的関節可動域、他者の力で動かせる範囲を他動的関節可動域といいます。
===
筋骨格系のフィジカルアセスメント | ROMとMMT | 1
Q: 徒手筋力検査（MMT）において、スコア「3（Fair）」は「重力を除去すれば全可動域の運動が可能」な状態を指す。
E: 重力を除去すれば全可動域の運動が可能なのは「MMT 2（Poor）」です。「MMT 3」は抵抗なしで重力に反して動かせる状態です。
===
筋骨格系のフィジカルアセスメント | ROMとMMT | 1
Q: MMTで徒手抵抗を加える際は、検査する関節よりも末梢側を固定し、素早く強い抵抗を加える。
E: 徒手抵抗を加える際は、検査する関節より「中枢」を固定し、「ゆっくり」と抵抗を加えていきます。
===
筋骨格系のフィジカルアセスメント | 運動の実行・調節と麻痺 | 0
Q: 上肢のバレー徴候の観察では、手掌を上に向けて両腕を前方水平に挙上してもらい、閉眼して20秒間保持できるかを観察する。
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
Q: 小脳の障害による運動失調では、目標に向かって手を伸ばすとき、目標に近づくほど手の震えが大きくなる「企図振戦」がみられる。
E: その通りです。小脳障害では他に、歩行失調、測定障害、反復拮抗運動不能などがみられます。
===
筋骨格系のフィジカルアセスメント | 運動失調・不随意運動とアセスメント | 0
Q: ロンベルグ試験では、つま先をそろえて直立し、開眼時よりも閉眼時に身体の動揺が顕著になる場合を陽性とする。
E: その通りです。検査中は転倒を防ぐため、常に対象者のすぐそばに立ち、いつでも支えられるようにします。
===
筋骨格系のフィジカルアセスメント | 運動失調・不随意運動とアセスメント | 1
Q: 大脳基底核の障害などによって生じる不随意運動のうち、突然、瞬間的にピクッと動くものを「ジストニア」という。
E: 突然ピクッと動くのは「ミオクローヌス」です。ジストニアは持続的・断続的な筋収縮により身体がねじれたりする不随意運動です。
===
看護過程 | 看護過程の概要と情報収集 | 1
Q: 看護過程は、①アセスメント、②看護問題の明確化、③計画立案、④実施、⑤評価の5段階からなり、一方向で完結する直線的なプロセスである。
E: 看護過程は一方向で終わるのではなく、評価から再びアセスメントや計画修正へ戻る「連続的かつ循環的なプロセス」です。
===
看護過程 | 看護過程の概要と情報収集 | 0
Q: 患者と初めて接するとき（入院時など）にスクリーニングの目的で収集する情報を「データベースアセスメント」という。
E: その通りです。気がかりな点を補足するフォーカスアセスメントや、時間経過に伴う経時的アセスメントと使い分けます。
===
看護過程 | ゴードンの機能的健康パターン | 1
Q: ゴードンの機能的健康パターンにおいて、BMIや血清アルブミン値（Alb）、褥瘡や浮腫の有無は「活動―運動パターン」に分類される。
E: 栄養状態（BMI、TP、Albなど）や皮膚状態（創傷、褥瘡、浮腫）、免疫状態は「栄養―代謝パターン」でアセスメントします。
===
看護過程 | ゴードンの機能的健康パターン | 0
Q: ゴードンの「自己知覚―自己概念パターン」では、アイデンティティやボディイメージ、自尊感情などをアセスメントする。
E: その通りです。「自分をどう見ているか・感じているか」は病気や治療に向き合う姿勢にも影響する重要な情報です。
===
看護過程 | 情報の分析と関連図 | 0
Q: 人間の反応の分析において、反応を引き起こす直接の要因を「原因」といい、人間の反応を維持・改善・促進できる因子を「強み」という。
E: その通りです。患者の「強み」は看護計画の介入方法で活かすことができ、個別性のある看護につながります。
===
看護過程 | 情報の分析と関連図 | 0
Q: 病態関連図は病態の機序を把握するのに適しているが、心理面や社会面を含めた問題をすべて把握できない欠点がある。
E: その通りです。そのため看護過程では、身体的・心理的・社会的状況の関連性を把握できる「人間の反応の関連図」を用います。
===
看護過程 | 看護問題の明確化と看護診断 | 1
Q: 看護問題の優先順位を決定する際、マズローの基本的欲求階層説では「安全のニード」が最優先される。
E: 最優先されるのは生命維持に関わる「生理的ニード」です。その次に「安全のニード（痛みや不安も含む）」が続きます。
===
看護過程 | 看護問題の明確化と看護診断 | 0
Q: リスク型の看護問題（潜在問題）を表記する際は、「（危険因子）に関連した（看護問題）」の形式で記述する。
E: その通りです。まだ症状は出現していないため、「〜によって明らか」という症状・徴候は記述しません。
===
看護過程 | 看護計画の立案 | 1
Q: 期待される成果（目標）を表記する際は、「看護師が〜を介助する」のように看護師を主語にして記述する。
E: 期待される成果の主語は必ず「患者」にします（原則として主語は省略して表記します）。
===
看護過程 | 看護計画の立案 | 0
Q: 看護計画は看護問題ごとに、観察計画（OP）、直接ケア計画（TPまたはCP）、教育計画（EP）の3つに分けて立案する。
E: その通りです。また、標準化されたケア計画表であるクリティカルパスから逸脱することを「バリアンス」といいます。
===
看護過程 | 実施・評価と看護記録 | 1
Q: SOAP形式による経過記録において、「A（アセスメント）」には観察した客観的事実や実施したケアの手順のみを記録する。
E: 観察した事実や実施した介入内容は「O（客観的情報）」に記録します。「A」には変化や達成度に関する考察を記録します。
===
看護過程 | 実施・評価と看護記録 | 0
Q: 保健師助産師看護師法に定められた守秘義務は、看護師の資格を喪失（退職など）した後も継続する。
E: その通りです。正当な理由なく業務上知りえた秘密を漏らしてはならず、看護職でなくなった後においても同様です。
"""


def parse_seed_questions(raw_text):
  out = []
  for block in raw_text.strip().split("==="):
    lines = [
        line.strip() for line in block.strip().splitlines() if line.strip()
    ]
    if len(lines) < 3:
      continue
    header = [p.strip() for p in lines[0].split("|")]
    if len(header) != 3:
      continue
    domain, sub_domain, ans_str = header
    q_lines = []
    e_lines = []
    mode_flag = None
    for line in lines[1:]:
      if line.startswith("Q:"):
        mode_flag = "Q"
        q_lines.append(line[2:].strip())
      elif line.startswith("E:"):
        mode_flag = "E"
        e_lines.append(line[2:].strip())
      elif mode_flag == "Q":
        q_lines.append(line)
      elif mode_flag == "E":
        e_lines.append(line)
    if q_lines:
      out.append({
          "domain": domain,
          "sub_domain": sub_domain,
          "question": "".join(q_lines),
          "options": ["○", "×"],
          "answer_index": int(ans_str),
          "explanation": "".join(e_lines),
      })
  return out


SEED_QUESTIONS = parse_seed_questions(SEED_RAW_TEXT)


# ==========================================
# 5. AI問題生成（Streamlit非依存）
# ==========================================
MODEL_PREFERENCE = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest",
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
              "temperature": 1.0,
          },
      )
      text = getattr(resp, "text", None)
      if text:
        return text
      errors.append("generate_content: text が空")
    except Exception as e:
      errors.append(f"generate_content: {type(e).__name__}: {e}")
  except Exception as e:
    errors.append(f"google-genai: {type(e).__name__}: {e}")

  # (2) 旧SDK: google-generativeai
  try:
    import google.generativeai as old_genai

    old_genai.configure(api_key=api_key)

    avail = []
    try:
      for m in old_genai.list_models():
        methods = getattr(m, "supported_generation_methods", [])
        if "generateContent" in methods:
          avail.append(m.name.split("/")[-1])
    except Exception:
      pass

    candidates = [m for m in MODEL_PREFERENCE if (not avail or m in avail)]
    for a in avail:
      if "flash" in a and "embedding" not in a and a not in candidates:
        candidates.append(a)
    if not candidates:
      candidates = list(MODEL_PREFERENCE)

    last = None
    for name in candidates:
      try:
        model = old_genai.GenerativeModel(
            name,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 1.0,
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
  s = re.sub(r"\s+", "", str(s))
  s = re.sub(r"[。、，．,\.「」『』（）\(\)\[\]”\"'！!？\?]", "", s)
  return s


def parse_answer_index(val):
  """AIが 0/1 以外に ○/× や true/false を返しても柔軟に 0 または 1 に変換する。"""
  if isinstance(val, bool):
    return 0 if val else 1
  if isinstance(val, int) and val in (0, 1):
    return val
  s = str(val).strip().lower()
  if s in ("0", "○", "◯", "〇", "maru", "true", "正しい", "正"):
    return 0
  if s in ("1", "×", "✕", "✖", "batsu", "false", "誤り", "誤"):
    return 1
  return None


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
    for key in ("questions", "items", "data", "quiz", "problems"):
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
        or item.get("問題文")
        or item.get("statement")
        or ""
    ).strip()
    explanation = str(
        item.get("explanation")
        or item.get("解説")
        or item.get("解説文")
        or item.get("reason")
        or ""
    ).strip()
    if not question:
      continue

    raw_ans = item.get("answer_index")
    if raw_ans is None:
      raw_ans = item.get("answer")
    if raw_ans is None:
      raw_ans = item.get("正解")
    idx = parse_answer_index(raw_ans)
    if idx is None:
      continue

    cleaned.append({
        "question": clean_source_text(question),
        "options": ["○", "×"],
        "answer_index": idx,
        "explanation": (
            clean_source_text(explanation)
            or "資料の記載内容を確認しましょう。"
        ),
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


def build_prompt(
    domain, sub_domain, difficulty, source_text, avoid_questions, n
):
  avoid_block = ""
  if avoid_questions:
    sample = avoid_questions[:25]
    listed = "\n".join(f"- {q}" for q in sample)
    avoid_block = (
        "\n【既存の問題（これらと全く同じ文は避け、"
        "別の項目・別の数値・逆の正誤などで作問すること）】\n"
        + listed
    )

  return f"""あなたは看護教育の専門家です。以下の【参考資料】の内容に基づいて、○×問題（正誤問題）を「{n}問」作成してください。

【条件】
- 領域: {domain}（範囲: {sub_domain} / 難易度: {difficulty}）
- answer_index は、問題文が正しければ 0（○）、誤りであれば 1（×）の整数にしてください。
- 正しい文（0）と誤りの文（1）を混ぜてください。
- 解説（explanation）は2〜3文で簡潔に記述してください。
{avoid_block}

【出力フォーマット（JSONのみ）】
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

  prompt = build_prompt(
      domain, sub_domain, difficulty, source_text, avoid_questions, n
  )

  last_error = ""
  for i in range(attempts):
    try:
      raw = _call_gemini(api_key, prompt)
      parsed = coerce_quiz_items(raw)
      items = drop_already_asked(parsed, avoid_keys)
      if items:
        return items, ""
      if parsed:
        last_error = (
            "生成された問題がすべてバンク内の既存問題と重複したため除外されました。"
        )
      else:
        last_error = "AIの応答形式を解析できませんでした。"
    except Exception as e:
      last_error = str(e)
    if i < attempts - 1:
      time.sleep(1.0)
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
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
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
        missing, "", "すべて", DIFFICULTIES[0], status="approved", source="premade"
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
        "承認済み（出題中）": sum(
            1 for q in items if q.get("status") == "approved"
        ),
        "うちAI自動追加": sum(
            1
            for q in items
            if q.get("status") == "approved" and q.get("source") == "ai-live"
        ),
        "未確認（保留）": sum(1 for q in items if q.get("status") == "pending"),
        "却下": sum(1 for q in items if q.get("status") == "rejected"),
    })
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
  except Exception as e:
    items, error = [], f"{type(e).__name__}: {e}"
  with box["lock"]:
    if box["epoch"] == epoch:
      box["items"].extend(items)
      box["error"] = error
    box["running"] = False


def resolve_target_sub_domain(domain, sub_domain):
  if sub_domain != "すべて":
    return sub_domain
  subs = [s for s in SUB_DOMAINS.get(domain, ["すべて"]) if s != "すべて"]
  return random.choice(subs) if subs else "すべて"


def start_background_generation(domain, sub_domain, difficulty, n=4):
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

  bank_qs = [
      q.get("question", "") for q in bank_filter(fetch_bank(), domain, target_sub)
  ]
  avoid_keys = [normalize_text(q) for q in bank_qs]
  avoid_keys += list(st.session_state.session_keys)
  random.shuffle(bank_qs)

  threading.Thread(
      target=_prefetch_worker,
      args=(
          box,
          epoch,
          GEMINI_API_KEY,
          domain,
          target_sub,
          difficulty,
          source_text,
          avoid_keys,
          bank_qs,
          n,
      ),
      daemon=True,
  ).start()


def harvest_background():
  box = get_prefetch_box()
  with box["lock"]:
    got = list(box["items"])
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
      bank_add(got, g_domain, g_sub, g_diff, status="approved", source="ai-live")
      return
    except Exception:
      pass

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
      "options": ["○", "×"],
      "answer_index": int(q["answer_index"]),
      "explanation": q.get("explanation") or "資料の記載内容を確認しましょう。",
      "id": question_id,
  }
  st.session_state.answered = False
  st.session_state.is_correct = False
  st.session_state.q_counter += 1
  st.session_state.session_keys.append(normalize_text(q["question"]))
  if question_id:
    st.session_state.session_seen.append(question_id)
    if st.session_state.user_data is not None:
      seen_list = st.session_state.user_data.setdefault("seen_ids", [])
      if question_id not in seen_list:
        seen_list.append(question_id)
    if user_ref is not None:
      try:
        user_ref.set(
            {"seen_ids": firestore.ArrayUnion([question_id])}, merge=True
        )
      except Exception:
        pass


def advance_to_next_question(domain, sub_domain, difficulty, seen_ids, user_ref):
  harvest_background()

  # (1) バンクから未出題の1問を選ぶ
  q = pick_from_bank(domain, sub_domain, difficulty, seen_ids)
  if q:
    set_question(q, question_id=q["id"], user_ref=user_ref)
    if fetch_settings().get("allow_live_generation", True):
      if count_unseen(domain, sub_domain, seen_ids) < 30:
        start_background_generation(domain, sub_domain, difficulty, n=4)
    return True, ""

  # (2) コード内の初期問題から未出題のものを探す
  used = set(st.session_state.session_keys)
  left = [
      s
      for s in SEED_QUESTIONS
      if s.get("domain") == domain
      and (sub_domain == "すべて" or s["sub_domain"] == sub_domain)
      and normalize_text(s["question"]) not in used
  ]
  if left:
    set_question(random.choice(left))
    start_background_generation(domain, sub_domain, difficulty, n=4)
    return True, ""

  if build_source_text(domain, sub_domain) == "準備中":
    return (
        False,
        "この領域の問題は現在準備中です。別の領域を選択してください。",
    )

  # (3) バンクの未出題が0問の場合 → AI生成
  settings = fetch_settings()
  if not settings.get("allow_live_generation", True):
    return (
        False,
        "この範囲の問題をすべて解き終えました。別の範囲を選んでください。",
    )

  if is_generating():
    wait_for_generation()
    harvest_background()
    q = pick_from_bank(domain, sub_domain, difficulty, seen_ids)
    if q:
      set_question(q, question_id=q["id"], user_ref=user_ref)
      start_background_generation(domain, sub_domain, difficulty, n=4)
      return True, ""

  target_sub = resolve_target_sub_domain(domain, sub_domain)
  source_text = build_source_text(domain, target_sub)
  bank_qs = [
      x.get("question", "") for x in bank_filter(fetch_bank(), domain, target_sub)
  ]
  with st.spinner("新しい問題を作成しています..."):
    items, error = generate_quiz_core(
        GEMINI_API_KEY,
        domain,
        target_sub,
        difficulty,
        source_text,
        [normalize_text(x) for x in bank_qs]
        + list(st.session_state.session_keys),
        bank_qs,
        n=4,
    )
  if items:
    st.session_state.last_ai_error = ""
    if DB_AVAILABLE:
      added_docs, _ = bank_add(
          items, domain, target_sub, difficulty, status="approved", source="ai-live"
      )
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

  return (
      False,
      st.session_state.last_ai_error or "新しい問題を作成できませんでした。",
  )


def request_next_question():
  st.session_state.pending_next = True


def render_score_metrics(container, user_data):
  current_streak = user_data.get("current_streak", 0)
  max_streak = user_data.get("max_streak", 0)
  total_answers = user_data.get("total_answers", 0)

  streak_title = get_streak_title(max_streak)
  total_title = get_total_title(total_answers)

  with container.container():
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
        st.caption(
            "連続正解称号：未獲得（30問連続正解で【🎖️ ルーキー】獲得！）"
        )

    with m_col2:
      st.metric(
          label="これまで取り組んだ問題数（総解答数）",
          value=f"{total_answers} 問",
          delta=get_next_total_goal(total_answers),
      )
      if total_title != "なし":
        st.info(f"**解答数称号：【{total_title}】**")
      else:
        st.caption(
            "解答数称号：未獲得（累計100問解答で【🌱 チャレンジャー】獲得！）"
        )


# ==========================================
# 8. UI
# ==========================================
ensure_seed_in_bank()

mode = st.sidebar.radio(
    "モード選択", ["学生用（クイズ演習）", "管理者用（モニタリング）"]
)

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
    student_id = st.text_input(
        "学籍番号を入力してください（半角数字7桁）",
        value=st.session_state.student_id,
    )
  with col_name:
    student_name = st.text_input("氏名（またはニックネーム）")

  if not student_id:
    st.warning("演習を開始するには学籍番号を入力してください。")
    st.stop()

  if not (len(student_id) == 7 and student_id.isdigit()):
    st.error("エラー：学籍番号は「半角数字7桁」で入力してください。")
    st.stop()

  st.session_state.student_id = student_id
  user_ref = (
      db.collection("students").document(student_id) if DB_AVAILABLE else None
  )

  if (
      st.session_state.loaded_student_id != student_id
      or st.session_state.user_data is None
  ):
    base_data = {
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
    if user_ref is not None:
      try:
        user_doc = user_ref.get()
        if user_doc.exists:
          base_data.update(user_doc.to_dict() or {})
        else:
          user_ref.set(base_data)
      except Exception as e:
        st.warning(f"成績データの読み込みに失敗しました: {type(e).__name__}")
    st.session_state.user_data = base_data
    st.session_state.loaded_student_id = student_id

  user_data = st.session_state.user_data
  if student_name and user_data.get("name") != student_name:
    user_data["name"] = student_name
    if user_ref is not None:
      try:
        user_ref.set({"name": student_name}, merge=True)
      except Exception:
        pass

  seen_ids = user_data.get("seen_ids") or []

  st.divider()
  metrics_placeholder = st.empty()
  render_score_metrics(metrics_placeholder, user_data)
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

  current_settings = {
      "domain": domain,
      "sub_domain": sub_domain,
      "difficulty": difficulty,
  }
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
    ok, msg = advance_to_next_question(
        domain, sub_domain, difficulty, seen_ids, user_ref
    )
    if not ok:
      st.error(f"⚠️ {msg}")
      if st.session_state.last_ai_error:
        with st.expander("詳細（担当者向け）"):
          st.code(st.session_state.last_ai_error, language="text")

  if st.session_state.current_question:
    q = st.session_state.current_question
    st.markdown(f"### Q. {q['question']}")

    st.markdown("### 選択肢を選んでください：")
    choice = st.radio(
        "選択肢",
        q["options"],
        key=f"quiz_choice_{st.session_state.q_counter}",
        index=None,
        disabled=st.session_state.answered,
        horizontal=True,
        label_visibility="collapsed",
    )

    submit_placeholder = st.empty()
    if not st.session_state.answered:
      if submit_placeholder.button("回答を送信する", type="primary"):
        if choice is None:
          st.warning("「○」または「×」を選んでから送信してください。")
        else:
          submit_placeholder.empty()
          st.session_state.answered = True
          is_correct = q["options"].index(choice) == int(q["answer_index"])
          st.session_state.is_correct = is_correct

          prev_max_streak = user_data.get("max_streak", 0)
          prev_total = user_data.get("total_answers", 0)

          new_total = prev_total + 1
          user_data["total_answers"] = new_total

          if is_correct:
            user_data["correct_answers"] = (
                user_data.get("correct_answers", 0) + 1
            )
            new_streak = user_data.get("current_streak", 0) + 1
            user_data["current_streak"] = new_streak
            if new_streak > prev_max_streak:
              user_data["max_streak"] = new_streak
          else:
            new_streak = 0
            user_data["current_streak"] = 0

          new_max_streak = user_data.get("max_streak", 0)
          new_streak_title = get_streak_title(new_max_streak)
          new_total_title = get_total_title(new_total)

          user_data["streak_title"] = new_streak_title
          user_data["total_title"] = new_total_title
          user_data["has_title"] = (
              new_streak_title != "なし" or new_total_title != "なし"
          )

          render_score_metrics(metrics_placeholder, user_data)

          celebrations = []
          for threshold, t_name in STREAK_TITLES:
            if is_correct and new_streak == threshold:
              celebrations.append(
                  f"🎊 連続 {threshold} 問正解達成！称号【{t_name}】を獲得しました！"
              )
          for threshold, t_name in TOTAL_TITLES:
            if new_total == threshold:
              celebrations.append(
                  f"🎊 累計 {threshold} 問解答達成！称号【{t_name}】を獲得しました！"
              )
          if celebrations:
            st.balloons()
            for c_msg in celebrations:
              st.success(c_msg)

          if user_ref is not None:
            try:
              payload = {k: v for k, v in user_data.items() if k != "seen_ids"}
              user_ref.set(payload, merge=True)
            except Exception as e:
              st.warning(f"成績の保存に失敗しました: {type(e).__name__}")

    if st.session_state.answered:
      if st.session_state.is_correct:
        st.success("🎉 正解です！")
      else:
        st.error("❌ 不正解です...")

      st.write("---")
      st.info(
          "**【解説】**\n"
          f"{q.get('explanation') or '資料の記載内容を確認しましょう。'}"
      )
      st.write("---")

      st.button("次の問題へ ➡️", type="primary", on_click=request_next_question)

      unseen = count_unseen(domain, sub_domain, seen_ids)
      if unseen > 0:
        st.caption(
            f"この範囲のストック: あと {unseen} 問"
            "（待ち時間ゼロで出題されます / 裏でAIが随時問題を追加中）"
        )
      else:
        st.caption(
            "現在AIが新しい問題を裏で追加作成しています。"
            "「次の問題へ」を押すと出題されます。"
        )

# ------------------------------------------------------------ 管理者画面
elif mode == "管理者用（モニタリング）":
  st.title("📊 管理者画面")

  admin_password = get_secret("ADMIN_PASSWORD", "admin1234")
  admin_pass = st.text_input(
      "管理者パスワードを入力してください", type="password"
  )
  if admin_pass != admin_password:
    st.info("正しいパスワードを入力すると管理機能が表示されます。")
    if admin_password == "admin1234":
      st.caption(
          "※ 現在は初期パスワードのままです。Streamlit の Secrets に "
          "ADMIN_PASSWORD を設定して変更してください。"
      )
    st.stop()

  if not DB_AVAILABLE:
    st.error("データベースに接続できないため、管理機能を利用できません。")
    st.code(DB_ERROR or "不明", language="text")
    st.stop()

  tab_bank, tab_review, tab_scores = st.tabs([
      "🏦 問題バンク（一括生成）",
      "✅ 内容の確認・パトロール",
      "📈 成績モニタリング",
  ])

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
        [
            "即時承認（すぐに学生へ出題する）",
            "未確認（確認タブで承認してから出題する）",
        ],
        horizontal=True,
    )
    target_status = "approved" if "即時承認" in gen_status_choice else "pending"

    sub_options = [
        s for s in SUB_DOMAINS.get(g_domain, ["すべて"]) if s != "すべて"
    ]
    if not sub_options:
      sub_options = ["すべて"]

    col_a, col_b, col_c = st.columns(3)
    with col_a:
      g_sub = st.selectbox("出題範囲", sub_options, key="bank_sub")
    with col_b:
      g_diff = st.selectbox("難易度", DIFFICULTIES, key="bank_diff")
    with col_c:
      g_batches = st.number_input(
          "生成回数（1回あたり約5問）", min_value=1, max_value=20, value=2
      )

    if build_source_text(g_domain, g_sub) == "準備中":
      st.warning(
          f"この領域の資料ファイル（{FILE_MAP.get(g_domain)}）がまだGitHub上にありません。"
      )
    elif st.button("🚀 一括生成を開始する", type="primary"):
      progress = st.progress(0.0, text="準備中...")
      total_added = total_skipped = 0
      errors = []
      for i in range(int(g_batches)):
        bank_qs = [
            q.get("question", "")
            for q in bank_filter(fetch_bank(), g_domain, g_sub)
        ]
        random.shuffle(bank_qs)
        items, err = generate_quiz_core(
            GEMINI_API_KEY,
            g_domain,
            g_sub,
            g_diff,
            build_source_text(g_domain, g_sub),
            [normalize_text(x) for x in bank_qs],
            bank_qs,
            n=5,
        )
        if items:
          added_docs, s = bank_add(
              items,
              g_domain,
              g_sub,
              g_diff,
              status=target_status,
              source="ai-admin",
          )
          total_added += len(added_docs)
          total_skipped += s
        elif err:
          errors.append(err)
        progress.progress(
            (i + 1) / int(g_batches),
            text=f"{i + 1}/{int(g_batches)} 回目完了 / 追加 {total_added} 問",
        )
      progress.empty()

      if total_added:
        st.success(
            f"✅ {total_added} 問を追加しました（重複除外: {total_skipped} 問）。"
        )
      else:
        st.warning(
            f"新しい問題を追加できませんでした（重複除外: {total_skipped} 問）。"
        )
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
        help=(
            "オンにすると、学生が解いている裏でAIが少しずつ問題を作り足すため、"
            "待ち時間ゼロで常に新しい問題が混ざるようになります。"
        ),
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
      r_domain = st.selectbox(
          "領域で絞る", ["すべて"] + AVAILABLE_DOMAINS, key="rev_domain"
      )

    if r_filter == "AI自動追加（承認済み・パトロール対象）":
      filtered = [
          q
          for q in bank
          if q.get("status") == "approved" and q.get("source") == "ai-live"
      ]
    elif r_filter == "未確認（保留中）":
      filtered = [q for q in bank if q.get("status") == "pending"]
    elif r_filter == "すべての承認済み問題":
      filtered = [q for q in bank if q.get("status") == "approved"]
    else:
      filtered = [q for q in bank if q.get("status") == "rejected"]

    target = [
        q for q in filtered if r_domain == "すべて" or q.get("domain") == r_domain
    ]
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
              f"<small>作成元: {q.get('source', '')} / "
              f"状態: {q.get('status', '')}</small>",
              unsafe_allow_html=True,
          )
          new_q = st.text_area(
              "問題文", value=q.get("question", ""), key=f"q_{q['id']}", height=80
          )
          ans = st.radio(
              "正解",
              ["○", "×"],
              index=int(q.get("answer_index", 0)),
              key=f"a_{q['id']}",
              horizontal=True,
          )
          new_e = st.text_area(
              "解説",
              value=q.get("explanation", ""),
              key=f"e_{q['id']}",
              height=80,
          )

          b1, b2, b3 = st.columns(3)
          with b1:
            if st.button("💾 内容を保存（承認維持）", key=f"ok_{q['id']}"):
              bank_update(
                  q["id"],
                  {
                      "question": new_q,
                      "answer_index": ["○", "×"].index(ans),
                      "explanation": new_e,
                      "qkey": normalize_text(new_q),
                      "status": "approved",
                      "source": "verified",
                  },
              )
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
