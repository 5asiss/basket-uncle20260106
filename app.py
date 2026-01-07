import os
import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'basket-1234'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'basket_v12.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- [모델] ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price_retail = db.Column(db.Integer)
    category = db.Column(db.String(50))
    image_url = db.Column(db.String(500))

class Cart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    quantity = db.Column(db.Integer, default=1)
    product = db.relationship('Product')

# --- [로그인 설정] ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

# --- [주요 경로] ---
@app.route('/')
def index():
    products = Product.query.all()
    # 상단 탭을 위해 카테고리 중복 없이 가져오기
    categories = db.session.query(Product.category).distinct().all()
    return render_template('index.html', products=products, categories=[c[0] for c in categories if c[0]])

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))

# --- [관리자 기능: 상품 관리 & 등록] ---
@app.route('/admin/products')
@login_required
def admin_products():
    if not current_user.is_admin: return "권한 없음"
    products = Product.query.all()
    return render_template('admin_products.html', products=products)

@app.route('/admin/product/add', methods=['GET', 'POST'])
@login_required
def add_product():
    if not current_user.is_admin: return "권한 없음"
    
    if request.method == 'POST':
        new_p = Product(
            name=request.form.get('name'),
            price_retail=int(request.form.get('price')),
            category=request.form.get('category'),
            image_url=request.form.get('image_url')
        )
        db.session.add(new_p)
        db.session.commit()
        return redirect(url_for('admin_products'))
    
    # DB에 등록된 카테고리 목록을 가져와서 등록 화면에 뿌려줍니다.
    cats = db.session.query(Product.category).distinct().all()
    main_cats = [c[0] for c in cats if c[0]]
    if not main_cats: main_cats = ['식자재', '과일', '채소'] # 기본값
    
    return render_template('admin_add_product.html', main_cats=main_cats)

@app.route('/add/<int:p_id>')
def add_to_cart(p_id):
    new_item = Cart(user_id=1, product_id=p_id) 
    db.session.add(new_item)
    db.session.commit()
    return redirect('/cart')

@app.route('/cart')
def view_cart():
    items = Cart.query.all()
    grouped = {}
    for i in items:
        cat = i.product.category if i.product.category else "미분류"
        if cat not in grouped: grouped[cat] = []
        grouped[cat].append(i)
    return render_template('cart.html', grouped_cart=grouped, min_price=10000, delivery_fee=1900)

with app.app_context():
    db.create_all()
    if not User.query.filter_by(email='admin@test.com').first():
        admin = User(email='admin@test.com', password=generate_password_hash('1234'), is_admin=True)
        db.session.add(admin)
        db.session.commit()

if __name__ == '__main__':
    app.run(debug=True)