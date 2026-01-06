import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-uncle-1234'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket_v2.db')
db = SQLAlchemy(app)

# --- [데이터베이스 설계] ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    grade = db.Column(db.String(20), default='RETAIL')
    is_admin = db.Column(db.Boolean, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    spec = db.Column(db.String(100))        # 규격
    price_retail = db.Column(db.Integer)    # 소매가
    price_wholesale = db.Column(db.Integer) # 도매가
    category = db.Column(db.String(50))     # 대분류
    sub_category = db.Column(db.String(50)) # 소분류 (식자재 세부)
    image_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)

# --- [로그인 설정] ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- [기능: 화면 경로] ---
@app.route('/')
def index():
    cat = request.args.get('cat')
    sub = request.args.get('sub')
    query = Product.query.filter_by(is_active=True)
    
    if cat: query = query.filter_by(category=cat)
    if sub: query = query.filter_by(sub_category=sub)
    
    products = query.all()
    # 삼촌님이 주신 16개 카테고리 목록
    fixed_subs = ['과일', '채소', '양곡/견과류', '정육/계란', '수산/건해산물', '양념/가루/오일', '반찬/냉장/냉동/즉석식품', '면류/통조림/간편식품', '유제품/베이커리', '생수/음료/커피/차', '과자/시리얼/빙과', '바디케어/베이비', '주방/세제/세탁/청소', '생활/잡화', '대용량/식자재', '세트상품']
    
    return render_template('index.html', products=products, current_cat=cat, current_sub=sub, fixed_subs=fixed_subs)

# --- [기능: 엑셀 업로드] ---
@app.route('/admin/upload/excel', methods=['POST'])
@login_required
def upload_excel():
    if not current_user.is_admin: return "권한 없음"
    file = request.files.get('file')
    
    # 식자재마트 세부 카테고리 숫자 매핑 (삼촌님 요청)
    SUB_MAP = {1:"과일", 2:"채소", 3:"양곡/견과류", 4:"정육/계란", 5:"수산/건해산물", 6:"양념/가루/오일", 7:"반찬/냉장/냉동/즉석식품", 8:"면류/통조림/간편식품", 9:"유제품/베이커리", 10:"생수/음료/커피/차", 11:"과자/시리얼/빙과", 12:"바디케어/베이비", 13:"주방/세제/세탁/청소", 14:"생활/잡화", 15:"대용량/식자재", 16:"세트상품"}

    try:
        df = pd.read_excel(file)
        for _, row in df.iterrows():
            cat_name = {1:'농산물직거래', 2:'식자재마트', 3:'다이소'}.get(int(row['카테고리']), '기타')
            sub_name = SUB_MAP.get(int(row['세부카테고리']), '기타')
            
            new_p = Product(
                name=row['상품명'], spec=row['규격'], price_retail=int(row['가격']),
                price_wholesale=int(row['가격']*0.9), category=cat_name, sub_category=sub_name,
                image_url=f"/static/product_images/{row['이미지파일명']}"
            )
            db.session.add(new_p)
        db.session.commit()
        return redirect(url_for('admin_products'))
    except Exception as e:
        return f"에러: {e}"

# (중략: 로그인/로그아웃/회원가입/관리자페이지 기본 경로는 이전과 동일하게 유지)
# ... [나머지 코드는 이전의 app.py 코드를 아래에 이어 붙이세요] ...

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)