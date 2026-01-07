import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-1234'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket_v12.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- [설계도: 유저, 카테고리, 상품, 장바구니] ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(20)) # 'MAIN' 또는 'SUB'
    parent_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    sub_categories = db.relationship('Category', backref=db.backref('parent', remote_side=[id]))

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    spec = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)
    category = db.Column(db.String(50))
    sub_category = db.Column(db.String(50))
    image_url = db.Column(db.String(500))

# --- [로그인 설정] ---
login_manager = LoginManager(); login_manager.init_app(app); login_manager.login_view = 'login'
@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# --- [주요 화면들] ---
@app.route('/')
def index():
    products = Product.query.all()
    return render_template('index.html', products=products)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect('/')
    return render_template('login.html')

# --- [상품 등록 화면 (여기가 핵심!)] ---
@app.route('/admin/product/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if not current_user.is_admin: return "권한 없음"
    
    if request.method == 'POST':
        # 1. 폼에서 입력한 정보 가져오기
        new_p = Product(
            name=request.form.get('name'),
            spec=request.form.get('spec'),
            price_retail=int(request.form.get('price')) if request.form.get('price') else 0,
            category=request.form.get('category'),
            sub_category=request.form.get('sub_category'),
            image_url=request.form.get('image_url')
        )
        # 2. DB에 저장하기
        db.session.add(new_p)
        db.session.commit()
        
        # [중요!] 저장이 끝나면 '상품 관리' 페이지로 자동으로 보냅니다.
        return redirect(url_for('admin_products')) 
    
    # 처음 화면을 열 때는 카테고리 목록을 챙겨서 보여줍니다.
    main_cats = Category.query.filter_by(type='MAIN').all()
    return render_template('admin_add_product.html', main_cats=main_cats)

# --- [엑셀 업로드 (자동 카테고리 생성 포함)] ---
@app.route('/admin/upload/excel', methods=['POST'])
@login_required
def upload_excel():
    file = request.files.get('file')
    df = pd.read_excel(file, engine='openpyxl')
    for _, row in df.iterrows():
        m_name = str(row['카테고리']).strip()
        # 카테고리 장부에 없으면 즉시 추가
        if not Category.query.filter_by(name=m_name, type='MAIN').first():
            db.session.add(Category(name=m_name, type='MAIN'))
            db.session.flush()
        
        new_p = Product(
            name=row['상품명'], spec=row['규격'], price_retail=int(row['가격']),
            category=m_name, sub_category=str(row['세부카테고리']),
            image_url=f"/static/product_images/{row['이미지파일명']}"
        )
        db.session.add(new_p)
    db.session.commit()
    return redirect('/admin/products')

with app.app_context():
    db.create_all()
    if not User.query.filter_by(email='admin@test.com').first():
        db.session.add(User(email='admin@test.com', password=generate_password_hash('1234'), is_admin=True))
        db.session.commit()
# --- [화면 연결 경로: 404 에러 해결용] ---

@app.route('/admin/products')
def admin_products_page():
    # 관리자 페이지(화면)를 보여줍니다.
    conn = get_db_conn()
    products = [dict(row) for row in conn.execute("SELECT * FROM products").fetchall()]
    conn.close()
    return render_template('admin_products.html', products=products)

@app.route('/admin/categories')
def admin_categories_page():
    # 카테고리 관리 화면을 보여줍니다.
    return render_template('admin_categories.html', categories=CATEGORIES)

@app.route('/admin/product/add')
def admin_add_product_page():
    # 상품 등록 화면을 보여줍니다.
    return render_template('admin_add_product.html', main_cats=CATEGORIES)
if __name__ == '__main__': app.run(debug=True)