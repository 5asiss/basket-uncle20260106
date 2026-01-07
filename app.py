import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-uncle-1234'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket_final_v6.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- [데이터 모델] ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    is_admin = db.Column(db.Boolean, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    spec = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)
    price_wholesale = db.Column(db.Integer)
    category = db.Column(db.String(50))     # 대분류명 저장
    sub_category = db.Column(db.String(50)) # 소분류명 저장
    image_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    type = db.Column(db.String(20)) # 'MAIN' 또는 'SUB'

# --- [설정 및 로그인] ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- [사용자 화면] ---
@app.route('/')
def index():
    cat = request.args.get('cat')
    sub = request.args.get('sub')
    query = Product.query.filter_by(is_active=True)
    if cat: query = query.filter_by(category=cat)
    if sub: query = query.filter_by(sub_category=sub)
    products = query.all()
    
    # DB에서 카테고리 목록을 가져와서 화면에 뿌려줌
    main_cats = Category.query.filter_by(type='MAIN').all()
    sub_cats = Category.query.filter_by(type='SUB').all()
    
    return render_template('index.html', products=products, 
                           current_cat=cat, current_sub=sub, 
                           main_cats=main_cats, sub_cats=sub_cats)

# --- [관리자: 카테고리 관리] ---
@app.route('/admin/categories', methods=['GET', 'POST'])
@login_required
def admin_categories():
    if not current_user.is_admin: return "권한 없음"
    if request.method == 'POST':
        name = request.form['name']
        cat_type = request.form['type']
        if name and not Category.query.filter_by(name=name).first():
            new_cat = Category(name=name, type=cat_type)
            db.session.add(new_cat)
            db.session.commit()
        return redirect(url_for('admin_categories'))
    
    main_cats = Category.query.filter_by(type='MAIN').all()
    sub_cats = Category.query.filter_by(type='SUB').all()
    return render_template('admin_categories.html', main_cats=main_cats, sub_cats=sub_cats)

@app.route('/admin/category/delete/<int:id>')
@login_required
def delete_category(id):
    if not current_user.is_admin: return "권한 없음"
    cat = Category.query.get(id)
    if cat:
        db.session.delete(cat)
        db.session.commit()
    return redirect(url_for('admin_categories'))

# --- [관리자: 상품 및 엑셀 (기존 유지)] ---
@app.route('/admin/products')
@login_required
def admin_products():
    if not current_user.is_admin: return "권한 없음"
    products = Product.query.all()
    return render_template('admin_products.html', products=products)

@app.route('/admin/upload/excel', methods=['POST'])
@login_required
def upload_excel():
    if not current_user.is_admin: return "권한 없음"
    file = request.files.get('file')
    # 엑셀 업로드 시 카테고리 숫자를 이름으로 변환하기 위해 현재 DB의 카테고리 정보를 활용하거나 
    # 고정된 매핑을 사용합니다. 여기서는 삼촌님의 요청대로 고정 매핑을 유지합니다.
    SUB_MAP = {1:"과일", 2:"채소", 3:"양곡/견과류", 4:"정육/계란", 5:"수산/건해산물", 6:"양념/가루/오일", 7:"반찬/냉장/냉동/즉석식품", 8:"면류/통조림/간편식품", 9:"유제품/베이커리", 10:"생수/음료/커피/차", 11:"과자/시리얼/빙과", 12:"바디케어/베이비", 13:"주방/세제/세탁/청소", 14:"생활/잡화", 15:"대용량/식자재", 16:"세트상품"}
    try:
        df = pd.read_excel(file, engine='openpyxl')
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
    except Exception as e: return f"에러: {e}"

# --- [로그인/로그아웃/회원가입 (생략 없이 포함)] ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'])
        new_user = User(email=request.form['email'], password=hashed_pw, name=request.form['name'])
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

# --- [서버 시작] ---
with app.app_context():
    db.create_all()
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(email='admin@test.com', password=generate_password_hash('1234'), name='관리자', is_admin=True)
        db.session.add(admin)
        # 초기 기본 카테고리 생성
        if not Category.query.first():
            db.session.add(Category(name='농산물직거래', type='MAIN'))
            db.session.add(Category(name='식자재마트', type='MAIN'))
            db.session.add(Category(name='다이소', type='MAIN'))
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)