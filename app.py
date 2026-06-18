from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, Response)
from datetime import datetime, timezone, timedelta
import os
import json
import anthropic

from database import init_db, get_connection

app = Flask(__name__)

# gunicorn(Railway)では __main__ が実行されないため、モジュールロード時に init_db
init_db()

app.secret_key = os.environ.get('SECRET_KEY', 'memory-diary-secret-dev')

JST = timezone(timedelta(hours=9))

# 費用を抑えたい場合は "claude-haiku-4-5" に変更可（精度はopusが上）
MODEL = "claude-opus-4-8"

# ANTHROPIC_API_KEY 環境変数からキーを読む。
# キー未設定でも画面は開けるよう、最初に呼ばれたときに生成する（遅延初期化）。
_client = None


def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def now_jst():
    return datetime.now(JST)


# ─── 認証（APP_PASSWORD が設定されているときだけ有効）─────────────
@app.before_request
def require_login():
    password = os.environ.get('APP_PASSWORD', '')
    if not password:
        return
    if request.endpoint in ('login', 'logout', 'static'):
        return
    if not session.get('logged_in'):
        return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == os.environ.get('APP_PASSWORD', ''):
            session['logged_in'] = True
            return redirect('/')
        error = 'パスワードが違います'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ─── メイン ────────────────────────────────────────────────
@app.route('/')
def index():
    today = now_jst().strftime('%Y-%m-%d')
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, created_at, text FROM entries
        WHERE entry_date = ? ORDER BY id
    """, (today,))
    today_entries = c.fetchall()
    conn.close()
    return render_template('index.html',
                           today=today,
                           today_entries=today_entries)


@app.route('/history')
def history():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, entry_date, created_at, text FROM entries
        ORDER BY id DESC
    """)
    rows = c.fetchall()
    conn.close()
    # 日付ごとにまとめる（新しい日付が上、同じ日の中は古い順）
    groups = []
    for r in rows:  # rows は新しい順
        d = r['entry_date']
        if not groups or groups[-1]['date'] != d:
            groups.append({'date': d, 'entries': []})
        groups[-1]['entries'].append(r)
    for g in groups:
        g['entries'].reverse()  # 同じ日の中は古い順（朝→夜）
    return render_template('history.html', groups=groups, total=len(rows))


# ─── バックアップ（保存／復元）───────────────────────────────
@app.route('/export')
def export():
    """全記録をJSONファイルとしてダウンロードする（バックアップ）。"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT created_at, entry_date, text FROM entries ORDER BY id")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    payload = json.dumps(rows, ensure_ascii=False, indent=2)
    fname = 'memory_diary_backup_' + now_jst().strftime('%Y%m%d_%H%M') + '.json'
    return Response(
        payload,
        mimetype='application/json; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename={fname}'},
    )


@app.route('/import', methods=['GET', 'POST'])
def import_data():
    """バックアップJSONから記録を復元する（既存に追加する）。"""
    if request.method == 'POST':
        f = request.files.get('file')
        if not f:
            return render_template('import.html', message='ファイルを選んでください。')
        try:
            data = json.loads(f.read().decode('utf-8'))
        except Exception:
            return render_template('import.html',
                                   message='JSONとして読み込めませんでした。')
        if not isinstance(data, list):
            return render_template('import.html',
                                   message='バックアップの形式が正しくありません。')
        conn = get_connection()
        c = conn.cursor()
        added = 0
        for r in data:
            text = (r.get('text') or '').strip()
            if not text:
                continue
            created_at = r.get('created_at') or now_jst().strftime('%Y-%m-%d %H:%M')
            entry_date = r.get('entry_date') or created_at[:10]
            c.execute("""
                INSERT INTO entries (created_at, entry_date, text)
                VALUES (?, ?, ?)
            """, (created_at, entry_date, text))
            added += 1
        conn.commit()
        conn.close()
        return render_template('import.html',
                               message=f'{added}件を復元しました。')
    return render_template('import.html', message=None)


@app.route('/add', methods=['POST'])
def add():
    text = (request.form.get('text') or '').strip()
    if text:
        now = now_jst()
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO entries (created_at, entry_date, text)
            VALUES (?, ?, ?)
        """, (now.strftime('%Y-%m-%d %H:%M'),
              now.strftime('%Y-%m-%d'), text))
        conn.commit()
        conn.close()
    return redirect('/')


@app.route('/edit/<int:entry_id>', methods=['POST'])
def edit(entry_id):
    text = (request.form.get('text') or '').strip()
    if text:
        # 本文だけ修正する。出来事の日時（created_at）は変えない。
        conn = get_connection()
        c = conn.cursor()
        c.execute("UPDATE entries SET text = ? WHERE id = ?", (text, entry_id))
        conn.commit()
        conn.close()
    return redirect('/')


@app.route('/delete/<int:entry_id>', methods=['POST'])
def delete(entry_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()
    return redirect('/')


# ─── LLM呼び出し（記録だけを根拠に答えさせる）────────────────────
SUMMARY_SYSTEM = """あなたは利用者の「記憶の補助」をする日記アシスタントです。
以下に渡すのは、利用者がその日に書き留めた出来事のメモ（原文）です。

次のルールを必ず守ってください。
- これらの記録だけを根拠にしてください。記録にない出来事・人物・約束を創作してはいけません。
- 推測が必要なときは「（メモからは断定できません）」と明記してください。
- やさしく読みやすい日本語で、その日の出来事を時系列でまとめてください。
- 約束ややるべきことがあれば、最後に「忘れないこと」として箇条書きで整理してください。
- 前置きや「承知しました」などは書かず、まとめ本文から始めてください。"""

ASK_SYSTEM = """あなたは利用者の「記憶の補助」をする日記アシスタントです。
利用者は病気のため忘れやすく、あなたの答えを強く信頼します。事実の間違いは人間関係を傷つけかねません。
以下に渡すのは、利用者がこれまでに書き留めた出来事のメモ（日付つきの原文）です。

次のルールを必ず守ってください。
- 答えは、渡された記録だけを根拠にしてください。記録にない事柄を、推測で事実のように述べてはいけません。
- 該当する記録が見つからないときは、はっきり「記録にありません」と答えてください。
- 答えの最後に「根拠」という見出しをつけ、使った記録の日付と原文をそのまま引用してください。
- アドバイスを求められたときは、まず記録に基づく事実を述べ、そのあと「アドバイス」と分けて述べてください。アドバイスは記録に基づく範囲にとどめ、断定は避けてください。
- やさしい日本語で、前置きなしに答えてください。"""


def call_claude(system, user_text, max_tokens=2000):
    try:
        resp = get_client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        return next((b.text for b in resp.content if b.type == "text"), "")
    except Exception as e:
        return f"（エラー: AIに接続できませんでした。{e}）"


@app.route('/summarize', methods=['POST'])
def summarize():
    today = now_jst().strftime('%Y-%m-%d')
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT created_at, text FROM entries
        WHERE entry_date = ? ORDER BY id
    """, (today,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        return jsonify({"summary": "今日の記録がまだありません。"})
    lines = [f"[{r['created_at'][11:]}] {r['text']}" for r in rows]
    user_text = f"【{today} の記録】\n" + "\n".join(lines)
    summary = call_claude(SUMMARY_SYSTEM, user_text, max_tokens=2000)
    return jsonify({"summary": summary})


@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({"answer": "質問を入力してください。"})
    conn = get_connection()
    c = conn.cursor()
    # 記録は分量が少ないため、直近1000件をまとめて渡す（検索不要・漏れなし）
    c.execute("""
        SELECT created_at, text FROM entries
        ORDER BY id DESC LIMIT 1000
    """)
    rows = list(reversed(c.fetchall()))
    conn.close()
    if not rows:
        records = "（まだ記録がありません）"
    else:
        records = "\n".join(f"[{r['created_at']}] {r['text']}" for r in rows)
    user_text = f"【これまでの記録】\n{records}\n\n【質問】\n{question}"
    answer = call_claude(ASK_SYSTEM, user_text, max_tokens=1500)
    return jsonify({"answer": answer})


if __name__ == '__main__':
    app.run(debug=True, port=5001)
