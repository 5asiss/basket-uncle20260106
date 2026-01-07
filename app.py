import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-uncle-1234'
basedir = os.path.abspath(os.path.dirname(__file__))
# DB 파일명을 v12로 설정하여 깨끗하게 시작합니다.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket_v12.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- [1. 데이터 모델: 설계도들이 맨 위에 있어야 에러가 안 납니다] ---

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    grade = db.Column(db.String(20), default='RETAIL')
    is_admin = db.Column(db.Boolean, default=False)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(20)) # 'MAIN' 또는 'SUB'
    parent_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    sub_categories = db.relationship('Category', backref=db.backref('parent', remote_side=[id]), lazy='joined')

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    spec = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)
    price_wholesale = db.Column(db.Integer)
    category = db.Column(db.String(50))
    sub_category = db.Column(db.String(50))
    image_url = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)

class Cart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer, default=1)
    product = db.relationship('Product')

# --- [2. 설정 및 로그인 매니저] ---
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- [3. 사용자 화면 경로] ---

@app.route('/')
def index():
    cat_name = request.args.get('cat')
    sub_name = request.args.get('sub')
    query = Product.query.filter_by(is_active=True)
    if cat_name: query = query.filter_by(category=cat_name)
    if sub_name: query = query.filter_by(sub_category=sub_name)
    products = query.all()
    main_cats = Category.query.filter_by(type='MAIN').all()
    return render_template('index.html', products=products, current_cat=cat_name, current_sub=sub_name, main_cats=main_cats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        print(f"로그인 시도: {email}")  # [체크 1] 버튼 누르면 터미널에 이게 뜨나요?

        user = User.query.filter_by(email=email).first()
        
        if user:
            print(f"사용자 발견: {user.email}") # [체크 2] DB에 사용자가 있는지 확인
            if check_password_hash(user.password, password):
                print("비밀번호 일치! 로그인 성공")
                login_user(user)
                return redirect(url_for('index'))
            else:
                print("비밀번호 불일치 에러")
        else:
            print("사용자를 찾을 수 없음 (DB에 계정이 없음)")
            
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

# --- [4. 장바구니 기능] ---

@app.route('/add_to_cart/<int:product_id>')
@login_required
def add_to_cart(product_id):
    item = Cart.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if item:
        item.quantity += 1
    else:
        new_item = Cart(user_id=current_user.id, product_id=product_id)
        db.session.add(new_item)
    db.session.commit()
    return redirect(url_for('cart_page'))

@app.route('/cart')
@login_required
def cart_page():
    cart_items = Cart.query.filter_by(user_id=current_user.id).all()
    grouped_cart = {}
    for item in cart_items:
        cat = item.product.category if item.product.category else "기타"
        if cat not in grouped_cart: grouped_cart[cat] = []
        grouped_cart[cat].append(item)
    return render_template('cart.html', grouped_cart=grouped_cart, min_price=10000, delivery_fee=1900)

# --- [5. 관리자 기능: 카테고리 & 상품 & 엑셀] ---

@app.route('/admin/categories', methods=['GET', 'POST'])
@login_required
def admin_categories():
    if not current_user.is_admin: return "권한 없음"
    if request.method == 'POST':
        name = request.form.get('name')
        cat_type = request.form.get('type')
        p_id = request.form.get('parent_id')
        if name:
            new_cat = Category(name=name, type=cat_type, parent_id=p_id if p_id else None)
            db.session.add(new_cat)
            db.session.commit()
        return redirect(url_for('admin_categories'))
    main_cats = Category.query.filter_by(type='MAIN').all()
    return render_template('admin_categories.html', main_cats=main_cats)

@app.route('/admin/category/delete/<int:id>')
@login_required
def delete_category(id):
    if not current_user.is_admin: return "권한 없음"
    cat = Category.query.get(id)
    if cat:
        db.session.delete(cat)
        db.session.commit()
    return redirect(url_for('admin_categories'))

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
    try:
        df = pd.read_excel(file, engine='openpyxl')
        for _, row in df.iterrows():
            new_p = Product(
                name=row['상품명'], spec=row['규격'], price_retail=int(row['가격']),
                price_wholesale=int(row['가격']*0.9), category=str(row['카테고리']), 
                sub_category=str(row['세부카테고리']),
                image_url=f"/static/product_images/{row['이미지파일명']}"
            )
            db.session.add(new_p)
        db.session.commit()
        return redirect(url_for('admin_products'))
    except Exception as e: return f"에러: {e}"

# --- [6. 서버 시작 시 초기화] ---
with app.app_context():
    # 1. 먼저 장부(테이블)를 생성합니다.
    db.create_all() 
    
    # 2. 그 다음에 관리자가 있는지 확인하고 만듭니다.
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(
            email='admin@test.com', 
            password=generate_password_hash('1234'), 
            name='관리자', 
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()
        print("관리자 계정 생성 완료!")

if __name__ == '__main__':
    app.run(debug=True)