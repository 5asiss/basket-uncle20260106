import sqlite3
import os
import pandas as pd
import base64
import io
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session
from datetime import datetime
import json

app = Flask(__name__)
app.secret_key = "basket_uncle_secret_key" # 세션 유지를 위한 열쇠

# 1. 데이터베이스 및 경로 설정
DB_PATH = "market.db"
BASE_IMG_PATH = r"C:\Users\new\Desktop\image\clean_images"

# 2. 카테고리 목록 정의 (CATEGORIES 에러 해결)
CATEGORY_MAP = {
    1: '과일', 2: '채소', 3: '양곡/견과류', 4: '정육/계란', 5: '수산/건해산물', 
    6: '양념/가루/오일', 7: '반찬/냉장/냉동/즉석식품', 8: '면류/통조림/간편식품', 
    9: '유제품/베이커리', 10: '생수/음료/커피/차', 11: '과자/시리얼/빙과', 
    12: '바디케어/베이비', 13: '주방/세제/세탁/청소', 14: '생활/잡화', 
    15: '대용량/식자재', 16: '세트상품'
}
CATEGORIES = list(CATEGORY_MAP.values()) + ['기타']

# 3. DB 연결 함수 정의 (get_db_conn 에러 해결)
def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --- [초기 설정: DB 만들기] ---
def init_db():
    conn = get_db_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products 
                 (id TEXT PRIMARY KEY, name TEXT, price INTEGER, stock INTEGER, category TEXT, icon TEXT, tags TEXT, isClosed INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders 
                 (orderNumber TEXT PRIMARY KEY, name TEXT, phone TEXT, address TEXT, detail TEXT, gate TEXT, items TEXT, total TEXT, status TEXT, payment TEXT, date TEXT, cart TEXT, userId TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id TEXT PRIMARY KEY, password TEXT, name TEXT, nickname TEXT, phone TEXT, email TEXT, address TEXT, detail_addr TEXT, gate_pw TEXT, is_admin INTEGER DEFAULT 0)''')
    c.execute("INSERT OR IGNORE INTO users (id, password, name, nickname, is_admin) VALUES ('admin', '3150', '관리자', '관리자형님', 1)")
    conn.commit()
    conn.close()

init_db()

# --- [화면 연결 경로: 여기서부터 삼촌님이 필요한 화면들입니다] ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # API 방식이 아닌 일반 폼 전송 방식 대응
        user_id = request.form.get('email') or request.form.get('id')
        password = request.form.get('password')
        
        conn = get_db_conn()
        user = conn.execute("SELECT * FROM users WHERE id=? AND password=?", (user_id, password)).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['is_admin'] = user['is_admin']
            return redirect(url_for('home'))
        else:
            return "<script>alert('아이디 또는 비밀번호가 틀렸습니다.'); history.back();</script>"
    return render_template('login.html')

@app.route('/admin/products')
def admin_products_page():
    conn = get_db_conn()
    products = [dict(row) for row in conn.execute("SELECT * FROM products").fetchall()]
    conn.close()
    return render_template('admin_products.html', products=products)

@app.route('/admin/product/add')
def admin_add_product_page():
    return render_template('admin_add_product.html', main_cats=CATEGORIES)

# --- [카테고리 관리: 보여주기 + 추가하기 합친 코드] ---

@app.route('/admin/categories', methods=['GET', 'POST'])
def admin_categories_page():
    if not session.get('is_admin'): return "권한 없음"
    
    # 1. 사용자가 '카테고리 생성' 버튼을 눌렀을 때 (POST 방식)
    if request.method == 'POST':
        new_cat_name = request.form.get('name')
        if new_cat_name:
            # 삼촌님은 CATEGORIES 리스트를 쓰시므로 여기에 추가해줍니다.
            if new_cat_name not in CATEGORIES:
                CATEGORIES.append(new_cat_name)
                # (참고) 만약 DB에도 저장하고 싶다면 여기에 DB 저장 코드를 추가할 수 있습니다.
            return redirect(url_for('admin_categories_page'))

    # 2. 그냥 페이지를 열었을 때 (GET 방식)
    return render_template('admin_categories.html', categories=CATEGORIES)

@app.route('/admin/orders')
def admin_orders_page():
    conn = get_db_conn()
    orders = [dict(row) for row in conn.execute("SELECT * FROM orders ORDER BY orderNumber DESC").fetchall()]
    conn.close()
    return render_template('admin_orders.html', orders=orders)

# --- [기타 API 기능들은 아래에 유지] ---
@app.route('/api/init', methods=['GET'])
def get_initial_data():
    conn = get_db_conn()
    products = [dict(row) for row in conn.execute("SELECT * FROM products").fetchall()]
    settings = {'categories': CATEGORIES}
    conn.close()
    return jsonify({'products': products, 'settings': settings})

# ... (나머지 삭제/업로드 기능들 생략되거나 유지 가능) ...

if __name__ == '__main__':
    app.run(debug=True, port=5000)