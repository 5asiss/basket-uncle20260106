import os
from flask import Flask, render_template, request, redirect, url_for, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from io import BytesIO
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-uncle-1234'

# 데이터베이스 경로 설정
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket.db')
db = SQLAlchemy(app)

# --- [1단계] 데이터베이스 설계도 (먼저 정의해야 함) ---
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
    price_retail = db.Column(db.Integer)    # 소매가
    price_wholesale = db.Column(db.Integer) # 도매가
    category = db.Column(db.String(50))     # 농산물직거래/식자재마트/다이소
    
    # --- 식자재마트 전용 상세 정보 ---
    spec = db.Column(db.String(100))        # 규격 (예: 18L, 1kg, 20개입)
    origin = db.Column(db.String(100))      # 원산지 (예: 국내산, 수입산)
    image_url = db.Column(db.String(500))   # 이미지 주소
    is_active = db.Column(db.Boolean, default=True)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    product_name = db.Column(db.String(100))
    total_price = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)

# --- [2단계] 로그인 관리 설정 ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- [3단계] 화면 경로 설정 (이제 Product를 마음껏 쓸 수 있습니다) ---
@app.route('/')
def index():
    category_name = request.args.get('cat')
    if category_name:
        products = Product.query.filter_by(category=category_name, is_active=True).all()
    else:
        products = Product.query.filter_by(is_active=True).all()
    return render_template('index.html', products=products, current_cat=category_name)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'])
        new_user = User(
            email=request.form['email'],
            password=hashed_pw,
            name=request.form['name'],
            grade='RETAIL'
        )
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin/excel')
@login_required
def download_excel():
    if not current_user.is_admin:
        return "권한 없음"
    orders = Order.query.all()
    df = pd.DataFrame([{
        "주문ID": o.id, "상품명": o.product_name, "금액": o.total_price, "날짜": o.created_at
    } for o in orders])
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, download_name="orders.xlsx", as_attachment=True)
# --- [관리자 전용: 상품 관리 페이지] ---
@app.route('/admin/products')
@login_required
def admin_products():
    if not current_user.is_admin: return "권한 없음"
    products = Product.query.all()
    return render_template('admin_products.html', products=products)

# --- [관리자 전용: 상품 등록 처리] ---
@app.route('/admin/product/add', methods=['POST'])
@login_required
def add_product():
    if not current_user.is_admin: return "권한 없음"
    
    new_p = Product(
        name = request.form['name'],
        price_retail = int(request.form['price_retail']),
        price_wholesale = int(request.form['price_wholesale']),
        category = request.form['category'],
        spec = request.form.get('spec', ''),      # 추가
        origin = request.form.get('origin', ''),  # 추가
        image_url = request.form.get('image_url', ''),
        is_active = True
    )
    db.session.add(new_p)
    db.session.commit()
    return redirect(url_for('admin_products'))

# --- [관리자 전용: 상품 삭제 처리] ---
@app.route('/admin/product/delete/<int:id>')
@login_required
def delete_product(id):
    if not current_user.is_admin: return "권한 없음"
    p = Product.query.get(id)
    db.session.delete(p)
    db.session.commit()
    return redirect(url_for('admin_products'))
# --- [4단계] 서버 실행 및 초기화 ---
# app.py 내의 초기화 블록 수정
with app.app_context():
    db.create_all()
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(
            email='admin@test.com', 
            password=generate_password_hash('1234'), 
            name='관리자', 
            is_admin=True
        )
        # 바뀐 카테고리에 맞춘 예시 상품
        p1 = Product(name='지례흑돼지 500g', price_retail=15000, price_wholesale=12000, category='농산물직거래', image_url='')
        p2 = Product(name='대용량 식용유 18L', price_retail=45000, price_wholesale=42000, category='식자재마트', image_url='')
        p3 = Product(name='다용도 정리함', price_retail=2000, price_wholesale=1500, category='다이소', image_url='')
        
        db.session.add_all([admin, p1, p2, p3])
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)