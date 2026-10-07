import streamlit as st
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
import json
import os
import random

st.set_page_config(page_title="看護学生向けフィジカルアセスメント 無限問題集", layout="wide")

# ★ラジオボタンの文字サイズを問題文(H3)と同じ大きさに拡大するCSS
st.markdown("""
<style>
div[role="radiogroup"] p {
    font-size: 1.5rem !important;
    font-weight: bold !important;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 1. 初期設定（APIキー・Firebase接続）
# ==========================================
if "GEMINI_API_KEY" in st.secrets:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
else:
    GEMINI_API_KEY = ""

genai.configure(api_key=GEMINI_API_KEY)

if not firebase_admin._apps:
    if "FIREBASE_JSON" in st.secrets:
        cred = credentials.Certificate(json.loads(st.secrets["FIREBASE_JSON"]))
    else:
        cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)
db = firestore.client()

# ==========================================
# 2. 知識データベース（即時出題バンク）
# ==========================================
PRE_MADE_QUESTIONS = [
    {"sub_domain": "解剖・生理", "question": "右主気管支は左主気管支よりも細く長いため、誤嚥性肺炎は左肺に起こりやすい。", "options": ["○", "×"], "answer_index": 1, "explanation": "右主気管支の方が「太く短く」、分岐角度が小さい（約25度）ため、誤嚥したものは右に入りやすく、誤嚥性肺炎は右肺に起こりやすいです。"},
    {"sub_domain": "解剖・生理", "question": "腹式呼吸は安静時の呼吸の大部分を担い、胸式呼吸よりも1回換気量が多くなる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。横隔膜の収縮による腹式呼吸は安静時の主な呼吸であり、胸式呼吸よりも多くの換気量を得られます。"},
    {"sub_domain": "解剖・生理", "question": "背面から見える肺の大部分は上葉であるため、背部の観察では上葉を意識することが重要である。", "options": ["○", "×"], "answer_index": 1, "explanation": "背面から見える肺の大部分は「下葉」です。そのため、背部の聴診や打診では主に下葉の状態を観察しています。"},
    {"sub_domain": "解剖・生理", "question": "PaO2（動脈血酸素分圧）が60Torr以下、またはSpO2が約90%の状態は呼吸不全を意味する。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。PaO2の基準値は80〜100Torrであり、60Torrを下回る（SpO2約90%以下）と呼吸不全と定義されます。"},
    {"sub_domain": "体表解剖", "question": "胸骨角（ルイ角）は第1肋骨の付着部であり、肋骨・肋間を数える起点となる。", "options": ["○", "×"], "answer_index": 1, "explanation": "胸骨角は「第2肋骨」の付着部です。第1肋骨は鎖骨の下にあり触れないため、ここを起点として肋骨を数えます。"},
    {"sub_domain": "問診・視診", "question": "起座呼吸は、横になると苦しく座ると楽になる状態で、心不全や喘息などが原因で起こる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。心不全などで肺にうっ血がある場合、横になると静脈還流量が増えて呼吸が苦しくなるため、起き上がることで呼吸を楽にしようとします。"},
    {"sub_domain": "問診・視診", "question": "チェーンストークス呼吸は、深く大きな規則的な呼吸が続く異常呼吸であり、糖尿病性ケトアシドーシスでみられる。", "options": ["○", "×"], "answer_index": 1, "explanation": "それは「クスマウル呼吸」の説明です。チェーンストークス呼吸は、無呼吸と深浅の周期を繰り返す呼吸で、心不全や脳障害などでみられます。"},
    {"sub_domain": "問診・視診", "question": "ブリンクマン指数は「1日の喫煙本数×喫煙年数」で計算され、400以上で肺がん危険群とされる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。600以上で高度危険群とされ、現在の喫煙状況にかかわらず過去の喫煙歴の問診が重要です。"},
    {"sub_domain": "問診・視診", "question": "チアノーゼは口唇や爪床が青紫色になるサインであるが、貧血の患者では現れにくい。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。チアノーゼは還元ヘモグロビンが一定量以上増えることで現れるため、もともとヘモグロビンが少ない貧血状態では出現しにくくなります。"},
    {"sub_domain": "触診・打診", "question": "背部での胸郭の可動性の触診において、正常であれば深吸気時に左右の母指の間は約4cm広がる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。第10肋骨付近で左右の母指を合わせ、深吸気で対称に約4cm広がるのが正常な所見です。"},
    {"sub_domain": "触診・打診", "question": "肺炎や肺水腫などで肺組織が硬化すると、声音振盪は減弱または消失する。", "options": ["○", "×"], "answer_index": 1, "explanation": "肺組織が硬化（水分の貯留など）すると、音の振動を伝えやすくなるため声音振盪は「増強（亢進）」します。減弱するのは気胸やCOPDなどです。"},
    {"sub_domain": "触診・打診", "question": "正常な肺野を打診すると、鈍くつまったような濁音が聴こえる。", "options": ["○", "×"], "answer_index": 1, "explanation": "正常な肺野は空気を多く含むため、響きのある低い「共鳴音（清音）」が聴こえます。濁音は肺炎や胸水貯留などで聴こえます。"},
    {"sub_domain": "触診・打診", "question": "気胸やCOPDなど、胸郭内の含気量が増えている部位を打診すると過共鳴音となる。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。空気が過剰に溜まっている状態では、正常な共鳴音よりもさらに響く過共鳴音や鼓音が聴取されます。"},
    {"sub_domain": "聴診", "question": "捻髪音（細かい断続性副雑音）は「ブクブク」と聴こえ、気道内に分泌物が溜まっている状態で聴取される。", "options": ["○", "×"], "answer_index": 1, "explanation": "分泌物が溜まっている状態で聴こえる「ブクブク」という音は「水泡音（粗い断続性副雑音）」です。捻髪音は「パリパリ」と聴こえ、間質性肺炎などで聴取されます。"},
    {"sub_domain": "聴診", "question": "ストライダーは吸気時に聴こえる高調性の「ゼーゼー」という音で、上気道の狭窄を示す緊急所見である。", "options": ["○", "×"], "answer_index": 0, "explanation": "その通りです。喉頭浮腫や気道異物などによる上気道の狭窄を示しており、窒息の恐れがあるため迅速な対応が必要です。"}
]

@st.cache_data
def load_knowledge_base(domain):
    if domain == "呼吸器系のフィジカルアセスメント":
        file_path = "kokyu.txt"
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
    return "準備中"

AVAILABLE_DOMAINS = [
    "呼吸器系のフィジカルアセスメント",
    "循環器系のフィジカルアセスメント",
    "脳神経系のフィジカルアセスメント"
]

# ==========================================
# 3. セッション管理・AI問題生成ロジック
# ==========================================
if "current_question" not in st.session_state:
    st.session_state.current_question = None
if "answered" not in st.session_state:
    st.session_state.answered = False
if "student_id" not in st.session_state:
    st.session_state.student_id = ""
if "is_correct" not in st.session_state:
    st.session_state.is_correct = False
if "question_pool" not in st.session_state:
    st.session_state.question_pool = []
if "used_pre_made" not in st.session_state:
    st.session_state.used_pre_made = []
if "last_settings" not in st.session_state:
    st.session_state.last_settings = {}
# ★ ラジオボタンを確実にリセットするための固有キー（通し番号）
if "q_key" not in st.session_state:
    st.session_state.q_key = 0

def generate_quiz_via_ai(domain, sub_domain, difficulty):
    source_text = load_knowledge_base(domain)
    model = genai.GenerativeModel("gemini-3.8-flash")
    prompt = f"""
    あなたは優秀な看護教育の専門家です。以下の【参考資料】に記載されている事実のみに基づいて、○×問題（正誤問題）をまとめて「3問」作成してください。
    外部の知識は絶対に混ぜないでください。

    【条件】
    - 出題領域: {domain}
    - 出題範囲: {sub_domain}（※「すべて」以外が指定された場合は該当内容を重点的に出題）
    - 難易度: {difficulty}
    - 選択肢は必ず「○」と「×」の2つだけにしてください。
    - 解説は要点を絞って短く簡潔に（2〜3文程度で）作成してください。

    【出力フォーマット】（JSON形式の配列のみ）
    [
      {{
        "question": "1問目の問題文",
        "options": ["○", "×"],
        "answer_index": 正解の番号(0または1),
        "explanation": "1問目の解説文"
      }},
      {{
        "question": "2問目の問題文",
        "options": ["○", "×"],
        "answer_index": 正解の番号(0または1),
        "explanation": "2問目の解説文"
      }},
      {{
        "question": "3問目の問題文",
        "options": ["○", "×"],
        "answer_index": 正解の番号(0または1),
        "explanation": "3問目の解説文"
      }}
    ]

    【参考資料】
    {source_text}
    """
    response = model.generate_content(prompt)
    clean_text = response.text.strip().replace("```json", "").replace("```", "")
    return json.loads(clean_text)

# ==========================================
# 4. UI（画面構成）
# ==========================================
mode = st.sidebar.radio("モード選択", ["学生用（クイズ演習）", "管理者用（モニタリング）"])

if mode == "学生用（クイズ演習）":
    st.title("🩺 フィジカルアセスメント 無限問題集")

    col_id, col_name = st.columns([1, 1])
    with col_id:
        student_id = st.text_input("学籍番号を入力してください（半角数字7桁）", value=st.session_state.student_id)
    with col_name:
        student_name = st.text_input("氏名（またはニックネーム）")

    # ★ 学籍番号が入力されていない場合のストップ処理
    if not student_id:
        st.warning("演習を開始するには学籍番号を入力してください。")
        st.stop()
        
    # ★ 学籍番号が「7文字」かつ「すべて数字」かどうかの厳密なチェック
    if not (len(student_id) == 7 and student_id.isdigit()):
        st.error("エラー：学籍番号は「半角数字7桁」で入力してください。")
        st.stop()

    st.session_state.student_id = student_id
    user_ref = db.collection("students").document(student_id)
    user_doc = user_ref.get()

    if user_doc.exists:
        user_data = user_doc.to_dict()
    else:
        user_data = {"name": student_name, "current_streak": 0, "max_streak": 0, "has_title": False, "total_answers": 0, "correct_answers": 0}
        user_ref.set(user_data)

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
        else:
            sub_domain = st.selectbox("出題範囲", ["すべて"])
    with c3:
        difficulty = st.selectbox("難易度", ["標準（基本知識）", "応用（病態との関連・臨床的判断）"])
    with c4:
        st.write("")
        st.write("")
        
        if st.button("次の問題を生成する 🎲", use_container_width=True):
            source_text = load_knowledge_base(domain)
            if source_text == "準備中":
                st.error("この領域の問題は現在準備中です。別の領域を選択してください。")
            elif source_text == "エラー":
                st.error("エラー：kokyu.txt ファイルが見つかりません。")
            else:
                current_settings = {"domain": domain, "sub_domain": sub_domain, "difficulty": difficulty}
                if st.session_state.last_settings != current_settings:
                    st.session_state.question_pool = []
                    st.session_state.used_pre_made = []
                    st.session_state.last_settings = current_settings
                
                available_pre_made = [
                    q for q in PRE_MADE_QUESTIONS
                    if (sub_domain == "すべて" or q["sub_domain"] == sub_domain)
                    and q["question"] not in st.session_state.used_pre_made
                ]

                # ★ 新しい問題を生成する際に状態をリセットし、ラジオボタンの固有キーをカウントアップする
                st.session_state.answered = False
                st.session_state.is_correct = False
                st.session_state.q_key += 1

                if available_pre_made:
                    selected_q = random.choice(available_pre_made)
                    st.session_state.used_pre_made.append(selected_q["question"])
                    st.session_state.current_question = {
                        "question": selected_q["question"],
                        "options": selected_q["options"],
                        "answer_index": selected_q["answer_index"],
                        "explanation": selected_q["explanation"]
                    }
                else:
                    if not st.session_state.question_pool:
                        with st.spinner("AIが新しい問題セットを作成中...（初回のみ数秒かかります）"):
                            try:
                                new_questions = generate_quiz_via_ai(domain, sub_domain, difficulty)
                                if isinstance(new_questions, list):
                                    st.session_state.question_pool = new_questions
                                else:
                                    st.session_state.question_pool = [new_questions]
                            except Exception as e:
                                st.error("問題の生成に失敗しました。もう一度ボタンを押してください。")

                    if st.session_state.question_pool:
                        st.session_state.current_question = st.session_state.question_pool.pop(0)

    if st.session_state.current_question:
        q = st.session_state.current_question
        st.markdown(f"### Q. {q['question']}")
        
        is_disabled = st.session_state.answered
        
        # ★ 「選択肢を選んでください」の文字を問題文と同サイズ(H3)に拡大
        st.markdown("### 選択肢を選んでください：")
        
        # ★ ラジオボタン自体のラベルは隠し、固有のキー(q_key)を割り当てることで確実にブランク状態にする
        choice = st.radio("選択肢", q["options"], key=f"quiz_choice_{st.session_state.q_key}", index=None, disabled=is_disabled, horizontal=True, label_visibility="collapsed")

        if not is_disabled:
            if st.button("回答を送信する") and choice:
                st.session_state.answered = True
                selected_idx = q["options"].index(choice)
                is_correct = (selected_idx == q["answer_index"])
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

                user_ref.set(user_data, merge=True)
                st.rerun() 
        else:
            if st.session_state.is_correct:
                st.success("🎉 正解です！")
            else:
                st.error("❌ 不正解です...")

            st.write("---")
            st.info(f"**【解説】**\n{q.get('explanation', '')}")
            st.write("---")
            pool_count = len(st.session_state.question_pool)
            if pool_count > 0:
                st.warning(f"⬆️ 画面上部の「次の問題を生成する 🎲」ボタンを押して進んでください。（あと {pool_count} 問は【待ち時間ゼロ】で即座に出題されます！）")
            else:
                st.warning("⬆️ 画面上部の「次の問題を生成する 🎲」ボタンを押して進んでください。（次は新しい問題セットを作成するため数秒かかります）")

elif mode == "管理者用（モニタリング）":
    st.title("📊 学生モニタリングダッシュボード")
    
    admin_pass = st.text_input("管理者パスワードを入力してください", type="password")
    if admin_pass != "admin1234":
        st.info("正しいパスワードを入力すると成績データが表示されます。")
        st.stop()

    students = db.collection("students").stream()
    records = []
    for s in students:
        d = s.to_dict()
        records.append({
            "学籍番号": s.id,
            "氏名": d.get("name", "未設定"),
            "総解答数": d.get("total_answers", 0),
            "正答数": d.get("correct_answers", 0),
            "正答率(%)": round((d.get("correct_answers", 0) / d.get("total_answers", 1)) * 100, 1) if d.get("total_answers", 0) > 0 else 0,
            "最大連続正解数": d.get("max_streak", 0),
            "称号獲得": "達成 🏆" if d.get("has_title", False) else "未達成"
        })

    if records:
        st.dataframe(records, use_container_width=True)
    else:
        st.write("まだ演習に取り組んだ学生のデータはありません。")
