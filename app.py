"""基礎看護技術論Ⅲ 無限問題集アプリ（静的問題バンク・待ち時間ゼロ版）"""

import json
import os
import random
import re
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
# 1. 初期設定（Firebase接続）
# ==========================================
def get_secret(name, default=""):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


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
# 3. 領域・出題範囲・ファイル対応の定義
# ==========================================
AVAILABLE_DOMAINS = [
    "呼吸器系のフィジカルアセスメント",
    "循環器系のフィジカルアセスメント",
    "脳神経系のフィジカルアセスメント",
    "筋骨格系のフィジカルアセスメント",
    "消化器系のフィジカルアセスメント",
    "看護過程",
]

# 各領域で優先して読み込む問題バンクファイルの候補
BANK_FILES_MAP = {
    "呼吸器系のフィジカルアセスメント": ["questions_kokyu.txt", "kokyu_bank.txt", "kokyu.txt"],
    "循環器系のフィジカルアセスメント": ["questions_junkanki.txt", "junkanki_bank.txt", "junkanki.txt"],
    "脳神経系のフィジカルアセスメント": ["questions_noushinkei.txt", "noushinkei_bank.txt", "noushinkei.txt"],
    "筋骨格系のフィジカルアセスメント": ["questions_kinkokkaku.txt", "kinkokkaku_bank.txt", "kinkokkaku.txt"],
    "消化器系のフィジカルアセスメント": ["questions_shokaki.txt", "shokaki_bank.txt", "shokaki.txt"],
    "看護過程": ["questions_kangokatei.txt", "kangokatei_bank.txt", "kangokatei.txt"],
}

COMMON_BANK_FILES = ["questions_bank.txt", "all_questions.txt"]

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
    "脳神経系のフィジカルアセスメント": [
        "すべて",
        "中枢・末梢神経の構造と機能",
        "脳神経（12対）の解剖と機能",
        "意識状態と高次脳機能",
        "視覚・眼球運動・瞳孔・対光反射",
        "三叉・顔面・内耳・舌下神経",
    ],
    "筋骨格系のフィジカルアセスメント": [
        "すべて",
        "筋の基礎知識",
        "骨格系と関節運動",
        "ROMとMMT",
        "運動の実行・調節と麻痺",
        "運動失調・不随意運動とアセスメント",
    ],
    "消化器系のフィジカルアセスメント": [
        "すべて",
        "解剖と機能",
        "症状・徴候と問診",
        "視診と全体的な進め方",
        "聴診と打診",
        "触診",
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

DIFFICULTIES = ["すべて", "標準（基本知識）", "応用（病態との関連・臨床的判断）"]


# ==========================================
# 4. テキスト整形・問題パーサー
# ==========================================
def clean_source_text(text):
    """資料や問題文内の不要な引用タグ（ や [span_...]）を除去する。"""
    text = re.sub(r"\[\s*cite\s*:[^\]]*\]", "", str(text))
    text = re.sub(r"\[span_\d+\]\((?:start|end)_span\)", "", text)
    return text.strip()


def normalize_text(s):
    """重複判定・履歴照合用の正規化キー（qkey）を作る。"""
    s = clean_source_text(s)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[。、，．,\.「」『』（）\(\)\[\]”\"'！!？\?]", "", s)
    return s


def parse_answer_index(val):
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


def parse_question_blocks(raw_text, default_domain=""):
    """`===` 区切りのテキストから問題リストを抽出する（3項目・4項目ヘッダ両対応）。"""
    out = []
    if "Q:" not in raw_text:
        return out

    for block in raw_text.strip().split("==="):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if len(lines) < 3:
            continue

        # ヘッダ行（| を含む最初の行）を探す
        header_idx = -1
        for idx, line in enumerate(lines):
            if "|" in line and not line.startswith(("Q:", "E:")):
                header_idx = idx
                break
        if header_idx == -1:
            continue

        header = [p.strip() for p in lines[header_idx].split("|")]
        if len(header) == 4:
            domain, sub_domain, difficulty, ans_str = header
        elif len(header) == 3:
            domain, sub_domain, ans_str = header
            difficulty = "標準（基本知識）"
        elif len(header) == 2 and default_domain:
            domain = default_domain
            sub_domain, ans_str = header
            difficulty = "標準（基本知識）"
        else:
            continue

        ans_idx = parse_answer_index(ans_str)
        if ans_idx is None:
            continue

        q_lines = []
        e_lines = []
        mode_flag = None
        for line in lines[header_idx + 1 :]:
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
            q_text = clean_source_text("".join(q_lines))
            e_text = clean_source_text("".join(e_lines))
            qkey = normalize_text(q_text)
            out.append({
                "id": f"static_{qkey}",
                "qkey": qkey,
                "domain": domain or default_domain,
                "sub_domain": sub_domain,
                "difficulty": difficulty,
                "question": q_text,
                "options": ["○", "×"],
                "answer_index": ans_idx,
                "explanation": e_text or "資料の記載内容を確認しましょう。",
                "status": "approved",
                "source": "static_file",
            })
    return out


# ==========================================
# 5. 内蔵の初期問題（フォールバック用）
# ==========================================
SEED_RAW_TEXT = """
呼吸器系のフィジカルアセスメント | 解剖・生理 | 標準（基本知識） | 1
Q: 右主気管支は左主気管支よりも細く長いため、誤嚥性肺炎は左肺に起こりやすい。
E: 右主気管支の方が「太く短く」、分岐角度が小さい（約25度）ため、誤嚥肺炎は右肺に起こりやすいです。
===
呼吸器系のフィジカルアセスメント | 解剖・生理 | 標準（基本知識） | 0
Q: 腹式呼吸は安静時の呼吸の大部分を担い、胸式呼吸よりも1回換気量が多くなる。
E: その通りです。横隔膜の収縮による腹式呼吸は安静時の主な呼吸であり、多くの換気量を得られます。
===
呼吸器系のフィジカルアセスメント | 体表解剖 | 標準（基本知識） | 1
Q: 胸骨角（ルイ角）は第1肋骨の付着部であり、肋骨・肋間を数える起点となる。
E: 胸骨角は「第2肋骨」の付着部です。第1肋骨は鎖骨の下にあり触れないため、ここを起点にします。
===
呼吸器系のフィジカルアセスメント | 問診・視診 | 標準（基本知識） | 0
Q: 起座呼吸は、横になると苦しく座ると楽になる状態で、心不全や喘息などが原因で起こる。
E: その通りです。横になると静脈還流量が増えて肺うっ血が悪化するため、起き上がることで呼吸が楽になります。
===
呼吸器系のフィジカルアセスメント | 触診・打診 | 標準（基本知識） | 0
Q: 背部での胸郭の可動性の触診において、正常であれば深吸気時に左右の母指の間は約4cm広がる。
E: その通りです。第10肋骨付近で左右の母指を合わせ、深吸気で対称に約4cm広がるのが正常な所見です。
===
呼吸器系のフィジカルアセスメント | 聴診 | 標準（基本知識） | 0
Q: ストライダーは吸気時に聴こえる高調性の「ゼーゼー」という音で、上気道の狭窄を示す緊急所見である。
E: その通りです。喉頭浮腫や気道異物などによる上気道の狭窄を示しており、迅速な対応が必要です。
===
循環器系のフィジカルアセスメント | 解剖・生理 | 標準（基本知識） | 1
Q: 心房と心室の間にある房室弁は、右心系が僧帽弁、左心系が三尖弁である。
E: 右心系の房室弁は「三尖弁」、左心系の房室弁は「僧帽弁」です。弁は血液の逆流を防いでいます。
===
循環器系のフィジカルアセスメント | 刺激伝導系と心電図 | 標準（基本知識） | 0
Q: 心電図の基本波形において、P波は心房の興奮（収縮）を、QRS波は心室の興奮（収縮）を意味する。
E: その通りです。T波は心室の興奮からの回復を意味し、1拍ごとにP→QRS→Tの順に繰り返します。
===
循環器系のフィジカルアセスメント | 問診・視診・触診 | 標準（基本知識） | 0
Q: 心尖拍動は心尖部を「手掌と指腹」で触れ、振戦（スリル）は4つの弁の領域を「指のつけ根」で触れる。
E: その通りです。触診の前には目的と方法を説明して同意を得て、手を温めておきます。
===
循環器系のフィジカルアセスメント | 聴診 | 標準（基本知識） | 0
Q: 心音のⅠ音は僧帽弁・三尖弁（房室弁）が閉じる音であり、収縮期の始まりを示す。
E: その通りです。Ⅱ音は大動脈弁・肺動脈弁（動脈弁）が閉じる音で、拡張期の始まりを示します。
===
循環器系のフィジカルアセスメント | 末梢動脈・静脈 | 標準（基本知識） | 0
Q: 爪床圧迫テスト（CRT）は、爪床を圧迫して解除した後、赤みが2秒以内に戻れば正常である。
E: その通りです。2秒以上かかる場合はショックや脱水などの末梢循環不全を疑います（アレンテストは5秒以内が正常）。
===
循環器系のフィジカルアセスメント | 浮腫と心不全 | 標準（基本知識） | 0
Q: 左心不全では肺うっ血が生じるため、主な症状として呼吸困難や起座呼吸がみられ、聴診では肺の水泡音が聴かれる。
E: その通りです。一方、右心不全では全身の静脈に体うっ血が生じ、下腿の圧痕性浮腫や体重増加などがみられます。
===
脳神経系のフィジカルアセスメント | 中枢・末梢神経の構造と機能 | 標準（基本知識） | 0
Q: 脳幹は中脳・橋・延髄からなり、特に延髄には呼吸中枢や循環中枢、嚥下中枢など生命維持に不可欠な中枢が存在する。
E: その通りです。また、脳幹は大脳皮質と連絡して覚醒状態の維持にも関与しています。
===
脳神経系のフィジカルアセスメント | 脳神経（12対）の解剖と機能 | 標準（基本知識） | 0
Q: 脳神経核の位置において、中脳には動眼神経（Ⅲ）・滑車神経（Ⅳ）があり、橋には三叉神経（Ⅴ）・外転神経（Ⅵ）・顔面神経（Ⅶ）・内耳神経（Ⅷ）がある。
E: その通りです。なお、嗅神経（Ⅰ）・視神経（Ⅱ）は脳幹より上にあり、舌咽（Ⅸ）・迷走（Ⅹ）・副（Ⅺ）・舌下神経（Ⅻ）は延髄にあります。
===
脳神経系のフィジカルアセスメント | 意識状態と高次脳機能 | 標準（基本知識） | 1
Q: JCS（ジャパン・コーマ・スケール）は点数が低いほど意識状態が悪く、GCS（グラスゴー・コーマ・スケール）は点数が高いほど意識状態が悪い。
E: 逆です。JCSは数値が大きいほど状態が悪く、GCS（E・V・Mの合計）は点数が低いほど状態が悪くなります。
===
脳神経系のフィジカルアセスメント | 視覚・眼球運動・瞳孔・対光反射 | 標準（基本知識） | 0
Q: 正常な瞳孔径は自然光のもとで2.5〜4.0mmの正円であり、2mm以下を「縮瞳」、5mm以上を「散瞳」、左右差が0.5mm以上ある場合を「瞳孔不同」という。
E: その通りです。対光反射では直接対光反射と間接対光反射の両方を観察します。
===
脳神経系のフィジカルアセスメント | 三叉・顔面・内耳・舌下神経 | 標準（基本知識） | 0
Q: 顔面神経（Ⅶ）のアセスメントでは、額のしわ寄せ（前頭筋）、閉眼（眼輪筋）、「イーッ」と歯を見せる口角挙上（口輪筋）を観察するが、中枢性の顔面麻痺では前頭筋は障害されない。
E: その通りです。末梢性の顔面神経麻痺では3つすべてが障害されますが、中枢性では額のしわ寄せは保たれます。
===
筋骨格系のフィジカルアセスメント | 筋の基礎知識 | 標準（基本知識） | 0
Q: 骨格筋は横紋筋であり自分の意思で動かせる随意筋であるが、心筋は横紋筋でありながら不随意筋である。
E: その通りです。なお、消化管や血管の壁にある平滑筋（瞳孔散大筋なども含む）は不随意筋です。
===
筋骨格系のフィジカルアセスメント | 骨格系と関節運動 | 標準（基本知識） | 0
Q: 前腕を軸とした回旋運動において、手掌を上に向ける動きを「回外」、手掌を下に向ける動きを「回内」という。
E: その通りです。また、体幹から遠ざかる動きを外転、近づく動きを内転といいます。
===
筋骨格系のフィジカルアセスメント | ROMとMMT | 標準（基本知識） | 1
Q: 徒手筋力検査（MMT）において、スコア「3（Fair）」は「重力を除去すれば全可動域の運動が可能」な状態を指す。
E: 重力を除去すれば全可動域の運動が可能なのは「MMT 2（Poor）」です。「MMT 3」は抵抗なしで重力に反して動かせる状態です。
===
筋骨格系のフィジカルアセスメント | 運動の実行・調節と麻痺 | 標準（基本知識） | 0
Q: 上肢のバレー徴候の観察では、手掌を上に向けて両腕を前方水平に挙上してもらい、閉眼して20秒間保持できるかを観察する。
E: その通りです。錐体路障害（運動麻痺）があると、麻痺側の肘の屈曲、前腕の回内、腕の下降がみられます。
===
筋骨格系のフィジカルアセスメント | 運動失調・不随意運動とアセスメント | 標準（基本知識） | 0
Q: 小脳の障害による運動失調では、目標に向かって手を伸ばすとき、目標に近づくほど手の震えが大きくなる「企図振戦」がみられる。
E: その通りです。小脳障害では他に、歩行失調、測定障害、反復拮抗運動不能などがみられます。
===
消化器系のフィジカルアセスメント | 解剖と機能 | 標準（基本知識） | 0
Q: 胃は横隔膜の下、左上腹部に位置しており、食道との境界を「噴門」、十二指腸との境界を「幽門」という。
E: その通りです。食物を胃壁の蠕動運動で粉砕して胃液と混和させ、徐々に十二指腸へ送ります。
===
消化器系のフィジカルアセスメント | 症状・徴候と問診 | 標準（基本知識） | 0
Q: 急性虫垂炎の典型例では、初期には心窩部痛（内臓痛・関連痛）が生じ、進行期には炎症が壁側腹膜に及んで右下腹部痛（体性痛）へと変化する。
E: その通りです。痛みの部位と性質が経過とともに移動するのが特徴です。
===
消化器系のフィジカルアセスメント | 視診と全体的な進め方 | 標準（基本知識） | 0
Q: 腹部のアセスメントは、打診や触診の刺激が腸蠕動に影響するのを防ぐため、「視診→聴診→打診→触診」の順番で行う。
E: その通りです。また、痛みのある部位は腹壁の緊張や苦痛を防ぐため一番最後に観察します。
===
消化器系のフィジカルアセスメント | 聴診と打診 | 標準（基本知識） | 0
Q: 腸蠕動音は腹部全体に伝わるため腹壁の1か所に膜型聴診器を当てて聴取し、1分間聴取されない場合は「減弱」、5分間聴取されない場合は「消失」と判断する。
E: その通りです。また、腹部の打診では大部分で「鼓音」が聴取され、実質臓器や便塊・腹水貯留部では「濁音」が聴取されます。
===
消化器系のフィジカルアセスメント | 触診 | 標準（基本知識） | 0
Q: 腹壁をゆっくり圧迫し、急に手を離した瞬間に疼痛が増強する現象を「反跳痛（Blumberg徴候）」といい、筋性防御とともに腹膜炎（腹膜刺激症状）を示すサインである。
E: その通りです。なお、急性虫垂炎の圧痛点にはMcBurney点（右上前腸骨棘と臍を結ぶ線の外側1/3）とLanz点（左右の上前腸骨棘を結ぶ線の右側1/3）があります。
===
看護過程 | 看護過程の概要と情報収集 | 標準（基本知識） | 1
Q: 看護過程は、①アセスメント、②看護問題の明確化、③計画立案、④実施、⑤評価の5段階からなり、一方向で完結する直線的なプロセスである。
E: 看護過程は一方向で終わるのではなく、評価から再びアセスメントや計画修正へ戻る「連続的かつ循環的なプロセス」です。
===
看護過程 | ゴードンの機能的健康パターン | 標準（基本知識） | 1
Q: ゴードンの機能的健康パターンにおいて、BMIや血清アルブミン値（Alb）、褥瘡や浮腫の有無は「活動―運動パターン」に分類される。
E: 栄養状態（BMI、TP、Albなど）や皮膚状態（創傷、褥瘡、浮腫）、免疫状態は「栄養―代謝パターン」でアセスメントします。
===
看護過程 | 情報の分析と関連図 | 標準（基本知識） | 0
Q: 人間の反応の分析において、反応を引き起こす直接の要因を「原因」といい、人間の反応を維持・改善・促進できる因子を「強み」という。
E: その通りです。患者の「強み」は看護計画の介入方法で活かすことができ、個別性のある看護につながります。
===
看護過程 | 看護問題の明確化と看護診断 | 標準（基本知識） | 1
Q: 看護問題の優先順位を決定する際、マズローの基本的欲求階層説では「安全のニード」が最優先される。
E: 最優先されるのは生命維持に関わる「生理的ニード」です。その次に「安全のニード（痛みや不安も含む）」が続きます。
===
看護過程 | 看護計画の立案 | 標準（基本知識） | 0
Q: 看護計画は看護問題ごとに、観察計画（OP）、直接ケア計画（TPまたはCP）、教育計画（EP）の3つに分けて立案する。
E: その通りです。また、期待される成果（目標）の主語は必ず「患者」にします。
===
看護過程 | 実施・評価と看護記録 | 標準（基本知識） | 1
Q: SOAP形式による経過記録において、「A（アセスメント）」には観察した客観的事実や実施したケアの手順のみを記録する。
E: 観察した事実や実施した介入内容は「O（客観的情報）」に記録します。「A」には変化や達成度に関する考察を記録します。
"""


# ==========================================
# 6. 問題バンクの統合読み込み（ファイル ＋ Firestore既存問題 ＋ 初期問題）
# ==========================================
@st.cache_data(ttl=300, show_spinner=False)
def _fetch_db_questions():
    """Firestoreの既存問題（および過去IDとqkeyの対応）を取得する。"""
    if not DB_AVAILABLE:
        return []
    out = []
    try:
        for doc in db.collection(QUESTIONS_COLLECTION).stream():
            d = doc.to_dict() or {}
            d["id"] = doc.id
            if not d.get("qkey") and d.get("question"):
                d["qkey"] = normalize_text(d["question"])
            out.append(d)
    except Exception:
        pass
    return out


def load_static_files():
    """ディレクトリ内の問題テキストファイルをすべて読み込む。"""
    loaded = []
    checked_paths = set()

    # 1. 共通バンクファイル
    for fname in COMMON_BANK_FILES:
        path = os.path.join(APP_DIR, fname)
        if os.path.exists(path):
            checked_paths.add(path)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded.extend(parse_question_blocks(f.read()))
            except Exception:
                pass

    # 2. 領域別バンクファイル
    for domain, fnames in BANK_FILES_MAP.items():
        for fname in fnames:
            path = os.path.join(APP_DIR, fname)
            if path in checked_paths or not os.path.exists(path):
                continue
            checked_paths.add(path)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded.extend(parse_question_blocks(f.read(), default_domain=domain))
            except Exception:
                pass

    return loaded


def get_master_bank():
    """静的ファイル・内蔵問題・DB上の承認済み問題を統合し、重複を除いた問題バンクを返す。"""
    if "master_bank" in st.session_state and st.session_state.master_bank is not None:
        return st.session_state.master_bank, st.session_state.id_to_qkey

    db_items = _fetch_db_questions()
    id_to_qkey = {}
    db_qkey_to_doc = {}
    for d in db_items:
        qk = d.get("qkey") or normalize_text(d.get("question", ""))
        if qk:
            id_to_qkey[d["id"]] = qk
            id_to_qkey[qk] = qk
            db_qkey_to_doc[qk] = d

    static_items = load_static_files()
    seed_items = parse_question_blocks(SEED_RAW_TEXT)

    merged = []
    seen_qkeys = set()

    # (A) 静的テキストファイルの問題を最優先で登録
    for item in static_items + seed_items:
        qk = item["qkey"]
        if not qk or qk in seen_qkeys:
            continue
        # DB側にすでに同じ問題があれば、既存のFirestore IDを引き継ぐ（seen_ids完全互換のため）
        if qk in db_qkey_to_doc:
            db_doc = db_qkey_to_doc[qk]
            if db_doc.get("status") == "rejected":
                continue
            item["id"] = db_doc["id"]
        seen_qkeys.add(qk)
        id_to_qkey[item["id"]] = qk
        id_to_qkey[qk] = qk
        merged.append(item)

    # (B) 過去にFirestore上に保存された承認済み問題も合流
    for d in db_items:
        if d.get("status") != "approved":
            continue
        qk = d.get("qkey") or normalize_text(d.get("question", ""))
        if not qk or qk in seen_qkeys:
            continue
        seen_qkeys.add(qk)
        merged.append({
            "id": d["id"],
            "qkey": qk,
            "domain": d.get("domain", ""),
            "sub_domain": d.get("sub_domain", ""),
            "difficulty": d.get("difficulty", "標準（基本知識）"),
            "question": clean_source_text(d.get("question", "")),
            "options": ["○", "×"],
            "answer_index": int(d.get("answer_index", 0)),
            "explanation": clean_source_text(d.get("explanation", "")),
            "status": "approved",
            "source": d.get("source", "db"),
        })

    st.session_state.master_bank = merged
    st.session_state.id_to_qkey = id_to_qkey
    return merged, id_to_qkey


def reload_bank():
    try:
        _fetch_db_questions.clear()
    except Exception:
        pass
    st.session_state.master_bank = None
    st.session_state.id_to_qkey = None


def filter_questions(bank, domain, sub_domain="すべて", difficulty="すべて"):
    out = []
    for q in bank:
        if q.get("domain") != domain:
            continue
        if sub_domain and sub_domain != "すべて" and q.get("sub_domain") != sub_domain:
            continue
        if difficulty and difficulty != "すべて" and q.get("difficulty") != difficulty:
            continue
        out.append(q)
    return out


def resolve_seen_qkeys(seen_ids, id_to_qkey):
    """学生の seen_ids（旧Firestore ID または qkey）をすべて qkey の集合に変換する。"""
    s = set(st.session_state.session_keys)
    for sid in seen_ids:
        if sid in id_to_qkey:
            s.add(id_to_qkey[sid])
        elif str(sid).startswith("static_"):
            s.add(str(sid)[7:])
        else:
            s.add(str(sid))
    return s


# ==========================================
# 7. セッション状態と出題ロジック
# ==========================================
DEFAULTS = {
    "current_question": None,
    "answered": False,
    "student_id": "",
    "loaded_student_id": "",
    "user_data": None,
    "is_correct": False,
    "session_keys": [],
    "q_counter": 0,
    "pending_next": False,
    "master_bank": None,
    "id_to_qkey": None,
    "cycle_notice": "",
}
for _k, _v in DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def advance_to_next_question(domain, sub_domain, difficulty, seen_ids, user_ref):
    bank, id_to_qkey = get_master_bank()

    # 難易度一致の候補を探す（もしその難易度に問題がなければ難易度フィルタを外す）
    cands = filter_questions(bank, domain, sub_domain, difficulty)
    if not cands and difficulty != "すべて":
        cands = filter_questions(bank, domain, sub_domain, "すべて")

    if not cands:
        return False, "この範囲の問題はまだ登録されていません。別の範囲を選択してください。"

    seen_qkeys = resolve_seen_qkeys(seen_ids, id_to_qkey)
    unseen = [q for q in cands if q["qkey"] not in seen_qkeys]

    st.session_state.cycle_notice = ""
    if not unseen:
        # この範囲の全問題を解き切った場合は、この範囲の履歴のみリセットして2周目に入る
        scope_qkeys = {q["qkey"] for q in cands}
        scope_ids = {q["id"] for q in cands}
        st.session_state.session_keys = [
            k for k in st.session_state.session_keys if k not in scope_qkeys
        ]
        if st.session_state.user_data is not None:
            old_seen = st.session_state.user_data.get("seen_ids") or []
            new_seen = [
                sid for sid in old_seen
                if sid not in scope_ids
                and sid not in scope_qkeys
                and id_to_qkey.get(sid) not in scope_qkeys
            ]
            st.session_state.user_data["seen_ids"] = new_seen
            if user_ref is not None:
                try:
                    user_ref.set({"seen_ids": new_seen}, merge=True)
                except Exception:
                    pass
        unseen = list(cands)
        st.session_state.cycle_notice = (
            f"🎉 この出題範囲（全 {len(cands)} 問）を一通り解き切りました！2周目（復習サイクル）に入ります。"
        )

    chosen = random.choice(unseen)
    st.session_state.current_question = chosen
    st.session_state.answered = False
    st.session_state.is_correct = False
    st.session_state.q_counter += 1
    st.session_state.session_keys.append(chosen["qkey"])

    # 解答済みIDの記録（既存のseen_idsリストと完全互換）
    record_id = chosen["id"]
    if st.session_state.user_data is not None:
        seen_list = st.session_state.user_data.setdefault("seen_ids", [])
        if record_id not in seen_list:
            seen_list.append(record_id)
    if user_ref is not None:
        try:
            user_ref.set({"seen_ids": firestore.ArrayUnion([record_id])}, merge=True)
        except Exception:
            pass

    return True, ""


def count_scope_progress(domain, sub_domain, difficulty, seen_ids):
    bank, id_to_qkey = get_master_bank()
    cands = filter_questions(bank, domain, sub_domain, difficulty)
    if not cands and difficulty != "すべて":
        cands = filter_questions(bank, domain, sub_domain, "すべて")
    total = len(cands)
    if total == 0:
        return 0, 0
    seen_qkeys = resolve_seen_qkeys(seen_ids, id_to_qkey)
    unseen_cnt = sum(1 for q in cands if q["qkey"] not in seen_qkeys)
    return unseen_cnt, total


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


# ==========================================
# 8. UI描画
# ==========================================
mode = st.sidebar.radio("モード選択", ["学生用（クイズ演習）", "管理者用（モニタリング）"])

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
    user_ref = db.collection("students").document(student_id) if DB_AVAILABLE else None

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

    if st.session_state.pending_next:
        st.session_state.pending_next = False
        next_clicked = True

    if next_clicked:
        ok, msg = advance_to_next_question(
            domain, sub_domain, difficulty, seen_ids, user_ref
        )
        if not ok:
            st.warning(f"⚠️ {msg}")

    if st.session_state.cycle_notice:
        st.info(st.session_state.cycle_notice)

    if st.session_state.current_question:
        q = st.session_state.current_question
        st.caption(
            f"領域: {q.get('domain', '')} ＞ 範囲: {q.get('sub_domain', '')} ｜ 難易度: {q.get('difficulty', '')}"
        )
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
                            payload = {
                                k: v for k, v in user_data.items() if k != "seen_ids"
                            }
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

            unseen_cnt, total_cnt = count_scope_progress(
                domain, sub_domain, difficulty, user_data.get("seen_ids") or []
            )
            if total_cnt > 0:
                st.caption(
                    f"この範囲の未出題ストック: あと {unseen_cnt} / 全 {total_cnt} 問（待ち時間ゼロで出題されます）"
                )

# ------------------------------------------------------------ 管理者画面
elif mode == "管理者用（モニタリング）":
    st.title("📊 管理者画面")

    admin_password = get_secret("ADMIN_PASSWORD", "admin1234")
    admin_pass = st.text_input("管理者パスワードを入力してください", type="password")
    if admin_pass != admin_password:
        st.info("正しいパスワードを入力すると管理機能が表示されます。")
        if admin_password == "admin1234":
            st.caption(
                "※ 現在は初期パスワードのままです。Streamlit の Secrets に "
                "ADMIN_PASSWORD を設定して変更してください。"
            )
        st.stop()

    tab_scores, tab_bank = st.tabs(["📈 成績モニタリング", "📚 問題バンク登録状況"])

    # --- 成績モニタリング -------------------------------------------------
    with tab_scores:
        if not DB_AVAILABLE:
            st.error("データベースに接続できないため、成績データを取得できません。")
            st.code(DB_ERROR or "不明", language="text")
        else:
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
                if records:
                    records.sort(key=lambda x: x["総解答数"], reverse=True)
                    st.dataframe(records, use_container_width=True, hide_index=True)
                else:
                    st.write("まだ演習に取り組んだ学生のデータはありません。")
            except Exception as e:
                st.error(f"成績データの取得に失敗しました: {type(e).__name__}: {e}")

    # --- 問題バンク登録状況 -----------------------------------------------
    with tab_bank:
        col_title, col_btn = st.columns([4, 1])
        with col_title:
            st.subheader("領域・出題範囲ごとの問題数一覧")
        with col_btn:
            if st.button("🔄 ファイルを再読込"):
                reload_bank()
                st.rerun()

        bank, _ = get_master_bank()
        st.caption(f"現在読み込まれている総問題数: **{len(bank)} 問**")

        b_domain = st.selectbox("確認する領域", AVAILABLE_DOMAINS, key="admin_domain")
        summary_rows = []
        for sd in SUB_DOMAINS.get(b_domain, ["すべて"]):
            if sd == "すべて":
                continue
            items_sd = filter_questions(bank, b_domain, sd, "すべて")
            std_cnt = sum(1 for q in items_sd if "標準" in q.get("difficulty", ""))
            app_cnt = sum(1 for q in items_sd if "応用" in q.get("difficulty", ""))
            summary_rows.append({
                "出題範囲": sd,
                "合計問題数": len(items_sd),
                "標準（基本知識）": std_cnt,
                "応用（病態との関連・臨床的判断）": app_cnt,
                "○（正）の数": sum(1 for q in items_sd if q.get("answer_index") == 0),
                "×（誤）の数": sum(1 for q in items_sd if q.get("answer_index") == 1),
            })
        st.dataframe(summary_rows, use_container_width=True, hide_index=True)

        with st.expander("問題一覧を確認する"):
            b_sub = st.selectbox(
                "出題範囲で絞り込む", SUB_DOMAINS.get(b_domain, ["すべて"]), key="admin_sub"
            )
            preview_items = filter_questions(bank, b_domain, b_sub, "すべて")
            st.write(f"該当: {len(preview_items)} 問")
            for idx, item in enumerate(preview_items, 1):
                ans_mark = "○" if item["answer_index"] == 0 else "×"
                st.markdown(
                    f"**Q{idx}. [{item['sub_domain']} / {item['difficulty']}]** "
                    f"{item['question']}  \n"
                    f"👉 **正解: {ans_mark}** ｜ {item['explanation']}"
                )
                st.divider()
