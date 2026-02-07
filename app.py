import os
import requests
from dotenv import load_dotenv
import base64
from datetime import datetime, timedelta
from io import BytesIO
import re
import random # 최신상품 랜덤 노출을 위해 추가

import pandas as pd
from flask import Flask, render_template_string, request, redirect, url_for, session, send_file, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import text
from delivery_system import logi_bp # 배송 시스템 파일에서 Blueprint 가져오기
load_dotenv()

# --------------------------------------------------------------------------------
# 1. 초기 설정 및 Flask 인스턴스 생성
# --------------------------------------------------------------------------------
# --- 수정 전 기존 코드 ---
# app = Flask(__name__)
# app.register_blueprint(logi_bp) 
# app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///direct_trade_mall.db'
# db = SQLAlchemy(app)

# --- 수정 후 (이 부분으로 교체하세요) ---
from delivery_system import logi_bp, db_delivery

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(BASE_DIR, 'direct_trade_mall.db')
delivery_db_path = os.path.join(BASE_DIR, 'delivery.db')

app = Flask(__name__)
def force_init_db():
    with app.app_context():
        try:
            # 1. 테이블 생성
            db.create_all()
            
            # 2. 필수 컬럼 강제 패치
            from sqlalchemy import text
            db.session.execute(text('ALTER TABLE "order" ADD COLUMN is_settled INTEGER DEFAULT 0'))
            db.session.execute(text('ALTER TABLE "order" ADD COLUMN settled_at DATETIME'))
            db.session.commit()
        except Exception:
            db.session.rollback() # 컬럼이 이미 있으면 에러나므로 롤백 후 통과

        # 3. 데이터가 비어있을 때만 100개 상품 생성 함수 실행
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        if inspector.has_table("category"):
            if not Category.query.first():
                print("🔄 [System] 테이블은 있으나 데이터가 비어있음. init_db() 실행...")
                init_db()
            else:
                print("✅ [System] 이미 데이터가 존재합니다.")
        else:
            print("❌ [Error] 여전히 category 테이블을 생성하지 못했습니다.")
         
            force_init_db()
# ... (기존 설정들: secret_key, config 등) ...

# [중요] 초기화 함수를 함수 밖으로 꺼내서 Gunicorn이 읽을 수 있게 합니다.
def initialize_database():
    with app.app_context():
        try:
            # 1. 테이블 생성
            db.create_all()
            
            # 2. SQLite 컬럼 패치
            from sqlalchemy import text
            alter_queries = [
                'ALTER TABLE "order" ADD COLUMN is_settled INTEGER DEFAULT 0',
                'ALTER TABLE "order" ADD COLUMN settled_at DATETIME'
            ]
            for query in alter_queries:
                try:
                    db.session.execute(text(query))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            # 3. 테스트 데이터 생성 (최저가 쇼핑몰 100개 상품)
            init_db() 
            print("✅ Database & Test Data initialized successfully.")
        except Exception as e:
            print(f"❌ Initialization Error: {e}")

# 앱이 로드될 때 즉시 실행
initialize_database()

# ... (이후 라우트 함수들: @app.route 등) ...
app.secret_key = os.getenv("FLASK_SECRET_KEY", "low_price_mall_key_2026")

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", f"sqlite:///{db_path}")
app.config['SQLALCHEMY_BINDS'] = {
    'delivery': os.getenv("DELIVERY_DATABASE_URL", f"sqlite:///{delivery_db_path}")
}
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
MALL_NAME = "최저가쇼핑몰"
# 2. DB 연결 (공백 제거 버전)
db = db_delivery
db.init_app(app)

# 3. 배송 관리 시스템 Blueprint 등록 (주소 접두어 /logi 적용됨)
app.register_blueprint(logi_bp)

# 결제 연동 키 (Toss Payments)
TOSS_CLIENT_KEY = os.getenv("TOSS_CLIENT_KEY")
TOSS_SECRET_KEY = os.getenv("TOSS_SECRET_KEY")

# 파일 업로드 경로 설정
UPLOAD_FOLDER = os.path.join('static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)


login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# --------------------------------------------------------------------------------
# 2. 데이터베이스 모델 설계 (DB 구조 변경 금지 규칙 준수)
# --------------------------------------------------------------------------------

class Settlement(db.Model):
    """카테고리별 정산 내역 모델"""
    id = db.Column(db.Integer, primary_key=True)
    category_name = db.Column(db.String(50), nullable=False)
    manager_email = db.Column(db.String(120), nullable=False)
    
    # 정산 기간 및 금액
    total_sales = db.Column(db.Integer, default=0)       # 총 판매금액
    delivery_fee_sum = db.Column(db.Integer, default=0)  # 발생한 총 배송비 (공제용)
    settlement_amount = db.Column(db.Integer, default=0) # 최종 정산(지급) 금액
    
    status = db.Column(db.String(20), default='정산대기')  # 정산대기, 정산완료, 보류
    requested_at = db.Column(db.DateTime, default=datetime.now)
    completed_at = db.Column(db.DateTime, nullable=True)
class User(db.Model, UserMixin):
    """사용자 정보 모델"""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False) 
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(50))
    phone = db.Column(db.String(20))
    address = db.Column(db.String(200))          
    address_detail = db.Column(db.String(200)) 
    entrance_pw = db.Column(db.String(100))    
    request_memo = db.Column(db.String(500))
    is_admin = db.Column(db.Boolean, default=False)
    consent_marketing = db.Column(db.Boolean, default=False)

class Category(db.Model):
    """카테고리 및 판매 사업자 정보 모델"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    tax_type = db.Column(db.String(20), default='과세') 
    manager_email = db.Column(db.String(120), nullable=True) 
    seller_name = db.Column(db.String(100), nullable=True)
    seller_inquiry_link = db.Column(db.String(500), nullable=True)
    order = db.Column(db.Integer, default=0) 
    description = db.Column(db.String(200), nullable=True)
    biz_name = db.Column(db.String(100), nullable=True)
    biz_representative = db.Column(db.String(50), nullable=True)
    biz_reg_number = db.Column(db.String(50), nullable=True)
    biz_address = db.Column(db.String(200), nullable=True)
    biz_contact = db.Column(db.String(50), nullable=True)

class Product(db.Model):
    """상품 정보 모델"""
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50)) 
    description = db.Column(db.String(200)) 
    name = db.Column(db.String(200))
    price = db.Column(db.Integer)
    spec = db.Column(db.String(100))     
    origin = db.Column(db.String(100))   
    farmer = db.Column(db.String(50))    
    image_url = db.Column(db.String(500)) 
    detail_image_url = db.Column(db.Text) 
    stock = db.Column(db.Integer, default=10) 
    deadline = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    tax_type = db.Column(db.String(20), default='과세') 
    badge = db.Column(db.String(50), default='')

class Cart(db.Model):
    """장바구니 모델"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    product_id = db.Column(db.Integer)
    product_name = db.Column(db.String(100))
    product_category = db.Column(db.String(50)) 
    price = db.Column(db.Integer)
    quantity = db.Column(db.Integer, default=1)
    tax_type = db.Column(db.String(20), default='과세')

class Order(db.Model):
    """주문 내역 모델"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    customer_name = db.Column(db.String(50))
    customer_phone = db.Column(db.String(20))
    customer_email = db.Column(db.String(120))
    product_details = db.Column(db.Text) 
    total_price = db.Column(db.Integer)
    delivery_fee = db.Column(db.Integer, default=0) 
    tax_free_amount = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='결제완료')
    is_settled = db.Column(db.Boolean, default=False)  # 정산 완료 여부
    settled_at = db.Column(db.DateTime, nullable=True) # 정산 처리 일시    
    order_id = db.Column(db.String(100)) 
    payment_key = db.Column(db.String(200)) 
    delivery_address = db.Column(db.String(500))
    request_memo = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.now)

class Review(db.Model):
    """사진 리뷰 모델"""
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, unique=True)
    user_id = db.Column(db.Integer)
    user_name = db.Column(db.String(50))
    product_id = db.Column(db.Integer) 
    product_name = db.Column(db.String(100))
    content = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.now)

class UserConsent(db.Model):
    """이용 동의 내역 모델"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    email = db.Column(db.String(120))
    consent_privacy = db.Column(db.Boolean, default=True)
    consent_third_party = db.Column(db.Boolean, default=True)
    consent_purchase_agency = db.Column(db.Boolean, default=True)
    consent_terms = db.Column(db.Boolean, default=True)
    consent_marketing = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --------------------------------------------------------------------------------
# 3. 공통 유틸리티 함수
# --------------------------------------------------------------------------------

from PIL import Image # 이미지 처리를 위해 상단에 추가

from PIL import Image, ImageOps # 상단 import문에 추가하세요

def save_uploaded_file(file):
    """핸드폰 사진 공백 제거(중앙 크롭) 및 WebP 변환"""
    if file and file.filename != '':
        # 파일명 설정 (.webp로 통일하여 용량 절감)
        new_filename = f"uncle_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.webp"
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], new_filename)

        # 1. 이미지 열기
        img = Image.open(file)

        # 2. 핸드폰 사진 회전 방지 (EXIF 정보 바탕으로 방향 바로잡기)
        img = ImageOps.exif_transpose(img)

        # 3. 정사각형으로 중앙 크롭 (가로세로 800px)
        # ImageOps.fit은 이미지의 중심을 기준으로 비율에 맞춰 꽉 채워 자릅니다.
        size = (800, 800)
        img = ImageOps.fit(img, size, Image.Resampling.LANCZOS)

        # 4. WebP로 저장 (용량 최적화)
        img.save(save_path, "WEBP", quality=85)
        
        return f"/static/uploads/{new_filename}"
    return None

def check_admin_permission(category_name=None):
    """관리자 권한 체크"""
    if not current_user.is_authenticated: return False
    if current_user.is_admin: return True 
    if category_name:
        cat = Category.query.filter_by(name=category_name).first()
        if cat and cat.manager_email == current_user.email: return True
    return False

# --------------------------------------------------------------------------------
# 4. HTML 공통 레이아웃 (Header / Footer / Global Styles)
# --------------------------------------------------------------------------------

HEADER_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="naver-site-verification" content="11c3f5256fbdca16c2d7008b7cf7d0feff9b056b" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="description" content="바구니 삼촌은 농산물·식자재를 중간 유통 없이 직접 연결하고 최소 배송비만 받는 신개념 물류·구매대행 서비스입니다.">
<title>바구니 삼촌 |  basam</title>

    <title>최저가 쇼핑몰 - 농산물·식자재 배송 신개념 6PL 생활서비스 basam </title>
    <script src="https://js.tosspayments.com/v1/payment"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="//t1.daumcdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
<style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;900&display=swap');
    
    body { 
        font-family: 'Noto Sans KR', sans-serif; 
        background-color: #f8fafc; /* 약간 더 밝고 세련된 slate-50 톤 */
        color: #1e293b; 
        -webkit-tap-highlight-color: transparent; 
        overflow-x: hidden; 
        line-height: 1.6;
    }
    
    /* 1. 품목 및 배지 겹침 방지 스타일 (기사앱 로직 적용) */
    .item-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 8px;
        font-weight: 700;
        font-size: 0.75rem;
        line-height: 1.4; /* 줄간격 확보 */
        margin-bottom: 4px;
        white-space: nowrap; /* 텍스트가 강제로 쪼개지지 않게 함 */
    }

    /* 2. 유틸리티 스타일 강화 */
    .sold-out { filter: grayscale(100%) blur(1px); opacity: 0.5; transition: 0.3s; }
    .sold-out-badge { 
        position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
        background: rgba(15, 23, 42, 0.9); /* slate-900 톤 */
        color: white; padding: 12px 24px; 
        border-radius: 16px; font-weight: 900; z-index: 10; 
        border: 1.5px solid rgba(255,255,255,0.3);
        box-shadow: 0 10px 20px rgba(0,0,0,0.2);
        letter-spacing: -0.05em;
    }
    .no-scrollbar::-webkit-scrollbar { display: none; }
    
    /* 3. 가로 스크롤 레이아웃 (상품 리스트 터치감 최적화) */
    .horizontal-scroll {
        display: flex; overflow-x: auto; scroll-snap-type: x mandatory; 
        gap: 16px; padding: 10px 20px 25px 20px; 
        -webkit-overflow-scrolling: touch;
    }
    .horizontal-scroll > div { scroll-snap-align: start; flex-shrink: 0; }
    
    /* 4. 사이드바 메뉴 (더 부드러운 애니메이션) */
    #sidebar {
        position: fixed; top: 0; left: -300px; width: 280px; height: 100%;
        background: white; z-index: 5001; transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 20px 0 50px rgba(0,0,0,0.1); overflow-y: auto;
    }
    #sidebar.open { left: 0; }
    #sidebar-overlay {
        position: fixed; top: 0; left: 0; width: 100%; height: 100%;
        background: rgba(15, 23, 42, 0.6); z-index: 5000; display: none; backdrop-filter: blur(4px);
    }
    #sidebar-overlay.show { display: block; }

    /* 5. 알림 토스트 (모바일 조작 영역 고려) */
    #toast {
        visibility: hidden; min-width: 80%; background-color: #0f172a; color: #fff; text-align: center;
        border-radius: 20px; padding: 16px; position: fixed; z-index: 9999; left: 50%; bottom: 40px;
        transform: translateX(-50%) translateY(20px); font-size: 14px; font-weight: 700; transition: 0.4s; opacity: 0;
        box-shadow: 0 15px 35px rgba(0,0,0,0.3);
    }
    #toast.show { visibility: visible; opacity: 1; transform: translateX(-50%) translateY(0); }

    /* 6. 모달 스타일 (가독성 및 버튼 접근성) */
    #term-modal { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(15, 23, 42, 0.8); z-index:6000; align-items:center; justify-content:center; padding:16px; }
    #term-modal-content { background:white; width:100%; max-width:500px; max-height:80vh; border-radius:2.5rem; overflow:hidden; display:flex; flex-direction:column; box-shadow:0 40px 80px rgba(0,0,0,0.5); }
    #term-modal-body { overflow-y:auto; padding:2rem; font-size:0.95rem; line-height:1.7; color:#334155; }

    /* 7. 반응형 디자인 최적화 */
    @media (max-width: 640px) {
        .hero-title { font-size: 2rem !important; line-height: 1.2 !important; font-weight: 900; }
        .hero-desc { font-size: 0.95rem !important; opacity: 0.8; }
        .card-padding { padding: 1rem !important; }
    }
</style>
</head>
<body class="text-left font-black">
    <div id="toast">메시지가 표시됩니다. 🧺</div>

    <div id="logout-warning-modal" class="fixed inset-0 bg-black/60 z-[9999] hidden flex items-center justify-center p-4 backdrop-blur-sm">
        <div class="bg-white w-full max-w-sm rounded-[2.5rem] p-10 shadow-2xl text-center">
            <div class="w-16 h-16 bg-orange-100 text-orange-500 rounded-full flex items-center justify-center mx-auto mb-6 text-2xl">
                <i class="fas fa-clock animate-pulse"></i>
            </div>
            <h3 class="text-xl font-black text-gray-800 mb-2">자동 로그아웃 안내</h3>
            <p class="text-gray-500 font-bold text-sm mb-8 leading-relaxed">
                장시간 활동이 없어 <span id="logout-timer" class="text-orange-600 font-black">60</span>초 후<br>로그아웃 됩니다. 로그인 상태를 유지할까요?
            </p>
            <div class="flex gap-3">
                <button onclick="location.href='/logout'" class="flex-1 py-4 bg-gray-100 text-gray-400 rounded-2xl font-black text-sm">로그아웃</button>
                <button onclick="extendSession()" class="flex-1 py-4 bg-green-600 text-white rounded-2xl font-black text-sm shadow-lg shadow-green-100">로그인 유지</button>
            </div>
        </div>
    </div>
    
    <div id="sidebar-overlay" onclick="toggleSidebar()"></div>
    <div id="sidebar" class="p-10 flex flex-col h-full">
        <div class="flex justify-between items-center mb-12">
            <div class="flex items-center gap-2">
                <img src="/static/logo/side1.jpg" class="h-6 w-auto rounded" onerror="this.style.display='none'">
                <h3 class="text-xl text-green-600 italic font-black uppercase tracking-tighter">Categories</h3>
            </div>
            <button onclick="toggleSidebar()" class="text-gray-300 text-2xl hover:text-red-500 transition"><i class="fas fa-times"></i></button>
        </div>
        <nav class="space-y-7 text-base flex-1">
            <a href="/" class="group flex items-center gap-3 text-gray-800 hover:text-green-600 transition font-black">
                <i class="fas fa-th-large opacity-20 group-hover:opacity-100 transition"></i> 전체 상품 리스트
            </a>
            <div class="h-px bg-gray-100 w-full my-4"></div>
            {% for c in nav_categories %}
            <a href="/category/{{ c.name }}" class="flex items-center justify-between text-gray-500 hover:text-green-600 transition">
                <span>{{ c.name }}</span>
                <i class="fas fa-chevron-right text-[10px] opacity-30"></i>
            </a>
            {% endfor %}
            <div class="h-px bg-gray-100 w-full my-4"></div>
            <a href="/about" class="block font-bold text-blue-500 hover:underline">최저가 쇼핑몰이란?</a>
        </nav>
    </div> <nav class="bg-white/95 backdrop-blur-md shadow-sm sticky top-0 z-50">
        <div class="max-w-7xl mx-auto px-3 md:px-6">
            <div class="flex justify-between h-16 md:h-20 items-center">
                <div class="flex items-center gap-2 md:gap-6">
                    <button onclick="toggleSidebar()" class="text-gray-400 text-xl md:text-2xl hover:text-green-600 transition p-1">
                        <i class="fas fa-bars"></i>
                    </button>
                    <a href="/" class="flex items-center gap-1.5">
                        <img src="/static/logo/side1.jpg" alt="최저가 쇼핑몰" class="h-7 md:h-10 w-auto rounded-lg" onerror="this.style.display='none'">
                        <span class="italic tracking-tighter uppercase font-black text-green-600 text-base md:text-xl">최저가 쇼핑몰</span>
                    </a>
                </div>

                <div class="flex items-center gap-2 md:gap-5 flex-1 justify-end">
                    <form action="/search" method="GET" class="relative hidden md:block max-w-xs flex-1">
                        <input name="q" placeholder="상품명을 입력하세요" 
                               class="w-full bg-gray-100 py-3 px-6 rounded-full text-sm font-bold outline-none focus:ring-4 focus:ring-green-50 transition border border-transparent focus:border-green-100 appearance-none"
                               style="line-height: normal; font-family: 'Noto Sans KR', sans-serif;">
                        <button type="submit" class="absolute right-4 top-3 text-gray-400 hover:text-green-600 transition">
                            <i class="fas fa-search"></i>
                        </button>
                    </form>
                    
                    <button onclick="document.getElementById('mobile-search-nav').classList.toggle('hidden')" class="md:hidden text-gray-400 p-2 text-lg">
                        <i class="fas fa-search"></i>
                    </button>

                    {% if current_user.is_authenticated %}
                        <a href="/cart" class="text-gray-400 relative p-1.5 hover:text-green-600 transition">
                            <i class="fas fa-shopping-cart text-xl md:text-3xl"></i>
                            <span id="cart-count-badge" class="absolute top-0 right-0 bg-red-500 text-white text-[8px] md:text-[10px] rounded-full px-1 py-0.5 font-black border border-white shadow-sm">{{ cart_count }}</span>
                        </a>
                        <a href="/mypage" class="text-gray-600 font-black bg-gray-100 px-3 py-1.5 rounded-full text-[9px] md:text-xs hover:bg-gray-200 transition">MY</a>
                    {% else %}
                        <a href="/login" class="text-gray-400 font-black text-[10px] md:text-sm hover:text-green-600 transition">로그인</a>
                    {% endif %}
                </div>
            </div>
            
            <div id="mobile-search-nav" class="hidden md:hidden pb-4">
                <form action="/search" method="GET" class="relative">
                    <input name="q" placeholder="어떤 상품을 찾으시나요?" 
                           class="w-full bg-white py-4 px-7 rounded-full text-base font-bold outline-none border-2 border-green-50 focus:border-green-200 transition shadow-sm appearance-none"
                           style="line-height: normal; font-family: 'Noto Sans KR', sans-serif;">
                    <button type="submit" class="absolute right-6 top-4 text-green-600">
                        <i class="fas fa-search"></i>
                    </button>
                </form>
            </div>
        </div>
    </nav>
    <main class="min-h-screen">
    <script>
    // Flask에서 설정한 세션 타임아웃 시간 (초 단위, 예: 30분 = 1800초)
    const SESSION_TIMEOUT = 30 * 60; 
    const WARNING_TIME = 60; // 로그아웃 60초 전에 경고창 표시
    
    let warningTimer;
    let countdownInterval;

    function startLogoutTimer() {
        // 1. 기존 타이머가 있다면 제거
        clearTimeout(warningTimer);
        
        // 2. 경고창을 띄울 시간 계산 (전체 시간 - 60초)
        warningTimer = setTimeout(() => {
            showLogoutWarning();
        }, (SESSION_TIMEOUT - WARNING_TIME) * 1000);
    }

    function showLogoutWarning() {
        const modal = document.getElementById('logout-warning-modal');
        const timerDisplay = document.getElementById('logout-timer');
        let timeLeft = WARNING_TIME;

        modal.classList.remove('hidden');
        
        // 1초마다 숫자를 깎는 카운트다운 시작
        countdownInterval = setInterval(() => {
            timeLeft -= 1;
            timerDisplay.innerText = timeLeft;
            
            if (timeLeft <= 0) {
                clearInterval(countdownInterval);
                location.href = '/logout'; // 0초가 되면 로그아웃 실행
            }
        }, 1000);
    }

    function extendSession() {
        // 서버에 가벼운 요청을 보내 세션을 연장시킵니다 (가장 간단한 방법)
        fetch('/').then(() => {
            // 경고창 숨기기 및 타이머 리셋
            document.getElementById('logout-warning-modal').classList.add('hidden');
            clearInterval(countdownInterval);
            startLogoutTimer(); 
            showToast("로그인 시간이 연장되었습니다. 😊");
        });
    }

    // 사용자가 로그인한 상태일 때만 타이머 작동
    {% if current_user.is_authenticated %}
    startLogoutTimer();
    {% endif %}
</script>
"""

FOOTER_HTML = """
    </main>

    <footer class="bg-gray-900 text-gray-400 py-12 md:py-20 border-t border-white/5 mt-20">
        <div class="max-w-7xl mx-auto px-6">
            
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-white/5 pb-10 mb-10 gap-8">
                <div class="text-left">
                    <p class="text-green-500 font-black text-2xl italic tracking-tighter mb-2 uppercase">최저가 쇼핑몰</p>
                    <p class="text-xs text-orange-500 font-bold italic">인천 연수구 송도동 전용 구매대행 및 배송 서비스</p>
                </div>
                
                <div class="flex flex-col md:items-end gap-3 w-full md:w-auto">
                    <p class="font-bold text-gray-200 text-sm md:text-base font-black">Customer Center</p>
                    <div class="flex flex-wrap md:justify-end gap-3 items-center">
                        <a href="http://pf.kakao.com/_AIuxkn" target="_blank" class="bg-[#FEE500] text-gray-900 px-5 py-2.5 rounded-xl font-black text-[11px] flex items-center gap-2 shadow-lg transition hover:brightness-105">
                            <i class="fas fa-comment"></i> 카카오톡 문의
                        </a>
                        <p class="text-lg font-black text-white ml-2">1666-8320</p>
                    </div>
                    <p class="text-[10px] font-bold text-gray-500">평일 09:00 ~ 18:00 (점심 12:00 ~ 13:00)</p>
                </div>
            </div>

            <div class="flex flex-wrap gap-x-6 gap-y-3 mb-8 text-[11px] font-bold opacity-60 underline">
                <a href="javascript:void(0)" onclick="openUncleModal('terms')" class="hover:text-white transition">이용약관</a>
                <a href="javascript:void(0)" onclick="openUncleModal('privacy')" class="hover:text-white transition">개인정보처리방침</a>
                <a href="javascript:void(0)" onclick="openUncleModal('agency')" class="hover:text-white transition">이용 안내</a>
                <a href="javascript:void(0)" onclick="openUncleModal('e_commerce')" class="hover:text-white transition">전자상거래 유의사항</a>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-2 gap-8 items-end">
                <div class="text-[10px] md:text-[11px] space-y-1.5 opacity-40 leading-relaxed font-medium text-left">
                    <p>상호: 최저가 쇼핑몰 | 대표: 금창권 | 개인정보관리책임자: 금창권</p>
                    <p>주소: 인천광역시 연수구 하모니로158, D동 317호 (송도동, 송도 타임스페이스)</p>
                    <p>사업자등록번호: 472-93-02262 | 통신판매업신고: 제 2025-인천연수-3388호</p>
                    <p>이메일: basamsongdo@gmail.com</p>
                    <p class="pt-4 opacity-100 font-bold uppercase tracking-[0.2em]">© 2026 BASAM. All Rights Reserved.</p>
                </div>
                
                <div class="hidden md:block text-right opacity-20">
                    <i class="fas fa-truck-fast text-5xl"></i>
                </div>
            </div>
        </div>
    </footer>


<!-- ✅ 여기부터 붙여넣기 -->
<div id="uncleModal" class="fixed inset-0 bg-black bg-opacity-70 hidden items-center justify-center z-50">
  <div class="bg-white text-black max-w-3xl w-full mx-4 rounded-xl shadow-lg overflow-y-auto max-h-[80vh]">
    <div class="flex justify-between items-center p-6 border-b">
      <h2 id="uncleModalTitle" class="text-lg font-bold"></h2>
      <button onclick="closeUncleModal()" class="text-gray-500 hover:text-black text-xl">✕</button>
    </div>
    <div id="uncleModalContent" class="p-6 text-sm leading-relaxed space-y-4"></div>
  </div>
</div>
<!-- ✅ 여기까지 -->

    <script>
        function toggleSidebar() {
            const sidebar = document.getElementById('sidebar');
            const overlay = document.getElementById('sidebar-overlay');
            sidebar.classList.toggle('open');
            overlay.classList.toggle('show');
        }

        const UNCLE_TERMS = {
    'terms': {
        'title': '최저가 쇼핑몰 서비스 이용약관',
        'content': `
            <b>제1조 (목적)</b><br>
            본 약관은 최저가 쇼핑몰(이하 “회사”)이 제공하는 구매대행 및 물류·배송 관리 서비스의 이용과 관련하여 회사와 이용자 간의 권리, 의무 및 책임사항을 규정함을 목적으로 합니다.<br><br>
            <b>제2조 (서비스의 성격 및 정의)</b><br>
            ① 회사는 이용자의 요청에 따라 상품을 대신 구매하고, 결제, 배송 관리, 고객 응대, 환불 처리 등 거래 전반을 회사가 직접 관리·운영하는 구매대행 서비스를 제공합니다.<br>
            ② 본 서비스는 <b>통신판매중개업(오픈마켓)이 아니며</b>, 회사가 거래 및 운영의 주체로서 서비스를 제공합니다.<br><br>
            <b>제4조 (회사의 역할 및 책임)</b><br>
            회사는 구매대행 과정에서 발생하는 주문, 결제, 배송, 환불 등 거래 전반에 대해 관계 법령에 따라 책임을 부담합니다.`
    },
    'privacy': {
        'title': '개인정보처리방침',
        'content': '<b>개인정보 수집 및 이용</b><br>수집항목: 이름, 연락처, 주소, 결제정보<br>이용목적: 상품 구매대행 및 송도 지역 직영 배송 서비스 제공<br>보관기간: 관련 법령에 따른 보존 기간 종료 후 즉시 파기'
    },
            'privacy': {
                'title': '개인정보처리방침',
                'content': '<b>개인정보의 수집 및 이용</b><br>최저가 쇼핑몰은 주문 처리, 상품 배송, 고객 상담을 위해 필수적인 개인정보를 수집하며, 관계 법령에 따라 안전하게 보호합니다.'
            },
            'agency': {
                'title': '서비스 이용 안내',
                'content': '<b>서비스 지역:</b> 인천광역시 연수구 송도동 일대 (인천대입구역 중심 동선)<br><b>운영 시간:</b> 평일 오전 9시 ~ 오후 6시<br><b>배송 원칙:</b> 신속하고 정확한 근거리 직접 배송'
            },
            'e_commerce': {
                'title': '전자상거래 이용자 유의사항',
                'content': '<b>거래 형태:</b> 본 서비스는 물류 인프라를 활용한 통합 유통 모델입니다.<br><b>환불 및 취소:</b> 상품 특성(신선식품 등)에 따라 환불이 제한될 수 있으며, 취소 시 이미 발생한 배송 비용이 청구될 수 있습니다.'
            }
        };

        function openUncleModal(type) {
            const data = UNCLE_TERMS[type];
            if(!data) return;
            document.getElementById('term-title').innerText = data.title;
            document.getElementById('term-modal-body').innerHTML = data.content;
            document.getElementById('term-modal').style.display = 'flex';
            document.body.style.overflow = 'hidden';
        }

        function closeUncleModal() {
            document.getElementById('term-modal').style.display = 'none';
            document.body.style.overflow = 'auto';
        }

        async function addToCart(productId) {
            try {
                const response = await fetch(`/cart/add/${productId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                if (response.redirected) { window.location.href = response.url; return; }
                const result = await response.json();
                if (result.success) {
                    showToast("장바구니에 상품을 담았습니다! 🧺");
                    const badge = document.getElementById('cart-count-badge');
                    if(badge) badge.innerText = result.cart_count;
                    if(window.location.pathname === '/cart') location.reload();
                } else { 
                    showToast(result.message || "추가 실패");
                }
            } catch (error) { 
                console.error('Error:', error); 
                showToast("일시적인 오류가 발생했습니다.");
            }
        }

        async function minusFromCart(productId) {
            try {
                const response = await fetch(`/cart/minus/${productId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const result = await response.json();
                if (result.success) {
                    const badge = document.getElementById('cart-count-badge');
                    if(badge) badge.innerText = result.cart_count;
                    location.reload(); 
                } else { alert(result.message); }
            } catch (error) { console.error('Error:', error); }
        }

        function showToast(msg) {
            const t = document.getElementById("toast");
            if(!t) return;
            t.innerText = msg;
            t.className = "show";
            setTimeout(() => { t.className = t.className.replace("show", ""); }, 2500);
        }

        function updateCountdowns() {
            const timers = document.querySelectorAll('.countdown-timer');
            const now = new Date().getTime();
            timers.forEach(timer => {
                if(!timer.dataset.deadline) { timer.innerText = "📅 상시판매"; return; }
                const deadline = new Date(timer.dataset.deadline).getTime();
                const diff = deadline - now;
                if (diff <= 0) {
                    timer.innerText = "판매마감";
                    const card = timer.closest('.product-card');
                    if (card && !card.classList.contains('sold-out')) { card.classList.add('sold-out'); }
                } else {
                    const h = Math.floor(diff / (1000 * 60 * 60));
                    const m = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
                    const s = Math.floor((diff % (1000 * 60)) / 1000);
                    timer.innerText = `📦 ${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')} 남음`;
                }
            });
        }
        setInterval(updateCountdowns, 1000);
        updateCountdowns();
        
        function execDaumPostcode() {
            new daum.Postcode({
                oncomplete: function(data) {
                    document.getElementById('address').value = data.address;
                    document.getElementById('address_detail').focus();
                }
            }).open();
        }
    </script>
<script>
function openUncleModal(type) {
  const title = document.getElementById('uncleModalTitle');
  const content = document.getElementById('uncleModalContent');

  const data = {
    terms: {
      title: '이용약관',
      content: `
      <p><strong>제1조 (목적)</strong><br>
      본 약관은 최저가 쇼핑몰(이하 "회사")이 제공하는 구매대행 및 배송 중개 서비스의 이용과 관련하여
      회사와 이용자 간의 권리, 의무 및 책임사항을 규정함을 목적으로 합니다.</p>

      <p><strong>제2조 (서비스의 정의)</strong><br>
      회사는 상품을 직접 판매하지 않으며,
      소비자의 요청에 따라 판매자(산지, 도매처 등)와 소비자를 연결하는
      구매대행 및 배송 중개 서비스를 제공합니다.</p>

      <p><strong>제3조 (서비스 이용 계약)</strong><br>
      이용자는 본 약관에 동의함으로써 서비스 이용 계약이 성립되며,
      결제 완료 시 구매대행 서비스 이용에 동의한 것으로 간주합니다.</p>

      <p><strong>제4조 (책임의 구분)</strong><br>
      상품의 품질, 원산지, 유통기한, 하자에 대한 책임은 판매자에게 있으며,
      회사는 주문 접수, 결제 처리, 배송 중개 및 고객 응대에 대한 책임을 집니다.</p>

      <p><strong>제5조 (면책 조항)</strong><br>
      천재지변, 배송사 사정, 판매자 사정 등 회사의 합리적인 통제 범위를 벗어난 사유로
      발생한 손해에 대하여 회사는 책임을 지지 않습니다.</p>
      `
    },

    privacy: {
      title: '개인정보처리방침',
      content: `
      <p><strong>1. 개인정보 수집 항목</strong><br>
      회사는 서비스 제공을 위해 다음과 같은 개인정보를 수집합니다.<br>
      - 필수항목: 이름, 휴대전화번호, 배송지 주소, 결제 정보</p>

      <p><strong>2. 개인정보 이용 목적</strong><br>
      수집된 개인정보는 다음 목적에 한하여 이용됩니다.<br>
      - 주문 처리 및 배송<br>
      - 고객 상담 및 민원 처리<br>
      - 결제 및 환불 처리</p>

      <p><strong>3. 개인정보 보관 및 이용 기간</strong><br>
      개인정보는 수집 및 이용 목적 달성 시까지 보관하며,
      관계 법령에 따라 일정 기간 보관 후 안전하게 파기합니다.</p>

      <p><strong>4. 개인정보 제3자 제공</strong><br>
      회사는 배송 및 주문 처리를 위해 판매자 및 배송업체에 한해
      최소한의 개인정보를 제공합니다.</p>

      <p><strong>5. 개인정보 보호</strong><br>
      회사는 개인정보 보호를 위해 기술적·관리적 보호 조치를 취하고 있습니다.</p>
      `
    },

    agency: {
      title: '이용안내',
      content: `
      <p><strong>서비스 안내</strong><br>
      최저가 쇼핑몰은 상품을 직접 보유하거나 판매하지 않는
      구매대행 및 배송 중개 플랫폼입니다.</p>

      <p><strong>주문 절차</strong><br>
      ① 이용자가 상품 선택 및 결제<br>
      ② 회사가 판매자에게 구매 요청<br>
      ③ 판매자가 상품 준비<br>
      ④ 배송을 통해 고객에게 전달</p>

      <p><strong>결제 안내</strong><br>
      결제 금액은 상품 대금과 배송비로 구성되며,
      구매대행 수수료는 별도로 청구되지 않습니다.</p>

      <p><strong>유의사항</strong><br>
      상품 정보는 판매자가 제공하며,
      실제 상품은 이미지와 다소 차이가 있을 수 있습니다.</p>
      `
    },

    e_commerce: {
      title: '전자상거래 유의사항',
      content: `
      <p><strong>1. 청약 철회 및 환불</strong><br>
      일반 상품의 경우 전자상거래법에 따라
      상품 수령 후 7일 이내 청약 철회가 가능합니다.</p>

      <p><strong>2. 농산물 및 신선식품</strong><br>
      농산물·신선식품은 특성상 단순 변심에 의한
      환불이 제한될 수 있습니다.</p>

      <p><strong>3. 환불 가능 사유</strong><br>
      - 상품 하자<br>
      - 오배송<br>
      - 상품 훼손</p>

      <p><strong>4. 환불 절차</strong><br>
      고객센터 접수 후 확인 절차를 거쳐
      결제 수단으로 환불 처리됩니다.</p>

      <p><strong>5. 분쟁 처리</strong><br>
      분쟁 발생 시 전자상거래 관련 법령 및
      소비자 분쟁 해결 기준을 따릅니다.</p>
      `
    }
  };

  title.innerText = data[type].title;
  content.innerHTML = data[type].content;
  document.getElementById('uncleModal').classList.remove('hidden');
  document.getElementById('uncleModal').classList.add('flex');
}

function closeUncleModal() {
  document.getElementById('uncleModal').classList.add('hidden');
  document.getElementById('uncleModal').classList.remove('flex');
}
</script>

</body>

</html>
"""

# --------------------------------------------------------------------------------
# 5. 비즈니스 로직 및 라우팅
# --------------------------------------------------------------------------------

# --------------------------------------------------------------------------------
# 5. 비즈니스 로직 및 라우팅 (보완 완료 버전)
# --------------------------------------------------------------------------------
@app.route('/admin/settlement/complete', methods=['POST'])
@login_required
def admin_settlement_complete():
    """마스터 관리자가 특정 카테고리의 매출을 정산 완료 처리"""
    if not current_user.is_admin:
        return jsonify({"success": False, "message": "권한이 없습니다."}), 403

    data = request.get_json()
    cat_name = data.get('category_name')
    amount = data.get('amount')
    manager_email = data.get('manager_email')

    try:
        # 1. 정산 기록 생성
        new_settle = Settlement(
            category_name=cat_name,
            manager_email=manager_email,
            total_sales=amount,
            settlement_amount=amount, # 실제로는 수수료 차감 로직 가능
            status='정산완료',
            completed_at=datetime.now()
        )
        db.session.add(new_settle)
        
        # 2. 해당 기간/카테고리의 주문 상태를 '정산완료'로 업데이트하고 싶다면 
        # 여기에 추가 로직을 작성할 수 있습니다. (현재는 기록만 남김)
        
        db.session.commit()
        return jsonify({"success": True, "message": f"{cat_name} 정산 처리가 완료되었습니다."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)})
    
@app.route('/admin/order/print')
@login_required
def admin_order_print():
    if not (current_user.is_admin or Category.query.filter_by(manager_email=current_user.email).first()):
        return "권한이 없습니다.", 403

    order_ids = request.args.get('ids', '').split(',')
    target_orders = Order.query.filter(Order.order_id.in_(order_ids)).all()

    # 데이터 가공 (마스킹 및 요약)
    processed_orders = []
    for o in target_orders:
        # 성함/번호 마스킹 동일
        name = o.customer_name or ""
        masked_name = name[0] + "*" * (len(name)-1) if len(name) > 1 else name
        
        phone = o.customer_phone or ""
        phone_parts = phone.split('-')
        masked_phone = f"{phone_parts[0]}-****-{phone_parts[2]}" if len(phone_parts) == 3 else "****"

        # ✅ 품목 전체 리스트화 (카테고리 기호 제거 및 깔끔하게 정리)
        raw_items = o.product_details.split('|')
        all_items = []
        for item in raw_items:
            # '[카테고리] 상품명(수량)'에서 상품명(수량)만 추출
            clean_item = item.split(']')[-1].strip() if ']' in item else item.strip()
            if clean_item:
                all_items.append(clean_item)

        # ✅ 현관 비밀번호 제외 로직 (숫자 포함 단어 필터링 강화)
        raw_memo = o.request_memo or ""
        clean_words = [w for w in raw_memo.split() if not (any(c.isdigit() for c in w) or any(k in w for k in ['비번', '번호', '현관', '#', '*']))]
        clean_memo = " ".join(clean_words) if clean_words else "요청사항 없음"

        processed_orders.append({
            'order_id': o.order_id,
            'masked_name': masked_name,
            'masked_phone': masked_phone,
            'all_items': all_items, # 전체 품목 리스트 전달
            'delivery_address': o.delivery_address,
            'clean_memo': clean_memo,
            'created_at': o.created_at
        })
# SyntaxWarning 방지를 위해 시작 부분에 r을 붙여 r""" 로 작성합니다.
    # SyntaxWarning 방지를 위해 시작 부분에 r을 붙여 r""" 로 작성합니다.
    invoice_html = r"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap');
            body { font-family: 'Noto Sans KR', sans-serif; background-color: #f1f1f1; margin: 0; padding: 0; }
            
            /* 송장 카드 사이즈 최적화 (A4 2분할용) */
            .invoice-card { 
                background: white; 
                width: 19cm; 
                height: 14.2cm; /* A4 반절(14.8cm) 보다 약간 작게 설정하여 밀림 방지 */
                margin: 0 auto; 
                border: 2px solid #000; 
                padding: 1.2rem; 
                box-sizing: border-box; 
                display: flex; 
                flex-direction: column;
                position: relative;
            }

            @media print {
                @page { size: A4; margin: 0; }
                .no-print { display: none; }
                body { background: white; }
                .invoice-card { 
                    border: 1.5px solid #000; 
                    margin: 0 auto;
                    page-break-inside: avoid; /* 카드 중간에 페이지가 잘리지 않게 함 */
                }
                /* 2번째 카드마다 강제 페이지 넘김 */
                .invoice-card:nth-child(even) { page-break-after: always; }
            }
            
            .item-list { max-height: 4.5cm; overflow: hidden; } 
            .line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        </style>
    </head>
    <body>
        <div class="no-print p-4 text-center bg-white border-b sticky top-0 z-50 shadow-md">
            <p class="text-sm font-bold text-blue-600 mb-2">총 {{ orders|length }}건의 주문이 확인되었습니다.</p>
            <button onclick="window.print()" class="bg-blue-600 text-white px-10 py-3 rounded-full font-black shadow-xl hover:bg-blue-700 transition">
                🖨️ 송장 출력하기 (A4 2분할)
            </button>
        </div>

        <div class="print-container">
            {% for o in orders %}
            <div class="invoice-card">
                <div class="flex justify-between items-center border-b-4 border-black pb-2 mb-3">
                    <h1 class="text-2xl font-black tracking-tighter text-green-700 italic">최저가 쇼핑몰</h1>
                    <p class="text-[11px] font-black bg-black text-white px-3 py-1 rounded">송도 전용 배송</p>
                </div>

                <div class="flex justify-between items-start mb-3">
                    <div class="w-2/3">
                        <p class="text-[9px] text-gray-400 font-black uppercase mb-1">Recipient</p>
                        <p class="text-4xl font-black text-gray-900 leading-none mb-2">{{ o.masked_name }}</p>
                        <p class="text-2xl font-black text-gray-700">{{ o.masked_phone }}</p>
                    </div>
                    <div class="w-1/3 text-right">
                        <p class="text-[9px] text-gray-400 font-black uppercase mb-1">Order ID</p>
                        <p class="text-xs font-black bg-gray-100 px-2 py-1 inline-block rounded">{{ o.order_id[-8:] }}</p>
                        <p class="text-[10px] text-gray-400 mt-1 font-bold">{{ o.created_at.strftime('%Y-%m-%d %H:%M') }}</p>
                    </div>
                </div>

                <div class="bg-gray-50 p-4 rounded-2xl border-l-8 border-green-600 mb-4">
                    <p class="text-[9px] text-gray-400 font-black mb-1 uppercase">Shipping Address</p>
                    <p class="text-xl font-black text-black leading-tight mb-2">{{ o.delivery_address }}</p>
                    <div class="bg-white px-3 py-2 rounded-lg border border-red-100 mt-1">
                        <p class="text-[11px] font-black text-red-600">
                            <i class="fas fa-exclamation-circle mr-1"></i>요청: {{ o.clean_memo }}
                        </p>
                    </div>
                </div>

                <div class="flex-grow overflow-hidden">
                    <p class="text-[9px] text-gray-400 font-black mb-2 border-b pb-1 uppercase tracking-widest">Order Items List</p>
                    <div class="item-list space-y-1.5">
                        {% for item in o.all_items %}
                        <div class="flex items-center justify-between border-b border-gray-50 pb-1">
                            <span class="text-[13px] font-black text-gray-800 line-clamp-1">□ {{ item }}</span>
                            <span class="text-[10px] text-gray-300 italic font-bold">check</span>
                        </div>
                        {% endfor %}
                    </div>
                </div>

                <div class="pt-3 border-t border-dashed border-gray-300 text-center opacity-40">
                    <p class="text-[9px] font-black italic tracking-[0.3em] uppercase">Premium Logistics Service by Basket Uncle</p>
                </div>
            </div>
            {% endfor %}
        </div>
    </body>
    </html>
    """
    return render_template_string(invoice_html, orders=processed_orders)
@app.context_processor
def inject_globals():
    """전역 템플릿 변수 주입"""
    cart_count = 0
    if current_user.is_authenticated:
        total_qty = db.session.query(db.func.sum(Cart.quantity)).filter(Cart.user_id == current_user.id).scalar()
        cart_count = total_qty if total_qty else 0
    categories = Category.query.order_by(Category.order.asc(), Category.id.asc()).all()
    managers = [c.manager_email for c in categories if c.manager_email]
    return dict(cart_count=cart_count, now=datetime.now(), managers=managers, nav_categories=categories)

@app.route('/search')
def search_view():
    """검색 결과 전용 페이지 (Jinja2 태그 누락 수정본)"""
    query = request.args.get('q', '').strip()
    if not query:
        return redirect(url_for('index'))

    # 1. 검색 결과 및 카테고리 그룹화
    search_products = Product.query.filter(Product.is_active == True, Product.name.contains(query)).all()
    grouped_search = {}
    for p in search_products:
        if p.category not in grouped_search: grouped_search[p.category] = []
        grouped_search[p.category].append(p)

    # 2. 하단 노출용 데이터
    recommend_cats = Category.query.order_by(Category.order.asc()).limit(3).all()
    cat_previews = {cat: Product.query.filter_by(category=cat.name, is_active=True).limit(4).all() for cat in recommend_cats}

    content = """
    <div class="max-w-7xl mx-auto px-4 md:px-6 py-12 md:py-20 text-left">
        <h2 class="text-2xl md:text-4xl font-black text-gray-800 mb-8">
            <span class="text-green-600">"{{ query }}"</span> 검색 결과 ({{ search_products|length }}건)
        </h2>

        {% if grouped_search %}
            {% for cat_name, products in grouped_search.items() %}
            <section class="mb-16">
                <h3 class="text-xl md:text-2xl font-black text-gray-700 mb-6 flex items-center gap-2">
                    <span class="w-1 h-6 bg-green-500 rounded-full"></span> {{ cat_name }} 카테고리
                </h3>
                <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-6">
                    {% for p in products %}
                    <div class="product-card bg-white rounded-3xl shadow-sm border border-gray-100 overflow-hidden relative flex flex-col transition-all hover:shadow-2xl {% if p.stock <= 0 %}sold-out{% endif %}">
                        <a href="/product/{{p.id}}" class="relative aspect-square block bg-white overflow-hidden">
                            <img src="{{ p.image_url }}" loading="lazy" class="w-full h-full object-cover p-2 md:p-6">
                        </a>
                        <div class="p-3 md:p-8 flex flex-col flex-1">
                            <h3 class="font-black text-gray-800 text-[11px] md:text-base mb-1 truncate">{{ p.name }}</h3>
                            <div class="mt-auto flex justify-between items-end">
                                <span class="text-[13px] md:text-2xl font-black text-green-600">{{ "{:,}".format(p.price) }}원</span>
                                <button onclick="addToCart('{{p.id}}')" class="bg-green-600 w-8 h-8 md:w-14 md:h-14 rounded-xl text-white flex items-center justify-center transition active:scale-90"><i class="fas fa-plus text-[10px] md:text-xl"></i></button>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </section>
            {% endfor %}
        {% else %}
            <div class="py-20 text-center bg-gray-50 rounded-[3rem] border-2 border-dashed border-gray-200 mb-20">
                <p class="text-gray-400 font-black text-lg">찾으시는 상품이 없습니다. 😥</p>
            </div>
        {% endif %}

        <hr class="border-gray-100 mb-20">
        
        <h3 class="text-xl md:text-3xl font-black text-gray-800 mb-10 italic">이런 상품은 어떠세요?</h3>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-10">
            {% for cat, prods in cat_previews.items() %}
            <div class="bg-gray-50 p-8 rounded-[3rem] border border-gray-100 shadow-inner">
                <h3 class="text-xl font-black mb-6">{{ cat.name }} <a href="/category/{{ cat.name }}" class="text-xs text-gray-400 ml-2">더보기 ></a></h3>
                <div class="grid grid-cols-2 gap-4">
                    {% for cp in prods %}
                    <a href="/product/{{ cp.id }}" class="bg-white p-3 rounded-2xl shadow-sm hover:scale-105 transition"><img src="{{ cp.image_url }}" class="w-full aspect-square object-contain"></a>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        </div>
        
        <div class="mt-20 text-center">
            <a href="/" class="inline-block bg-gray-800 text-white px-12 py-5 rounded-full font-black shadow-xl hover:bg-black transition">메인으로 이동</a>
        </div>
    </div>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, **locals())

@app.route('/')
def index():
    """메인 페이지 (디자인 유지)"""
    categories = Category.query.order_by(Category.order.asc()).all()
    grouped_products = {}
    order_logic = (Product.stock <= 0) | (Product.deadline < datetime.now())
    
    latest_all = Product.query.filter_by(is_active=True).order_by(Product.id.desc()).limit(20).all()
    random_latest = random.sample(latest_all, min(len(latest_all), 30)) if latest_all else []
    
    today_end = datetime.now().replace(hour=23, minute=59, second=59)
    closing_today = Product.query.filter(Product.is_active == True, Product.deadline > datetime.now(), Product.deadline <= today_end).order_by(Product.deadline.asc()).all()
    latest_reviews = Review.query.order_by(Review.created_at.desc()).limit(4).all()

    for cat in categories:
        prods = Product.query.filter_by(category=cat.name, is_active=True).order_by(order_logic, Product.id.desc()).all()
        if prods: grouped_products[cat] = prods
    
    content = """
<div class="bg-gray-900 text-white py-20 md:py-32 px-4 shadow-inner relative overflow-hidden text-center">
    <div class="max-w-7xl mx-auto relative z-10 font-black text-center">
        
        <span class="text-green-400 text-[10px] md:text-sm font-black mb-6 inline-block uppercase tracking-[0.3em]">
            Direct Delivery & Agency Service
        </span>

        <h1 class="hero-title text-3xl md:text-7xl font-black mb-8 leading-tight tracking-tighter break-keep">
            우리는 상품을 직접 팔지 않습니다.<br>
            <span class="text-green-500 uppercase">Premium 6PL Service</span>
        </h1>

        <div class="w-12 h-1 bg-white/20 mx-auto mb-8"></div>

        <p class="hero-desc text-gray-400 text-sm md:text-2xl font-bold max-w-3xl mx-auto mb-12 leading-relaxed md:leading-normal break-keep px-4">
            최저가 쇼핑몰은 재고를 쌓아두는 판매처가 아닌, <br class="hidden md:block">
            이용자의 요청에 따라 <span class="text-white underline decoration-green-500 decoration-2 md:decoration-4 underline-offset-8">구매와 배송을 책임 대행</span>하는 물류 인프라입니다.
        </p>

        <div class="flex flex-col md:flex-row justify-center items-center gap-6">
            <a href="#products"
               class="bg-green-600 text-white px-10 py-4 md:px-12 md:py-5 rounded-full font-black shadow-2xl hover:bg-green-700 transition active:scale-95">
                대행 서비스 이용하기
            </a>

            <a href="/about"
               class="text-white/60 hover:text-white font-bold border-b border-white/20 pb-1 transition text-xs md:text-base">
                6PL 구매대행이란? <i class="fas fa-arrow-right ml-2"></i>
            </a>
        </div>

    </div>
    <div class="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/dark-matter.png')] opacity-30"></div>
</div>

<div id="products" class="max-w-7xl mx-auto px-4 py-16 text-left">
    {% if latest_reviews %}
    <section class="mb-12 text-left">
        <div class="mb-6 flex justify-between items-end border-b border-gray-100 pb-4 text-left">
            <h2 class="text-xl md:text-3xl font-black text-gray-800 flex items-center gap-3 tracking-tighter"><span class="w-1.5 h-8 bg-orange-400 rounded-full"></span> 📸 생생한 구매 후기</h2>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-6 text-left">
            {% for r in latest_reviews %}
            <div class="bg-white rounded-[2rem] p-4 shadow-sm border border-gray-50 flex flex-col gap-3 transition hover:shadow-xl hover:-translate-y-1">
                <img src="{{ r.image_url }}" class="w-full aspect-square object-cover rounded-2xl bg-gray-50">
                <div>
                    <p class="text-[10px] text-gray-400 font-bold mb-1">{{ r.user_name[:1] }}**님 | {{ r.product_name }}</p>
                    <p class="text-[11px] font-bold text-gray-700 line-clamp-2 leading-relaxed">{{ r.content }}</p>
                </div>
            </div>
            {% endfor %}
        </div>
    </section>
    {% endif %}

    {% for cat, products in grouped_products.items() %}
    <section class="mb-12 text-left">
        <div class="mb-6 flex justify-between items-end border-b border-gray-100 pb-4 text-left">
            <div class="text-left">
                <h2 class="text-xl md:text-3xl font-black text-gray-800 flex items-center gap-3 tracking-tighter text-left"><span class="w-1.5 h-8 bg-green-500 rounded-full"></span> {{ cat.name }} 리스트</h2>
            </div>
            <a href="/category/{{ cat.name }}" class="text-[10px] md:text-sm font-bold text-gray-400 hover:text-green-600 flex items-center gap-1 transition">전체보기 <i class="fas fa-chevron-right text-[8px]"></i></a>
        </div>
        <div class="horizontal-scroll no-scrollbar text-left">
            {% for p in products %}
            <div class="product-card bg-white rounded-3xl md:rounded-[3rem] shadow-sm border border-gray-100 overflow-hidden relative flex flex-col w-[calc((100%-24px)/3)] md:w-[calc((100%-48px)/5)] transition-all hover:shadow-2xl {% if p.stock <= 0 %}sold-out{% endif %} text-left">
                
                {% if p.description %}
                <div class="absolute top-3 left-0 z-20">
        <span class="px-3 py-1 text-[10px] font-black text-white shadow-md rounded-r-full 
            {% if '당일' in p.description %} bg-red-500 
            {% else %} bg-blue-500 {% endif %}">
            {{ p.description }}
        </span>
    </div>
                {% endif %}

                <a href="/product/{{p.id}}" class="relative aspect-square block bg-white overflow-hidden text-left">
                    <img src="{{ p.image_url }}" loading="lazy" class="w-full h-full object-cover p-2 md:p-6 text-left">
                </a>
                <div class="p-3 md:p-8 flex flex-col flex-1 text-left">
                    <h3 class="font-black text-gray-800 text-[11px] md:text-base mb-1 truncate text-left">
                        {{ p.name }}
                        {% if p.badge %}
                        <span class="text-[9px] md:text-[11px] text-orange-500 font-bold ml-1">| {{ p.badge }}</span>
                        {% endif %}
                    </h3>
                    
                    <div class="flex items-center gap-1.5 mb-3 text-left">
                        <span class="text-[8px] md:text-[10px] text-gray-400 font-bold bg-gray-100 px-1.5 py-0.5 rounded">{{ p.spec or '일반' }}</span>
                    </div>

                    <div class="mt-auto flex justify-between items-end text-left">
                        <span class="text-[13px] md:text-2xl font-black text-green-600 text-left">{{ "{:,}".format(p.price) }}원</span>
                        <button onclick="addToCart('{{p.id}}')" class="bg-green-600 w-8 h-8 md:w-14 md:h-14 rounded-xl md:rounded-[1.5rem] text-white shadow-xl hover:bg-green-700 flex items-center justify-center transition active:scale-90"><i class="fas fa-plus text-[10px] md:text-xl"></i></button>
                    </div>
                </div>
            </div>
            {% endfor %} </div>
    </section>
    {% endfor %} </div>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, 
                                  grouped_products=grouped_products, 
                                  random_latest=random_latest, 
                                  closing_today=closing_today, 
                                  latest_reviews=latest_reviews)

# --- 상단 HEADER_HTML 내의 검색창 부분도 아래와 같이 반드시 수정되어야 합니다 ---
# (HEADER_HTML 변수를 찾아서 해당 부분의 action="/"을 action="/search"로 바꾸세요)
# 1. <form action="/search" method="GET" class="relative hidden md:block max-w-xs flex-1">
# 2. <form action="/search" method="GET" class="relative">
    """메인 페이지"""
    query = request.args.get('q', '').strip()
    categories = Category.query.order_by(Category.order.asc(), Category.id.asc()).all()
    grouped_products = {}
    
    order_logic = (Product.stock <= 0) | (Product.deadline < datetime.now())
    
    # 최신 상품 20개 중 8개 랜덤
    latest_all = Product.query.filter_by(is_active=True).order_by(Product.id.desc()).limit(20).all()
    random_latest = random.sample(latest_all, min(len(latest_all), 30)) if latest_all else []
    
    # 오늘 마감 상품
    today_end = datetime.now().replace(hour=23, minute=59, second=59)
    closing_today = Product.query.filter(
        Product.is_active == True,
        Product.deadline > datetime.now(),
        Product.deadline <= today_end
    ).order_by(Product.deadline.asc()).all()

    # 최신 리뷰 4개 (메인 노출)
    latest_reviews = Review.query.order_by(Review.created_at.desc()).limit(4).all()

    for cat in categories:
        q_obj = Product.query.filter_by(category=cat.name, is_active=True)
        if query: q_obj = q_obj.filter(Product.name.contains(query))
        products = q_obj.order_by(order_logic, Product.id.desc(), Product.deadline.asc()).all()
        if products: grouped_products[cat] = products
    
    content = """
   <div class="bg-gray-900 text-white py-20 md:py-32 px-4 shadow-inner relative overflow-hidden text-center">
    <div class="max-w-7xl mx-auto relative z-10 font-black text-center">
        
        <span class="text-green-400 text-[10px] md:text-sm font-black mb-6 inline-block uppercase tracking-[0.3em]">
            Direct Delivery Service
        </span>

        <h1 class="hero-title text-3xl md:text-7xl font-black mb-8 leading-tight tracking-tighter">
            우리는 상품을 판매하지 않습니다.<br>
            <span class="text-green-500 uppercase">Premium Service</span>
        </h1>

        <div class="w-12 h-1 bg-white/20 mx-auto mb-8"></div>

        <p class="hero-desc text-gray-400 text-sm md:text-2xl font-bold max-w-2xl mx-auto mb-12">
            판매가 아닌,
            <span class="text-white underline decoration-green-500 decoration-4 underline-offset-8">
                배송 서비스
            </span>
            입니다.
        </p>

        <div class="flex flex-col md:flex-row justify-center items-center gap-6">
            <a href="#products"
               class="bg-green-600 text-white px-10 py-4 md:px-12 md:py-5 rounded-full font-black shadow-2xl hover:bg-green-700 transition active:scale-95">
                쇼핑하러 가기
            </a>

            <a href="/about"
               class="text-white/60 hover:text-white font-bold border-b border-white/20 pb-1 transition text-xs md:text-base">
                최저가 쇼핑몰이란? <i class="fas fa-arrow-right ml-2"></i>
            </a>
        </div>

    </div>

    <div class="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/dark-matter.png')] opacity-30"></div>
</div>

    <div id="products" class="max-w-7xl mx-auto px-4 py-16 text-left">
        {% if query %}
            <p class="mb-8 font-black text-gray-400 text-lg md:text-xl border-b border-gray-100 pb-4 text-left">
                <span class="text-green-600">"{{ query }}"</span>에 대한 상품 검색 결과입니다.
            </p>
        {% endif %}

        {% if latest_reviews and not query %}
        <section class="mb-12 text-left">
            <div class="mb-6 flex justify-between items-end border-b border-gray-100 pb-4 text-left">
                <h2 class="text-xl md:text-3xl font-black text-gray-800 flex items-center gap-3 tracking-tighter">
                    <span class="w-1.5 h-8 bg-orange-400 rounded-full"></span> 📸 생생한 구매 후기
                </h2>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-6 text-left">
                {% for r in latest_reviews %}
                <div class="bg-white rounded-[2rem] p-4 shadow-sm border border-gray-50 flex flex-col gap-3 transition hover:shadow-xl hover:-translate-y-1">
                    <img src="{{ r.image_url }}" class="w-full aspect-square object-cover rounded-2xl bg-gray-50">
                    <div>
                        <p class="text-[10px] text-gray-400 font-bold mb-1">{{ r.user_name[:1] }}**님 | {{ r.product_name }}</p>
                        <p class="text-[11px] font-bold text-gray-700 line-clamp-2 leading-relaxed">{{ r.content }}</p>
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>
        {% endif %}

        {% if random_latest and not query %}
        <section class="mb-12 text-left">
            <div class="mb-6 flex justify-between items-end border-b border-gray-100 pb-4 text-left">
                <h2 class="text-xl md:text-3xl font-black text-gray-800 flex items-center gap-3 tracking-tighter">
                    <span class="w-1.5 h-8 bg-blue-500 rounded-full"></span> ✨ 최신 상품
                </h2>
                <a href="/category/최신상품" class="text-[10px] md:text-sm font-bold text-gray-400 hover:text-green-600 flex items-center gap-1 transition">
                    전체보기 <i class="fas fa-chevron-right text-[8px]"></i>
                </a>
            </div>
            <div class="horizontal-scroll no-scrollbar">
                {% for p in random_latest %}
                <div class="product-card bg-white rounded-3xl md:rounded-[3rem] shadow-sm border border-gray-100 overflow-hidden relative flex flex-col w-[calc((100%-24px)/3)] md:w-[calc((100%-48px)/5)] transition-all hover:shadow-2xl">
                    <a href="/product/{{p.id}}" class="relative aspect-square block bg-white overflow-hidden">
                        <img src="{{ p.image_url }}"loading="lazy" class="w-full h-full object-cover p-1.5 md:p-5" onerror="this.src='https://placehold.co/400x400?text={{ p.name }}'">
                        <div class="absolute top-2 left-2 md:top-4 md:left-4"><span class="bg-blue-500 text-white text-[7px] md:text-[10px] px-1.5 py-0.5 md:px-3 md:py-1 rounded md:rounded-lg uppercase font-black">NEW</span></div>
                    </a>
                    <div class="p-3 md:p-7 flex flex-col flex-1 text-left">
                        <h3 class="font-black text-gray-800 text-[11px] md:text-base truncate mb-0.5">{{ p.name }}</h3>
                        <p class="text-[9px] md:text-[11px] text-green-600 mb-2 font-medium truncate">{{ p.description or '' }}</p>
                        <div class="mt-auto flex justify-between items-end">
                            <span class="text-[13px] md:text-2xl text-gray-900 font-black tracking-tighter">{{ "{:,}".format(p.price) }}원</span>
                            <button onclick="addToCart('{{p.id}}')" class="bg-green-600 w-8 h-8 md:w-14 md:h-14 rounded-xl md:rounded-[1.5rem] text-white shadow-xl hover:bg-green-700 flex items-center justify-center transition active:scale-90"><i class="fas fa-plus text-[10px] md:text-xl"></i></button>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>
        {% endif %}

        {% if closing_today and not query %}
        <section class="mb-12 text-left">
            <div class="mb-6 flex justify-between items-end border-b border-gray-100 pb-4 text-left">
                <h2 class="text-xl md:text-3xl font-black text-gray-800 flex items-center gap-3 tracking-tighter">
                    <span class="w-1.5 h-8 bg-red-500 rounded-full"></span> 🔥 오늘 마감 임박!
                </h2>
                <a href="/category/오늘마감" class="text-[10px] md:text-sm font-bold text-gray-400 hover:text-green-600 flex items-center gap-1 transition">
                    전체보기 <i class="fas fa-chevron-right text-[8px]"></i>
                </a>
            </div>
            <div class="horizontal-scroll no-scrollbar">
                {% for p in closing_today %}
                <div class="product-card bg-white rounded-3xl md:rounded-[3rem] shadow-sm border border-red-50 overflow-hidden relative flex flex-col w-[calc((100%-24px)/3)] md:w-[calc((100%-48px)/5)] transition-all hover:shadow-2xl">
                    <a href="/product/{{p.id}}" class="relative aspect-square block bg-white overflow-hidden">
                        <img src="{{ p.image_url }}"loading="lazy" class="w-full h-full object-cover p-1.5 md:p-5">
                        <div class="absolute bottom-2 left-2 md:bottom-5 md:left-5"><span class="bg-red-600 text-white text-[7px] md:text-[10px] px-1.5 py-0.5 md:px-3 md:py-1 rounded md:rounded-lg font-black animate-pulse uppercase">CLOSING</span></div>
                    </a>
                    <div class="p-3 md:p-7 flex flex-col flex-1 text-left">
                        <p class="countdown-timer text-[8px] md:text-[10px] font-bold text-red-500 mb-1.5" data-deadline="{{ p.deadline.strftime('%Y-%m-%dT%H:%M:%S') if p.deadline else '' }}"></p>
                        <h3 class="font-black text-gray-800 text-[11px] md:text-base truncate mb-0.5">{{ p.name }}</h3>
                        <p class="text-[9px] md:text-[11px] text-green-600 mb-2 font-medium truncate">{{ p.description or '' }}</p>
                        <div class="mt-auto flex justify-between items-end">
                            <span class="text-[13px] md:text-2xl text-gray-900 font-black tracking-tighter">{{ "{:,}".format(p.price) }}원</span>
                            <button onclick="addToCart('{{p.id}}')" class="bg-green-600 w-8 h-8 md:w-14 md:h-14 rounded-xl md:rounded-[1.5rem] text-white shadow-xl hover:bg-green-700 flex items-center justify-center transition active:scale-90"><i class="fas fa-plus text-[10px] md:text-xl"></i></button>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </section>
        {% endif %}
        
        {% for cat, products in grouped_products.items() %}
        <section class="mb-12 text-left">
            <div class="mb-6 flex justify-between items-end border-b border-gray-100 pb-4 text-left">
                <div class="text-left">
                    <h2 class="text-xl md:text-3xl font-black text-gray-800 flex items-center gap-3 tracking-tighter text-left">
                        <span class="w-1.5 h-8 bg-green-500 rounded-full"></span> {{ cat.name }} 리스트
                    </h2>
                    {% if cat.description %}<p class="text-[11px] md:text-sm text-gray-400 mt-2 font-bold text-left">{{ cat.description }}</p>{% endif %}
                </div>
                <a href="/category/{{ cat.name }}" class="text-[10px] md:text-sm font-bold text-gray-400 hover:text-green-600 flex items-center gap-1 transition">
                    전체보기 <i class="fas fa-chevron-right text-[8px]"></i>
                </a>
            </div>
            <div class="horizontal-scroll no-scrollbar text-left">
                {% for p in products %}
                {% set is_expired = (p.deadline and p.deadline < now) %}
                <div class="product-card bg-white rounded-3xl md:rounded-[3rem] shadow-sm border border-gray-100 overflow-hidden relative flex flex-col w-[calc((100%-24px)/3)] md:w-[calc((100%-48px)/5)] transition-all hover:shadow-2xl {% if is_expired or p.stock <= 0 %}sold-out{% endif %} text-left">
                    {% if is_expired or p.stock <= 0 %}<div class="sold-out-badge text-[9px] md:text-xs text-center">판매마감</div>{% endif %}
                    <a href="/product/{{p.id}}" class="relative aspect-square block bg-white overflow-hidden text-left">
                        <img src="{{ p.image_url }}"loading="lazy" class="w-full h-full object-cover p-2 md:p-6 text-left">
                        <div class="absolute bottom-2 left-2 md:bottom-5 md:left-5 text-left">
                            <span class="bg-black/70 text-white text-[7px] md:text-[11px] px-2 py-1 rounded-md font-black backdrop-blur-sm">잔여: {{ p.stock }}</span>
                        </div>
                    </a>
                    <div class="p-3 md:p-8 flex flex-col flex-1 text-left">
                        <p class="countdown-timer text-[8px] md:text-[10px] font-bold text-red-500 mb-1.5 text-left" data-deadline="{{ p.deadline.strftime('%Y-%m-%dT%H:%M:%S') if p.deadline else '' }}"></p>
                        <h3 class="font-black text-gray-800 text-[11px] md:text-base truncate mb-0.5 text-left">{{ p.name }}</h3>
                        <p class="text-[9px] md:text-[11px] text-green-600 mb-2 font-medium truncate text-left">{{ p.description or '' }}</p>
                        <div class="mt-auto flex justify-between items-end text-left">
                            <span class="text-[13px] md:text-2xl font-black text-green-600 text-left">{{ "{:,}".format(p.price) }}원</span>
                            {% if not is_expired and p.stock > 0 %}
                            <button onclick="addToCart('{{p.id}}')" class="bg-green-600 w-8 h-8 md:w-14 md:h-14 rounded-xl md:rounded-[1.5rem] text-white shadow-xl hover:bg-green-700 flex items-center justify-center transition active:scale-90 text-center">
                                <i class="fas fa-plus text-[10px] md:text-xl"></i>
                            </button>
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% endfor %}
                <div class="w-4 md:w-10 flex-shrink-0"></div>
            </div>
        </section>
        {% endfor %}
    </div>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, grouped_products=grouped_products, random_latest=random_latest, closing_today=closing_today, latest_reviews=latest_reviews)

@app.route('/about')
def about_page():
    """제공된 HTML 형식을 반영한 최저가 쇼핑몰 브랜드 소개 페이지"""
    content = """
    <style>
        /* 소개 페이지 전용 스타일 */
        .about-body {
            margin: 0;
            background-color: #f9fafb;
            color: #111827;
            line-height: 1.7;
            font-family: "Pretendard", "Noto Sans KR", sans-serif;
        }

        .about-container {
            max-width: 1100px;
            margin: 0 auto;
            padding: 80px 20px;
            text-align: left; /* 왼쪽 정렬 유지 */
        }

        .about-container h1 {
            font-size: 42px;
            font-weight: 800;
            margin-bottom: 24px;
            letter-spacing: -0.02em;
        }

        .about-container h2 {
            font-size: 28px;
            font-weight: 800;
            margin: 80px 0 24px;
            color: #111827;
        }

        .about-container p {
            font-size: 17px;
            margin-bottom: 20px;
            color: #374151;
        }

        .about-container b {
            color: #111827;
        }

        .about-highlight {
            font-weight: 700;
            color: #059669;
        }

        /* Core Value Boxes */
        .core-values {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 24px;
            margin-top: 40px;
        }

        .value-box {
            background: #ffffff;
            border-radius: 20px;
            padding: 40px;
            text-align: center;
            box-shadow: 0 10px 30px rgba(0,0,0,0.05);
            border: 1px solid #f3f4f6;
        }

        .value-box span {
            display: block;
            font-size: 14px;
            font-weight: 700;
            color: #6b7280;
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }

        .value-box strong {
            font-size: 48px;
            color: #059669;
            font-weight: 900;
            font-style: italic;
        }

        /* Premium 6PL Model Section */
        .premium-section {
            margin-top: 100px;
            background: #111827;
            color: #ffffff;
            border-radius: 32px;
            padding: 60px 50px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
        }

        .premium-section h2 {
            color: #ffffff;
            margin-top: 0;
            font-size: 32px;
        }

        .premium-list {
            margin-top: 32px;
            padding: 0;
        }

        .premium-list li {
            list-style: none;
            font-size: 19px;
            margin-bottom: 18px;
            position: relative;
            padding-left: 32px;
            font-weight: 500;
            color: #d1d5db;
        }

        .premium-list li::before {
            content: "✔";
            position: absolute;
            left: 0;
            color: #10b981;
            font-weight: 900;
        }

        .premium-list li b {
            color: #ffffff;
        }

        /* Call To Action Button */
        .about-cta {
            text-align: center;
            margin-top: 100px;
            padding-bottom: 40px;
        }

        .about-cta a {
            display: inline-block;
            padding: 20px 48px;
            font-size: 20px;
            font-weight: 800;
            background: #059669;
            color: #ffffff;
            border-radius: 999px;
            text-decoration: none;
            transition: all 0.3s ease;
            box-shadow: 0 10px 20px rgba(5, 150, 105, 0.2);
        }

        .about-cta a:hover {
            background: #047857;
            transform: translateY(-3px);
            box-shadow: 0 15px 30px rgba(5, 150, 105, 0.3);
        }

        @media (max-width: 640px) {
            .about-container { padding: 60px 24px; }
            .about-container h1 { font-size: 32px; }
            .premium-section { padding: 40px 30px; }
            .value-box strong { font-size: 38px; }
        }
    </style>

    <div class="about-body">
        <div class="about-container">
    <h1>바구니 삼촌몰</h1>
    <p>
        바구니 삼촌몰은 <span class="about-highlight">물류 인프라를 직접 운영하며 주문 전 과정을 책임지는 구매대행 서비스</span>입니다.
    </p>
    <p>
        우리는 기존 유통의 불필요한 단계를 제거하기 위해 <b>상품 대리 구매 · 직영 물류 · 라스트마일 배송</b>을 하나의 시스템으로 통합했습니다.
    </p>
    <p>
        단순히 판매자와 구매자를 연결하는 중개 플랫폼이 아니라, 이용자의 요청을 받아 <span class="about-highlight">삼촌이 직접 검수하고 구매하여 문 앞까지 배송</span>하는 책임 대행 모델을 지향합니다.
    </p>
    <p>
        직구/구매대행 방식의 효율적인 물류 시스템을 통해 광고비와 유통 거품을 뺐으며, 그 혜택을 <b>상품의 실제 조달 원가와 합리적인 배송비</b>에 그대로 반영합니다.
    </p>

    <h2>Our Core Value</h2>
    <div class="core-values">
        <div class="value-box">
            <span>불필요 유통 마진</span>
            <strong>ZERO</strong>
        </div>
        <div class="value-box">
            <span>배송 책임 서비스</span>
            <strong>DIRECT</strong>
        </div>
    </div>

    <p style="margin-top: 60px; font-size: 19px; font-weight: 700; border-left: 4px solid #10b981; padding-left: 20px;">
        바구니 삼촌은 중개만 하는 장터가 아니라, <br>
        <span class="about-highlight">‘구매부터 배송까지 당사가 직접 책임지고 완료하는 대행 플랫폼’</span>입니다.
    </p>

            <div class="premium-section">
                <h2>Premium 6PL Model</h2>
                <ul class="premium-list">
                    <li><b>송도 생활권 중심</b>의 직영 배송 네트워크</li>
                    <li>산지 소싱부터 문 앞까지 <b>삼촌이 직접 관리</b></li>
                    <li>자체 기술 인프라를 통한 <b>압도적 비용 절감</b></li>
                    <li>불필요한 마케팅비를 뺀 <b>원가 중심 유통</b></li>
                    <li>가장 합리적인 유통을 <b>송도에서 실현</b></li>
                </ul>
            </div>

            <div class="about-cta">
                <a href="/">지금 상품 확인하기</a>
            </div>
        </div>
    </div>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML)
# [추가] 무한 스크롤을 위한 상품 데이터 제공 API
@app.route('/api/category_products/<string:cat_name>')
def api_category_products(cat_name):
    """무한 스크롤용 데이터 제공 API (20개 단위 고정)"""
    page = int(request.args.get('page', 1))
    per_page = 20  # 요청하신 대로 20개씩 나눕니다.
    offset = (page - 1) * per_page
    
    query = Product.query.filter_by(is_active=True)
    if cat_name == '최신상품':
        query = query.order_by(Product.id.desc())
    elif cat_name == '오늘마감':
        today_end = datetime.now().replace(hour=23, minute=59, second=59)
        query = query.filter(Product.deadline > datetime.now(), Product.deadline <= today_end).order_by(Product.deadline.asc())
    else:
        query = query.filter_by(category=cat_name).order_by(Product.id.desc())
    
    products = query.offset(offset).limit(per_page).all()
    
    res_data = []
    for p in products:
        res_data.append({
            "id": p.id,
            "name": p.name,
            "price": p.price,
            "image_url": p.image_url,
            "description": p.description or "",
            "stock": p.stock,
            "is_sold_out": (p.deadline and p.deadline < datetime.now()) or p.stock <= 0,
            "deadline": p.deadline.strftime('%Y-%m-%dT%H:%M:%S') if p.deadline else ""
        })
    return jsonify(res_data)
@app.route('/category/<string:cat_name>')
def category_view(cat_name):
    """카테고리별 상품 목록 뷰 (무한 스크롤 및 상세페이지 연결 완전 복구본)"""
    order_logic = (Product.stock <= 0) | (Product.deadline < datetime.now())
    cat = None
    limit_num = 20  # 요청하신 20개 단위 로딩 설정
    
    if cat_name == '최신상품':
        products = Product.query.filter_by(is_active=True).order_by(Product.id.desc()).limit(limit_num).all()
        display_name = "✨ 최신 상품"
    elif cat_name == '오늘마감':
        today_end = datetime.now().replace(hour=23, minute=59, second=59)
        products = Product.query.filter(Product.is_active == True, Product.deadline > datetime.now(), Product.deadline <= today_end).order_by(Product.deadline.asc()).limit(limit_num).all()
        display_name = "🔥 오늘 마감 임박!"
    else:
        cat = Category.query.filter_by(name=cat_name).first_or_404()
        products = Product.query.filter_by(category=cat_name, is_active=True).order_by(order_logic, Product.id.desc()).limit(limit_num).all()
        display_name = f"{cat_name} 상품 리스트"

    # 하단 추천 섹션 데이터
    latest_all = Product.query.filter(Product.is_active == True, Product.category != cat_name).order_by(Product.id.desc()).limit(10).all()
    recommend_cats = Category.query.filter(Category.name != cat_name).order_by(Category.order.asc()).limit(3).all()
    cat_previews = {c: Product.query.filter_by(category=c.name, is_active=True).limit(4).all() for c in recommend_cats}

    content = """
    <div class="max-w-7xl mx-auto px-4 md:px-6 py-20 text-left">
        <div class="mb-16 text-left">
            <h2 class="text-3xl md:text-5xl text-gray-800 font-black text-left">{{ display_name }}</h2>
            {% if cat and cat.description %}<p class="text-gray-400 font-bold mt-4 text-base md:text-xl text-left">{{ cat.description }}</p>{% endif %}
        </div>
        
        <div id="product-grid" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6 md:gap-10 text-left mb-12">
            {% for p in products %}
            <div class="product-card bg-white rounded-3xl md:rounded-[3rem] shadow-sm border border-gray-100 overflow-hidden relative flex flex-col transition-all hover:shadow-2xl {% if p.stock <= 0 %}sold-out{% endif %}">
                
                {% if p.description %}
                <div class="absolute top-4 left-0 z-20">
                    <span class="px-3 py-1.5 text-[9px] md:text-[11px] font-black text-white shadow-md rounded-r-full 
                        {% if '당일' in p.description %} bg-red-600 
                        {% elif '+1' in p.description %} bg-blue-600 
                        {% elif '+2' in p.description %} bg-emerald-600 
                        {% else %} bg-gray-600 {% endif %}">
                        <i class="fas fa-truck-fast mr-1"></i> {{ p.description }}
                    </span>
                </div>
                {% endif %}

                <a href="/product/{{p.id}}" class="relative aspect-square block bg-white overflow-hidden">
                    <img src="{{ p.image_url }}" loading="lazy" class="w-full h-full object-cover p-4 md:p-8">
                </a>
                <div class="p-5 md:p-10 flex flex-col flex-1 text-left">
                    <a href="/product/{{p.id}}">
                        <h3 class="font-black text-gray-800 text-sm md:text-lg truncate mb-2 text-left">{{ p.name }}</h3>
                    </a>
                    
                    <p class="text-[10px] md:text-xs text-gray-400 font-bold mb-3">{{ p.spec or '일반' }}</p>

                    <div class="mt-auto flex justify-between items-center text-left">
                        <span class="text-base md:text-2xl font-black text-green-600 text-left">{{ "{:,}".format(p.price) }}원</span>
                        <button onclick="addToCart('{{p.id}}')" class="bg-green-600 w-8 h-8 md:w-12 md:h-12 rounded-full text-white shadow-lg flex items-center justify-center transition active:scale-90 text-center">
                            <i class="fas fa-plus text-[10px] md:text-base"></i>
                        </button>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>

        <div id="load-more-trigger" class="w-full min-h-[100px] flex flex-col items-center justify-center py-10">
            <div id="spinner" class="w-10 h-10 border-4 border-green-100 border-t-green-600 rounded-full animate-spin hidden"></div>
            <div id="end-message" class="hidden text-gray-300 font-black text-lg py-4 w-full text-center">마지막 상품입니다. 😊</div>
        </div>

        <hr class="border-gray-100 mb-24">

        <div class="grid grid-cols-1 md:grid-cols-3 gap-10 text-left mb-24">
            {% for c_info, c_prods in cat_previews.items() %}
            <div class="bg-gray-50 p-6 md:p-8 rounded-[3rem] border border-gray-100 shadow-inner text-left">
                <h3 class="text-xl font-black mb-6 flex justify-between items-center text-left">
                    {{ c_info.name }}
                    <a href="/category/{{ c_info.name }}" class="text-xs text-gray-400 font-bold hover:text-green-600">전체보기 ></a>
                </h3>
                <div class="grid grid-cols-2 gap-4">
                    {% for cp in c_prods %}
                    <div class="bg-white p-3 rounded-2xl shadow-sm relative flex flex-col">
                        {% if cp.description %}
                        <div class="absolute top-2 left-0 z-20">
                            <span class="px-2 py-1 text-[7px] md:text-[9px] font-black text-white shadow-sm rounded-r-full 
                                {% if '당일' in cp.description %} bg-red-600 
                                {% elif '+1' in cp.description %} bg-blue-600 
                                {% elif '+2' in cp.description %} bg-emerald-600 
                                {% else %} bg-gray-600 {% endif %}">
                                {{ cp.description }}
                            </span>
                        </div>
                        {% endif %}

                        <a href="/product/{{ cp.id }}" class="block mb-2">
                            <img src="{{ cp.image_url }}" class="w-full aspect-square object-contain rounded-xl p-1">
                        </a>
                        <div class="px-1">
                            <p class="text-[10px] md:text-xs font-black text-gray-800 truncate">{{ cp.name }}</p>
                            <p class="text-[8px] md:text-[10px] text-gray-400 font-bold mb-1">{{ cp.spec or '일반' }}</p>
                            <p class="text-xs md:text-sm font-black text-green-600">{{ "{:,}".format(cp.price) }}원</p>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        </div>

        <div class="flex justify-center mt-24">
            <a href="/" class="bg-gray-800 text-white px-12 py-5 rounded-full font-black shadow-xl hover:bg-black transition active:scale-95 text-center">
                <i class="fas fa-home mr-2"></i> 메인화면으로 이동하기
            </a>
        </div>
    </div>

    <script>
    let page = 1;
    let loading = false;
    let hasMore = true;
    const catName = "{{ cat_name }}";

    async function loadMore() {
        if (loading || !hasMore) return;
        loading = true;
        document.getElementById('spinner').classList.remove('hidden');

        page++;
        try {
            const res = await fetch(`/api/category_products/${encodeURIComponent(catName)}?page=${page}&per_page=20`);
            const data = await res.json();

            if (!data || data.length === 0) {
                hasMore = false;
                document.getElementById('end-message').classList.remove('hidden');
                document.getElementById('spinner').classList.add('hidden');
                return;
            }

            const grid = document.getElementById('product-grid');
            data.forEach(p => {
                const soldOutClass = p.is_sold_out ? 'sold-out' : '';
                
                // ✅ 배송 일정 배지 색상 결정 로직 (JS)
                let badgeColor = 'bg-gray-600';
                if (p.description.includes('당일')) badgeColor = 'bg-red-600';
                else if (p.description.includes('+1')) badgeColor = 'bg-blue-600';
                else if (p.description.includes('+2')) badgeColor = 'bg-emerald-600';

                // ✅ 배송 일정 HTML
                const deliveryBadge = p.description ? `
                    <div class="absolute top-4 left-0 z-20">
                        <span class="px-3 py-1.5 text-[9px] md:text-[11px] font-black text-white shadow-md rounded-r-full ${badgeColor}">
                            <i class="fas fa-truck-fast mr-1"></i> ${p.description}
                        </span>
                    </div>` : '';

                const html = `
                    <div class="product-card bg-white rounded-3xl md:rounded-[3rem] shadow-sm border border-gray-100 overflow-hidden relative flex flex-col transition-all hover:shadow-2xl ${soldOutClass}">
                        ${deliveryBadge}
                        <a href="/product/${p.id}" class="relative aspect-square block bg-white overflow-hidden">
                            <img src="${p.image_url}" loading="lazy" class="w-full h-full object-cover p-4 md:p-10">
                        </a>
                        <div class="p-5 md:p-10 flex flex-col flex-1 text-left">
                            <a href="/product/${p.id}">
                                <h3 class="font-black text-gray-800 text-sm md:text-lg truncate mb-2 text-left">${p.name}</h3>
                            </a>
                            <div class="mt-auto flex justify-between items-center text-left">
                                <span class="text-base md:text-2xl font-black text-green-600 text-left">${p.price.toLocaleString()}원</span>
                                <button onclick="addToCart('${p.id}')" class="bg-green-600 w-8 h-8 md:w-12 md:h-12 rounded-full text-white shadow-lg flex items-center justify-center transition active:scale-90">
                                    <i class="fas fa-plus text-[10px] md:text-base"></i>
                                </button>
                            </div>
                        </div>
                    </div>`;
                grid.insertAdjacentHTML('beforeend', html);
            });

            if (data.length < 20) {
                hasMore = false;
                document.getElementById('end-message').classList.remove('hidden');
            }
        } catch (e) { console.error("Infinity Scroll Error:", e); }
        finally {
            loading = false;
            if (hasMore) document.getElementById('spinner').classList.add('hidden');
        }
    }

    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) loadMore();
    }, { threshold: 0.1, rootMargin: '300px' });

    observer.observe(document.getElementById('load-more-trigger'));
    </script>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, **locals())

@app.route('/product/<int:pid>')
def product_detail(pid):
    """상품 상세 정보 페이지 (최근등록상품 복구 및 추천 카테고리 추가 완료본)"""
    p = Product.query.get_or_404(pid)
    is_expired = (p.deadline and p.deadline < datetime.now())
    detail_images = p.detail_image_url.split(',') if p.detail_image_url else []
    cat_info = Category.query.filter_by(name=p.category).first()
    
    # 1. 연관 추천 상품: 키워드(상품명 첫 단어) 기반
    keyword = p.name.split()[0] if p.name else ""
    keyword_recommends = Product.query.filter(
        Product.name.contains(keyword),
        Product.id != pid,
        Product.is_active == True,
        Product.stock > 0
    ).limit(10).all()

    # 2. 최근 등록 상품 10개 (이 데이터가 정상적으로 전달되어야 합니다)
    latest_all = Product.query.filter(Product.is_active == True, Product.id != pid).order_by(Product.id.desc()).limit(10).all()
    
    # 3. 하단 노출용 추천 카테고리 3개 및 미리보기 상품
    recommend_cats_detail = Category.query.filter(Category.name != p.category).order_by(Category.order.asc()).limit(3).all()
    cat_previews_detail = {c: Product.query.filter_by(category=c.name, is_active=True).limit(4).all() for c in recommend_cats_detail}
    
    # 4. 리뷰 리스트
    product_reviews = Review.query.filter_by(product_id=pid).order_by(Review.created_at.desc()).all()

    content = """
    <div class="max-w-5xl mx-auto px-0 md:px-6 pb-40 font-black text-left">
        
        <div class="grid grid-cols-1 md:grid-cols-2 gap-0 md:gap-16 items-start">
            <div class="relative w-full aspect-square bg-white overflow-hidden md:rounded-[3rem] md:shadow-xl border-b md:border border-gray-100">
                {% if p.description %}
                <div class="absolute top-6 left-0 z-20">
                    <span class="px-5 py-2 text-xs md:text-sm font-black text-white shadow-xl rounded-r-full 
                        {% if '당일' in p.description %} bg-red-600 
                        {% elif '+1' in p.description %} bg-blue-600 
                        {% elif '+2' in p.description %} bg-emerald-600 
                        {% else %} bg-gray-600 {% endif %}">
                        <i class="fas fa-truck-fast mr-2"></i> {{ p.description }}
                    </span>
                </div>
                {% endif %}

                <img src="{{ p.image_url }}" class="w-full h-full object-contain p-6 md:p-12" loading="lazy">
                
                {% if is_expired or p.stock <= 0 %}
                <div class="absolute inset-0 bg-black/50 flex items-center justify-center backdrop-blur-[2px]">
                    <span class="text-white font-black text-2xl border-4 border-white px-8 py-3 rounded-2xl rotate-[-5deg]">판매마감</span>
                </div>
                {% endif %}
            </div>

            <div class="p-6 md:p-0 flex flex-col justify-start">
                <nav class="flex items-center gap-2 text-[10px] md:text-xs text-gray-400 mb-6 uppercase tracking-[0.2em] font-bold">
                    <a href="/" class="hover:text-green-600">Home</a>
                    <i class="fas fa-chevron-right text-[8px]"></i>
                    <a href="/category/{{ p.category }}" class="hover:text-green-600 text-green-600">{{ p.category }}</a>
                </nav>

                <h2 class="text-3xl md:text-5xl text-gray-900 mb-4 leading-tight tracking-tighter break-keep">
                    {{ p.name }}
                    {% if p.badge %}
                    <span class="block mt-2 text-orange-500 text-sm md:text-lg font-black italic tracking-normal">
                        # {{ p.badge }}
                    </span>
                    {% endif %}
                </h2>

                <div class="flex items-baseline gap-2 mb-10">
                    <span class="text-4xl md:text-6xl text-green-600 font-black italic tracking-tighter">{{ "{:,}".format(p.price) }}</span>
                    <span class="text-xl text-gray-400 font-bold">원</span>
                </div>

                <div class="grid grid-cols-2 gap-3 mb-10">
                    <div class="bg-gray-50 p-5 rounded-2xl border border-gray-100 shadow-sm">
                        <p class="text-[9px] text-gray-400 uppercase mb-1 font-black">Standard</p>
                        <p class="text-sm md:text-base font-black text-gray-700">{{ p.spec or '기본규격' }}</p>
                    </div>
                    <div class="bg-gray-50 p-5 rounded-2xl border border-gray-100 shadow-sm">
                        <p class="text-[9px] text-gray-400 uppercase mb-1 font-black">Stock Status</p>
                        <p class="text-sm md:text-base font-black text-gray-700">{{ p.stock }}개 남음</p>
                    </div>
                    <div class="bg-blue-50 p-5 rounded-2xl border border-blue-100 col-span-2 shadow-sm">
                        <p class="text-[9px] text-blue-400 uppercase mb-1 font-black">Direct Delivery (송도전용)</p>
                        <p class="text-sm md:text-base font-black text-blue-700">
                            <i class="fas fa-truck-fast mr-2"></i>최저가 쇼핑몰 {{ p.description }} 내 직접 배송
                        </p>
                    </div>
                </div>

             
<div class="hidden md:block">
    <div class="bg-gray-50 p-4 rounded-2xl mb-6 border border-gray-100">
        <p class="text-[11px] text-gray-500 leading-relaxed font-bold">
            <i class="fas fa-info-circle mr-1"></i> 최저가 쇼핑몰은 구매대행형 서비스로서 본 상품의 실제 판매처와 고객을 연결하고 결제 및 배송 전반을 책임 관리합니다.
        </p>
    </div>
    {% if p.stock > 0 and not is_expired %}
    <button onclick="addToCart('{{p.id}}')" class="w-full bg-green-600 text-white py-7 rounded-[2rem] font-black text-2xl shadow-2xl hover:bg-green-700 transition active:scale-95">장바구니 담기</button>
    {% else %}
    <button class="w-full bg-gray-200 text-gray-400 py-7 rounded-[2rem] font-black text-2xl cursor-not-allowed italic" disabled>판매가 마감되었습니다</button>
    {% endif %}
</div>
            </div>
        </div>

        <div class="mt-20 md:mt-32">
            <div class="sticky top-16 md:top-20 bg-white/90 backdrop-blur-md z-30 border-y border-gray-100 flex justify-around mb-12 shadow-sm">
                <a href="#details" class="py-5 px-4 text-sm font-black text-gray-800 border-b-4 border-green-600 transition-all">상세정보</a>
                <a href="#reviews" class="py-5 px-4 text-sm font-black text-gray-400 hover:text-orange-500 transition-all">구매후기 ({{ product_reviews|length }})</a>
                <a href="#related" class="py-5 px-4 text-sm font-black text-gray-400 hover:text-blue-500 transition-all">추천상품</a>
            </div>

            <div id="details" class="space-y-12 px-4 md:px-0">
    <div class="bg-gray-50 p-10 md:p-16 rounded-[2.5rem] text-left border border-gray-100 shadow-inner">
        <p class="text-orange-600 font-black text-sm mb-4">
            <i class="fas fa-truck-fast mr-2"></i>배송 안내: {{ p.description }}
        </p>
        <h3 class="text-2xl font-black text-gray-800 mb-6">상품 상세 설명</h3>
        <p class="text-lg text-gray-600 leading-relaxed font-bold">
            본 상품은 **{{ p.origin }}** 상품으로, {{ p.farmer }}에서 정성껏 준비했습니다.<br><br>
            최저가쇼핑몰이 직접 검수한 믿을 수 있는 품질! <br>
            복잡한 유통 과정을 생략하여 거품 없는 가격으로 고객님께 직접 배송해 드립니다.
        </p>
    </div>

    <div class="flex flex-col gap-0 max-w-4xl mx-auto">
        {% if detail_images %}
            {% for img in detail_images %}
            <img src="{{ img.strip() }}" class="w-full shadow-sm rounded-2xl mb-4" loading="lazy">
            {% endfor %}
        {% endif %}
    </div>
</div>
        </div>

        <div id="reviews" class="mt-40 px-4 md:px-0">
            <h3 class="text-2xl md:text-4xl font-black text-gray-900 mb-12 flex items-center gap-4 tracking-tighter">
                <span class="w-2 h-10 bg-orange-400 rounded-full"></span> 📸 생생한 구매 후기
            </h3>
            {% if product_reviews %}
            <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
                {% for r in product_reviews %}
                <div class="bg-white p-7 rounded-[2.5rem] border border-gray-100 shadow-sm flex flex-col sm:flex-row gap-6 hover:shadow-xl transition-all">
                    <img src="{{ r.image_url }}" class="w-full sm:w-32 h-32 rounded-3xl object-cover flex-shrink-0 bg-gray-50">
                    <div class="flex-1 text-left">
                        <div class="flex items-center justify-between mb-3">
                            <span class="text-xs font-black text-gray-800">{{ r.user_name[:1] }}**님</span>
                            <span class="text-[10px] text-gray-300 font-bold">{{ r.created_at.strftime('%Y.%m.%d') }}</span>
                        </div>
                        <p class="text-sm font-bold text-gray-600 leading-relaxed line-clamp-4">{{ r.content }}</p>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="py-24 text-center bg-gray-50 rounded-[3rem] border-2 border-dashed border-gray-200">
                <p class="text-gray-300 font-black text-lg">아직 등록된 후기가 없습니다. 첫 후기를 남겨보세요! 😊</p>
            </div>
            {% endif %}
        </div>

        <div id="related" class="mt-40">
            {% if keyword_recommends %}
            <div class="border-t border-gray-100 pt-24">
                <h3 class="font-black text-2xl md:text-4xl mb-12 flex items-center gap-4 tracking-tighter px-4 md:px-0">
                    <span class="w-2 h-10 bg-green-500 rounded-full"></span> ⭐ 연관 추천 상품
                </h3>
                <div class="horizontal-scroll no-scrollbar px-4 md:px-0">
                    {% for rp in keyword_recommends %}
                    <a href="/product/{{rp.id}}" class="group flex-shrink-0 w-44 md:w-64 relative">
                        {% if rp.description %}
                        <div class="absolute top-2 left-0 z-20">
                            <span class="px-2 py-1 text-[7px] md:text-[10px] font-black text-white shadow-sm rounded-r-full 
                                {% if '당일' in rp.description %} bg-red-600 {% elif '+1' in rp.description %} bg-blue-600 {% elif '+2' in rp.description %} bg-emerald-600 {% else %} bg-gray-600 {% endif %}">
                                {{ rp.description }}
                            </span>
                        </div>
                        {% endif %}
                        <div class="bg-white rounded-[2rem] border border-gray-100 p-4 shadow-sm transition hover:shadow-2xl hover:-translate-y-2 text-left h-full flex flex-col">
                            <img src="{{ rp.image_url }}" class="w-full aspect-square object-contain mb-4 rounded-2xl bg-gray-50 p-2">
                            <p class="text-xs md:text-sm font-black text-gray-800 truncate mb-1">{{ rp.name }}</p>
                            <p class="text-[9px] md:text-[11px] text-gray-400 font-bold mb-3">{{ rp.spec or '일반' }}</p>
                            <p class="text-sm md:text-lg font-black text-green-600 mt-auto">{{ "{:,}".format(rp.price) }}원</p>
                        </div>
                    </a>
                    {% endfor %}
                </div>
            </div>
            {% endif %}
        </div>

        {% if latest_all %}
        <div class="mt-20">
            <h3 class="font-black text-2xl md:text-4xl mb-12 flex items-center gap-4 tracking-tighter px-4 md:px-0">
                <span class="w-2 h-10 bg-blue-500 rounded-full"></span> ✨ 최근 등록 상품
            </h3>
            <div class="horizontal-scroll no-scrollbar px-4 md:px-0">
                {% for rp in latest_all %}
                <a href="/product/{{rp.id}}" class="group flex-shrink-0 w-44 md:w-64 relative">
                    {% if rp.description %}
                    <div class="absolute top-2 left-0 z-20">
                        <span class="px-2 py-1 text-[7px] md:text-[10px] font-black text-white shadow-sm rounded-r-full 
                            {% if '당일' in rp.description %} bg-red-600 {% elif '+1' in rp.description %} bg-blue-600 {% elif '+2' in rp.description %} bg-emerald-600 {% else %} bg-gray-600 {% endif %}">
                            {{ rp.description }}
                        </span>
                    </div>
                    {% endif %}
                    <div class="bg-white rounded-[2rem] border border-gray-100 p-4 shadow-sm transition hover:shadow-2xl hover:-translate-y-2 text-left h-full flex flex-col">
                        <img src="{{ rp.image_url }}" class="w-full aspect-square object-contain mb-4 rounded-2xl bg-gray-50 p-2">
                        <p class="text-xs md:text-sm font-black text-gray-800 truncate mb-1">{{ rp.name }}</p>
                        <p class="text-[9px] md:text-[11px] text-gray-400 font-bold mb-3">{{ rp.spec or '일반' }}</p>
                        <p class="text-sm md:text-lg font-black text-green-600 mt-auto">{{ "{:,}".format(rp.price) }}원</p>
                    </div>
                </a>
                {% endfor %}
            </div>
        </div>
        {% endif %}

        <div class="mt-40 border-t border-gray-100 pt-24 px-4 md:px-0">
            <h3 class="font-black text-2xl md:text-4xl mb-12 flex items-center gap-4 tracking-tighter text-left">
                <span class="w-2 h-10 bg-green-600 rounded-full"></span> 📦 카테고리 더 둘러보기
            </h3>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-10">
                {% for c_info in recommend_cats_detail %}
                <div class="bg-gray-50 p-6 md:p-8 rounded-[3rem] border border-gray-100 shadow-inner text-left">
                    <h3 class="text-lg md:text-xl font-black mb-6 flex justify-between items-center">
                        {{ c_info.name }}
                        <a href="/category/{{ c_info.name }}" class="text-xs text-gray-400 font-bold hover:text-green-600">전체보기 ></a>
                    </h3>
                    <div class="grid grid-cols-2 gap-4">
                        {% for cp in cat_previews_detail[c_info] %}
                        <div class="bg-white p-3 rounded-2xl shadow-sm relative flex flex-col">
                            {% if cp.description %}
                            <div class="absolute top-2 left-0 z-20">
                                <span class="px-2 py-1 text-[7px] font-black text-white shadow-sm rounded-r-full 
                                    {% if '당일' in cp.description %} bg-red-600 {% elif '+1' in cp.description %} bg-blue-600 {% elif '+2' in cp.description %} bg-emerald-600 {% else %} bg-gray-600 {% endif %}">
                                    {{ cp.description }}
                                </span>
                            </div>
                            {% endif %}
                            <a href="/product/{{ cp.id }}" class="block mb-2">
                                <img src="{{ cp.image_url }}" class="w-full aspect-square object-contain rounded-xl p-1 bg-gray-50">
                            </a>
                            <div class="px-1">
                                <p class="text-[10px] font-black text-gray-800 truncate">{{ cp.name }}</p>
                                <p class="text-[9px] text-gray-400 font-bold">{{ "{:,}".format(cp.price) }}원</p>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>

        <div class="mt-24 px-4 md:px-0 grid grid-cols-1 md:grid-cols-2 gap-6">
            <a href="/category/최신상품" class="bg-gray-800 text-white py-8 rounded-[2.5rem] text-center text-base font-black shadow-xl hover:bg-black transition flex items-center justify-center gap-4">
                <i class="fas fa-rocket text-xl text-blue-400"></i> 최신 상품 전체보기
            </a>
            <a href="/" class="bg-white border-2 border-green-600 text-green-600 py-8 rounded-[2.5rem] text-center text-base font-black shadow-sm hover:bg-green-50 transition flex items-center justify-center gap-4">
                <i class="fas fa-home text-xl"></i> 최저가 쇼핑몰 홈으로
            </a>
        </div>
    </div>

    <div class="fixed bottom-0 left-0 right-0 z-[100] md:hidden bg-white/80 backdrop-blur-2xl border-t border-gray-100 p-5 pb-10 shadow-[0_-20px_50px_rgba(0,0,0,0.1)]">
        <div class="max-w-xl mx-auto flex items-center gap-4">
            <a href="/cart" class="relative bg-gray-100 w-16 h-16 rounded-2xl flex items-center justify-center text-gray-500 active:scale-90 transition-all">
                <i class="fas fa-shopping-cart text-xl"></i>
                <span class="absolute -top-2 -right-2 bg-red-500 text-white text-[10px] font-black w-7 h-7 flex items-center justify-center rounded-full border-2 border-white shadow-lg">{{ cart_count }}</span>
            </a>
            {% if p.stock > 0 and not is_expired %}
            <button onclick="addToCart('{{p.id}}')" class="flex-1 bg-green-600 text-white h-16 rounded-2xl font-black text-lg shadow-xl shadow-green-100 active:scale-95 transition-all">장바구니 담기</button>
            {% else %}
            <button class="flex-1 bg-gray-200 text-gray-400 h-16 rounded-2xl font-black text-sm cursor-not-allowed" disabled>판매 마감</button>
            {% endif %}
        </div>
    </div>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, 
                                  p=p, is_expired=is_expired, detail_images=detail_images, 
                                  cat_info=cat_info, latest_all=latest_all, 
                                  keyword_recommends=keyword_recommends, 
                                  product_reviews=product_reviews,
                                  recommend_cats_detail=recommend_cats_detail,
                                  cat_previews_detail=cat_previews_detail)
@app.route('/category/seller/<int:cid>')
def seller_info_page(cid):
    """판매 사업자 정보 상세 페이지"""
    cat = Category.query.get_or_404(cid)
    content = """
    <div class="max-w-xl mx-auto py-24 md:py-32 px-6 font-black text-left">
        <nav class="mb-12 text-left"><a href="javascript:history.back()" class="text-green-600 font-black hover:underline flex items-center gap-2"><i class="fas fa-arrow-left"></i> 이전으로 돌아가기</a></nav>
        <div class="bg-white rounded-[3rem] md:rounded-[5rem] shadow-2xl border border-gray-100 overflow-hidden text-left">
            <div class="bg-green-600 p-12 md:p-16 text-white text-center">
                <div class="w-20 h-20 md:w-24 md:h-24 bg-white/20 rounded-full flex items-center justify-center mx-auto mb-8 text-3xl md:text-4xl text-center"><i class="fas fa-store"></i></div>
                <h2 class="text-3xl md:text-4xl font-black tracking-tight mb-3 italic uppercase text-center">Business Info</h2>
                <p class="opacity-80 font-bold text-sm md:text-lg text-center">본 상품의 실제 판매 사업자 정보입니다.</p>
            </div>
            
            <div class="p-10 md:p-20 space-y-10 md:space-y-14 text-left">
                <div class="text-left"><p class="text-[10px] text-gray-400 uppercase tracking-[0.3em] mb-3 font-black text-left">Company Name</p><p class="text-2xl md:text-3xl text-gray-800 font-black text-left">상호명 : {{ cat.biz_name or '-' }}</p></div>
                <div class="grid grid-cols-2 gap-10 text-left">
                    <div class="text-left"><p class="text-[10px] text-gray-400 uppercase tracking-[0.3em] mb-3 font-black text-left">Representative</p><p class="text-gray-800 font-black text-lg md:text-xl text-left">대표자 : {{ cat.biz_representative or '-' }}</p></div>
                    <div class="text-left"><p class="text-[10px] text-gray-400 uppercase tracking-[0.3em] mb-3 font-black text-left">Tax ID</p><p class="text-gray-800 font-black text-lg md:text-xl text-left">{{ cat.biz_reg_number or '-' }}</p></div>
                </div>
                <div class="text-left"><p class="text-[10px] text-gray-400 uppercase tracking-[0.3em] mb-3 font-black text-left">Location</p><p class="text-gray-700 font-bold leading-relaxed text-sm md:text-lg text-left">{{ cat.biz_address or '-' }}</p></div>
                <div class="p-8 md:p-12 bg-gray-50 rounded-[2rem] md:rounded-[3rem] border border-dashed border-gray-200 text-left"><p class="text-[10px] text-gray-400 uppercase tracking-[0.3em] mb-3 font-black text-left">Inquiry Center</p><p class="text-green-600 text-2xl md:text-4xl font-black italic text-left">{{ cat.biz_contact or '-' }}</p></div>
            </div>
            
            <div class="bg-gray-50 p-8 text-center border-t border-gray-100 text-[11px] text-gray-400 font-black uppercase tracking-[0.5em] text-center">
                바구니 삼촌 Premium Service
            </div>
        </div>
    </div>"""
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, cat=cat)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """로그인 라우트"""
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form.get('email')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            # --- 세션 고정 활성화 추가 ---
            session.permanent = True # 앱 설정에서 정한 30분 타이머가 작동하기 시작합니다.
            # ---------------------------
            login_user(user); return redirect('/')
        flash("로그인 정보를 다시 한 번 확인해주세요.")
    return render_template_string(HEADER_HTML + """
    <div class="max-w-md mx-auto mt-24 p-10 md:p-16 bg-white rounded-[3rem] md:rounded-[4rem] shadow-2xl border text-left">
        <h2 class="text-3xl font-black text-center mb-16 text-green-600 uppercase italic tracking-tighter text-center">Login</h2>
        <form method="POST" class="space-y-8 text-left">
            <div class="space-y-2 text-left">
                <label class="text-[10px] text-gray-300 font-black uppercase tracking-widest ml-4 text-left">ID (Email)</label>
                <input name="email" type="email" placeholder="email@example.com" class="w-full p-6 bg-gray-50 rounded-3xl font-black focus:ring-4 focus:ring-green-100 outline-none text-sm text-left" required>
            </div>
            <div class="space-y-2 text-left">
                <label class="text-[10px] text-gray-300 font-black uppercase tracking-widest ml-4 text-left">Password</label>
                <input name="password" type="password" placeholder="••••••••" class="w-full p-6 bg-gray-50 rounded-3xl font-black focus:ring-4 focus:ring-green-100 outline-none text-sm text-left" required>
            </div>
            <button class="w-full bg-green-600 text-white py-6 rounded-3xl font-black text-lg md:text-xl shadow-xl hover:bg-green-700 transition active:scale-95 text-center">로그인</button>
        </form>
        <div class="text-center mt-10 text-center"><a href="/register" class="text-gray-400 text-xs font-black hover:text-green-600 transition text-center text-center">아직 회원이 아니신가요? 회원가입</a></div>
    </div>""" + FOOTER_HTML)

@app.route('/register', methods=['GET', 'POST'])
def register():
    """회원가입 라우트 (전자상거래 동의 포함)"""
    if request.method == 'POST':
        name, email, pw, phone = request.form['name'], request.form['email'], request.form['password'], request.form['phone']
        addr, addr_d, ent_pw, memo = request.form['address'], request.form['address_detail'], request.form['entrance_pw'], request.form['request_memo']
        
        # 송도동 체크
        if "송도동" not in (addr or ""):
            flash("최저가 쇼핑몰은 현재 송도동 지역 전용 서비스입니다. 배송지 주소를 확인해주세요."); return redirect('/register')

        if not request.form.get('consent_e_commerce'):
            flash("전자상거래 이용 약관 및 유의사항에 동의해야 합니다."); return redirect('/register')

        if User.query.filter_by(email=email).first(): flash("이미 가입된 이메일입니다."); return redirect('/register')
        new_user = User(email=email, password=generate_password_hash(pw), name=name, phone=phone, address=addr, address_detail=addr_d, entrance_pw=ent_pw, request_memo=memo)
        db.session.add(new_user); db.session.commit(); return redirect('/login')
    return render_template_string(HEADER_HTML + """
    <div class="max-w-md mx-auto mt-12 mb-24 p-10 md:p-16 bg-white rounded-[3rem] md:rounded-[4rem] shadow-2xl border text-left">
        <h2 class="text-2xl md:text-3xl font-black mb-12 tracking-tighter uppercase text-green-600 text-left">Join Us</h2>
        <form method="POST" class="space-y-6 text-left">
            <div class="space-y-4 text-left">
                <input name="name" placeholder="실명 (성함)" class="w-full p-5 bg-gray-50 rounded-2xl font-black text-sm text-left" required>
                <input name="email" type="email" placeholder="이메일 주소" class="w-full p-5 bg-gray-50 rounded-2xl font-black text-sm text-left" required>
                <input name="password" type="password" placeholder="비밀번호" class="w-full p-5 bg-gray-50 rounded-2xl font-black text-sm text-left" required>
                <input name="phone" placeholder="휴대폰 번호 ( - 제외 )" class="w-full p-5 bg-gray-50 rounded-2xl font-black text-sm text-left" required>
            </div>
            
            <div class="space-y-4 border-t border-gray-100 pt-6 text-left">
                <div class="flex gap-2 text-left text-left">
                    <input id="address" name="address" placeholder="인천광역시 연수구 송도동..." class="flex-1 p-5 bg-gray-100 rounded-2xl font-black text-xs md:text-sm text-left" readonly onclick="execDaumPostcode()">
                    <button type="button" onclick="execDaumPostcode()" class="bg-gray-800 text-white px-6 rounded-2xl font-black text-xs text-center">검색</button>
                </div>
                <input name="address_detail" placeholder="상세주소 (동/호수)" class="w-full p-5 bg-gray-50 rounded-2xl font-black text-sm text-left" required>
                <input name="entrance_pw" placeholder="공동현관 비밀번호 (필수)" class="w-full p-5 bg-red-50 rounded-2xl font-black border border-red-100 text-sm text-left" required>
                <textarea name="request_memo" placeholder="배송 시 요청사항을 남겨주세요" class="w-full p-5 bg-white border border-gray-100 rounded-2xl font-black h-28 text-sm text-left"></textarea>
            </div>
            
            <div class="p-5 bg-gray-50 rounded-2xl border border-gray-100 text-[10px] space-y-3 mt-6 text-left">
                <label class="flex items-start gap-3 cursor-pointer group text-left text-left">
                    <span class="group-hover:text-gray-800 transition leading-normal md:leading-relaxed break-keep text-[11px] md:text-sm">
    [필수] 본 서비스는 <b>구매대행형 통합 물류 서비스</b>이며, 이용자의 주문 요청에 따라 당사가 상품을 구매 및 배송함을 확인하고 이에 동의합니다.
</span>
                </label>
            </div>

            <button class="w-full bg-green-600 text-white py-6 rounded-3xl font-black text-lg shadow-xl mt-6 hover:bg-green-700 transition active:scale-95 text-center text-center">가입 완료</button>
        </form>
    </div>""" + FOOTER_HTML)

@app.route('/logout')
def logout(): 
    """로그아웃"""
    logout_user(); return redirect('/')
@app.route('/mypage/update_address', methods=['POST'])
@login_required
def update_address():
    """마이페이지 주소 업데이트 및 강제 데이터 갱신"""
    addr = request.form.get('address')
    addr_d = request.form.get('address_detail')
    ent_pw = request.form.get('entrance_pw')

    if not addr or "송도동" not in addr:
        flash("최저가 쇼핑몰은 송도 전용 서비스입니다. 주소에 '송도동'이 포함되어야 합니다. 😊")
        return redirect(url_for('mypage'))

    try:
        # 1. DB 데이터 업데이트
        current_user.address = addr
        current_user.address_detail = addr_d
        current_user.entrance_pw = ent_pw
        
        # 2. 변경사항 저장 및 객체 새로고침 (핵심)
        db.session.commit()
        db.session.refresh(current_user) 
        
        flash("회원 정보가 성공적으로 수정되었습니다! ✨")
    except Exception as e:
        db.session.rollback()
        flash("저장 중 오류가 발생했습니다.")
        print(f"Error: {e}")

    return redirect(url_for('mypage'))

@app.route('/mypage')
@login_required
def mypage():
    """마이페이지 (최종 완성본: 폰트 최적화 및 한글화 버전)"""
    db.session.refresh(current_user)
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    
    # ✅ 품목별 금액을 포함한 상세 텍스트 생성 로직 유지
    enhanced_orders = []
    for o in orders:
        details_with_price = []
        parts = o.product_details.split(' | ')
        for part in parts:
            match = re.search(r'\[(.*?)\] (.*?)\((\d+)\)', part)
            if match:
                cat_n, p_name, qty = match.groups()
                p_obj = Product.query.filter_by(name=p_name.strip()).first()
                price = p_obj.price if p_obj else 0
                line_total = price * int(qty)
                details_with_price.append(f"{p_name.strip()}({qty}개) --- {line_total:,}원")
            else:
                details_with_price.append(part)
        
        o.enhanced_details = "\\n".join(details_with_price)
        enhanced_orders.append(o)

    content = """
    <div class="max-w-4xl mx-auto py-8 md:py-12 px-4 font-black text-left">
        <div class="flex justify-between items-center mb-10 px-1">
            <a href="/" class="text-gray-400 hover:text-green-600 transition flex items-center gap-1.5 text-sm font-bold">
                <i class="fas fa-home"></i> 홈으로
            </a>
            <a href="/logout" class="text-gray-400 hover:text-red-500 transition flex items-center gap-1.5 text-sm font-black">
                로그아웃 <i class="fas fa-sign-out-alt"></i>
            </a>
        </div>

        <div class="bg-white rounded-[2.5rem] shadow-sm border border-gray-100 mb-10 overflow-hidden">
            <div class="p-8 md:p-12">
                <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-6 mb-10">
                    <div class="text-left">
                        <span class="bg-green-100 text-green-700 text-[10px] px-3 py-1 rounded-lg tracking-widest uppercase mb-3 inline-block font-black">우수 회원</span>
                        <h2 class="text-3xl md:text-4xl font-black text-gray-800 leading-tight">
                            {{ current_user.name }} <span class="text-gray-400 font-medium text-xl">님</span>
                        </h2>
                        <p class="text-gray-400 text-sm mt-1 font-bold">{{ current_user.email }}</p>
                    </div>
                    <button onclick="toggleAddressEdit()" id="edit-btn" class="bg-gray-50 text-gray-600 px-6 py-3 rounded-xl text-sm font-black hover:bg-gray-100 transition border border-gray-100">
                        <i class="fas fa-edit mr-1"></i> 주소 수정
                    </button>
                </div>

                <div class="pt-8 border-t border-gray-50 text-left">
                    <div id="address-display" class="grid md:grid-cols-2 gap-4">
                        <div class="bg-gray-50/50 p-6 rounded-3xl border border-gray-50">
                            <p class="text-[10px] text-gray-400 uppercase mb-2 tracking-widest font-black">기본 배송지</p>
                            <p class="text-gray-700 text-base md:text-lg leading-snug font-black">
                                {{ current_user.address or '정보 없음' }}<br>
                                <span class="text-gray-400 text-sm font-bold">{{ current_user.address_detail or '' }}</span>
                            </p>
                        </div>
                        <div class="bg-orange-50/30 p-6 rounded-3xl border border-orange-50">
                            <p class="text-[10px] text-orange-400 uppercase mb-2 tracking-widest font-black">공동현관 비밀번호</p>
                            <p class="text-orange-600 text-lg md:text-xl flex items-center gap-2 font-black">
                                <span class="text-2xl">🔑</span> {{ current_user.entrance_pw or '미등록' }}
                            </p>
                        </div>
                    </div>

                    <form id="address-edit-form" action="/mypage/update_address" method="POST" class="hidden space-y-4">
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div class="space-y-3">
                                <div class="flex gap-2">
                                    <input id="address" name="address" value="{{ current_user.address or '' }}" class="flex-1 p-5 bg-gray-50 rounded-2xl text-sm font-black border-none" readonly onclick="execDaumPostcode()" placeholder="주소 검색">
                                    <button type="button" onclick="execDaumPostcode()" class="bg-gray-800 text-white px-6 rounded-2xl text-xs font-black">검색</button>
                                </div>
                                <input name="address_detail" value="{{ current_user.address_detail or '' }}" class="w-full p-5 bg-gray-50 rounded-2xl text-sm font-black border-none" required placeholder="상세주소">
                            </div>
                            <div class="space-y-3">
                                <input name="entrance_pw" value="{{ current_user.entrance_pw or '' }}" class="w-full p-5 bg-orange-50 rounded-2xl text-sm font-black border-none" required placeholder="공동현관 비밀번호">
                                <div class="flex gap-2">
                                    <button type="button" onclick="toggleAddressEdit()" class="flex-1 py-5 bg-gray-100 text-gray-400 rounded-2xl text-sm font-black">취소</button>
                                    <button type="submit" class="flex-[2] py-5 bg-green-600 text-white rounded-2xl text-sm font-black shadow-lg">저장하기</button>
                                </div>
                            </div>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <h3 class="text-xl md:text-2xl font-black text-gray-800 mb-8 flex items-center gap-3 px-1">
            <span class="w-1.5 h-8 bg-green-500 rounded-full"></span> 최근 주문 내역
        </h3>

        <div class="space-y-6 text-left">
            {% if orders %}
                {% for o in orders %}
                <div class="bg-white p-6 md:p-8 rounded-[2.5rem] border border-gray-100 flex flex-col md:flex-row justify-between items-start md:items-center gap-6 transition-all hover:shadow-md">
                    <div class="text-left flex-1">
                        <div class="flex items-center gap-3 mb-2">
                            <span class="text-xs text-gray-400 font-bold">{{ o.created_at.strftime('%Y.%m.%d') }}</span>
                            <span class="text-xs font-black {% if o.status == '결제취소' %}text-red-400{% else %}text-green-500{% endif %}">
                                [{{ o.status }}]
                            </span>
                        </div>
                        <p class="text-lg md:text-xl font-black text-gray-700 leading-tight">
                            {{ o.product_details.split('|')[0][:40] }}...
                        </p>
                    </div>
                    <div class="flex items-center justify-between w-full md:w-auto gap-8">
                        <span class="text-xl md:text-2xl font-black text-gray-800 tracking-tighter">
                            {{ "{:,}".format(o.total_price) }}원
                        </span>
                        <div class="flex gap-2">
                            <button onclick='openReceiptModal({{ o.id }}, "{{ o.enhanced_details }}", "{{ o.total_price }}", "{{ o.delivery_address }}", "{{ o.order_id }}", "{{ o.delivery_fee }}")' class="text-xs font-black text-gray-400 bg-gray-50 px-4 py-2.5 rounded-xl border border-gray-100 hover:bg-gray-100 transition">영수증</button>
                            
                            {% if o.status == '결제완료' %}
                                {% set existing_review = Review.query.filter_by(order_id=o.id).first() %}
                                {% if existing_review %}
                                    <button class="text-xs font-black text-gray-300 bg-gray-50 px-4 py-2.5 rounded-xl border border-gray-100 cursor-not-allowed" disabled>작성완료</button>
                                {% else %}
                                    <button onclick='openReviewModal({{ o.id }}, "{{ o.product_details.split("(")[0] }}")' class="text-xs font-black text-orange-500 bg-orange-50 px-4 py-2.5 rounded-xl border border-orange-100 hover:bg-orange-100 transition shadow-sm">후기작성</button>
                                {% endif %}
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="py-32 text-center bg-white rounded-[2.5rem] border-2 border-dashed border-gray-100">
                    <p class="text-gray-300 text-lg font-black">아직 주문 내역이 없습니다. 😊</p>
                </div>
            {% endif %}
        </div>
    </div>

    <div id="receipt-modal" class="fixed inset-0 bg-black/60 z-[6000] hidden flex items-center justify-center p-4 backdrop-blur-sm">
        <div id="printable-receipt" class="bg-white w-full max-w-sm rounded-2xl overflow-hidden shadow-2xl animate-in zoom-in duration-200 flex flex-col">
            <div class="p-5 bg-gray-50 border-b border-gray-100 flex justify-between items-center no-print">
                <h4 class="text-xs font-black uppercase tracking-widest text-gray-500">신용카드 매출전표</h4>
                <button onclick="closeReceiptModal()" class="text-gray-300 text-2xl hover:text-black transition">✕</button>
            </div>
            
            <div class="p-8 space-y-8 text-left bg-white">
                <div class="text-center border-b-2 border-gray-800 pb-6">
                    <h3 class="text-2xl font-black text-gray-900 mb-2 italic">최저가 쇼핑몰</h3>
                    <div class="text-[10px] text-gray-500 font-bold space-y-1">
                        <p>사업자번호: 472-93-02262</p>
                        <p>대표: 금창권 | 고객센터: 1666-8320</p>
                        <p>인천광역시 연수구 하모니로158, D동 317호</p>
                    </div>
                </div>

                <div class="space-y-5 font-bold">
                    <div class="flex justify-between text-xs font-black"><span class="text-gray-400">주문번호</span><span id="modal-order-id" class="text-gray-700"></span></div>
                    <div>
                        <p class="text-[10px] text-gray-400 uppercase font-black mb-2 tracking-widest">구매 내역</p>
                        <p id="modal-items" class="text-gray-800 text-sm leading-relaxed whitespace-pre-wrap border-y border-gray-50 py-4 font-black"></p>
                    </div>
                    <div>
                        <p class="text-[10px] text-gray-400 uppercase font-black mb-2 tracking-widest">배송지</p>
                        <p id="modal-address" class="text-gray-700 text-xs font-black"></p>
                    </div>
                </div>

                <div class="pt-6 border-t-4 border-double border-gray-200 flex justify-between items-center">
                    <span class="text-base font-black text-gray-800">합계 금액</span>
                    <span id="modal-total" class="text-3xl font-black text-green-600 italic tracking-tighter"></span>
                </div>
                <div class="text-center opacity-30 pt-4"><p class="text-[9px] font-black uppercase tracking-[0.4em]">이용해 주셔서 감사합니다</p></div>
            </div>

            <div class="p-6 bg-gray-50 flex gap-3 no-print">
                <button onclick="closeReceiptModal()" class="flex-1 py-5 bg-gray-200 text-gray-500 rounded-2xl text-sm font-black">닫기</button>
                <button onclick="printReceipt()" class="flex-[2] py-5 bg-gray-800 text-white rounded-2xl text-sm font-black shadow-lg hover:bg-black transition">출력하기</button>
            </div>
        </div>
    </div>

    <div id="review-modal" class="fixed inset-0 bg-black/60 z-[6000] hidden flex items-center justify-center p-4 backdrop-blur-sm">
        <div class="bg-white w-full max-w-sm rounded-[2.5rem] overflow-hidden shadow-2xl">
            <div class="p-6 bg-orange-500 text-white flex justify-between items-center">
                <h4 class="text-base font-black">📸 소중한 후기 작성</h4>
                <button onclick="closeReviewModal()" class="text-white/60 text-2xl hover:text-white transition">✕</button>
            </div>
            <form action="/review/add" method="POST" enctype="multipart/form-data" class="p-8 space-y-6 text-left">
                <input type="hidden" name="order_id" id="review-order-id">
                <input type="hidden" name="rating" id="review-rating-value" value="5">
                <div>
                    <p id="review-product-name" class="text-gray-800 font-black text-sm mb-4"></p>
                    <div class="flex gap-2 text-3xl text-gray-200" id="star-rating-container">
                        {% for i in range(1, 6) %}<i class="fas fa-star cursor-pointer transition-colors" data-value="{{i}}"></i>{% endfor %}
                    </div>
                </div>
                <div class="space-y-2">
                    <label class="text-[10px] text-gray-400 font-black ml-2 uppercase">사진 첨부</label>
                    <input type="file" name="review_image" class="w-full text-xs p-4 bg-gray-50 rounded-2xl border border-dashed border-gray-200" required accept="image/*">
                </div>
                <textarea name="content" class="w-full p-5 h-32 bg-gray-50 rounded-2xl border-none text-sm font-black" placeholder="맛과 신선함은 어땠나요? 다른 이웃들을 위해 솔직한 후기를 남겨주세요! 😊" required></textarea>
                <button type="submit" class="w-full py-5 bg-green-600 text-white rounded-[1.5rem] text-base font-black shadow-xl shadow-green-100 hover:bg-green-700 transition">등록 완료</button>
            </form>
        </div>
    </div>

    <style>
        @media print {
            .no-print { display: none !important; }
            body * { visibility: hidden; }
            #printable-receipt, #printable-receipt * { visibility: visible; }
            #printable-receipt { position: absolute; left: 0; top: 0; width: 100%; box-shadow: none; border: none; }
        }
    </style>

    <script>
        function toggleAddressEdit() {
            const f = document.getElementById('address-edit-form');
            const d = document.getElementById('address-display');
            const b = document.getElementById('edit-btn');
            const isHidden = f.classList.contains('hidden');
            f.classList.toggle('hidden', !isHidden);
            d.classList.toggle('hidden', isHidden);
            b.innerHTML = isHidden ? '<i class="fas fa-times"></i> 취소' : '<i class="fas fa-edit mr-1"></i> 주소 수정';
        }

        function openReceiptModal(id, items, total, address, orderFullId, deliveryFee) {
            document.getElementById('modal-order-id').innerText = orderFullId || ('ORD-' + id);
            let itemText = items.replace(/\\\\n/g, '\\n');
            const fee = parseInt(deliveryFee) || 0;
            if (fee > 0) { itemText += "\\n[배송비] --- " + fee.toLocaleString() + "원"; }
            else { itemText += "\\n[배송비] --- 0원 (무료)"; }
            document.getElementById('modal-items').innerText = itemText;
            document.getElementById('modal-address').innerText = address;
            document.getElementById('modal-total').innerText = Number(total).toLocaleString() + '원';
            document.getElementById('receipt-modal').classList.remove('hidden');
        }

        function closeReceiptModal() { document.getElementById('receipt-modal').classList.add('hidden'); }
        function printReceipt() { window.print(); }

        const stars = document.querySelectorAll('#star-rating-container i');
        const ratingInput = document.getElementById('review-rating-value');
        stars.forEach(star => {
            star.addEventListener('click', function() {
                ratingInput.value = this.dataset.value;
                updateStars(this.dataset.value);
            });
            star.addEventListener('mouseover', function() { updateStars(this.dataset.value); });
            star.addEventListener('mouseleave', function() { updateStars(ratingInput.value); });
        });
        function updateStars(value) {
            stars.forEach(s => {
                const active = parseInt(s.dataset.value) <= parseInt(value);
                s.classList.toggle('text-orange-400', active);
                s.classList.toggle('text-gray-200', !active);
            });
        }

        function openReviewModal(oid, pName) {
            document.getElementById('review-order-id').value = oid;
            document.getElementById('review-product-name').innerText = pName;
            ratingInput.value = 5; updateStars(5);
            document.getElementById('review-modal').classList.remove('hidden');
        }
        function closeReviewModal() { document.getElementById('review-modal').classList.add('hidden'); }
    </script>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, orders=enhanced_orders, Review=Review)
@app.route('/order/cancel/<int:oid>', methods=['POST'])
@login_required
def order_cancel(oid):
    """결제 취소 로직 (재고 복구 포함)"""
    order = Order.query.get_or_404(oid)
    if order.user_id != current_user.id: return redirect('/mypage')
    if order.status != '결제완료': 
        flash("취소 가능한 상태가 아닙니다. 이미 배송이 시작되었을 수 있습니다."); return redirect('/mypage')
    
    # 1. 상태 변경
    order.status = '결제취소'
    
    # 2. 재고 복구 (주문 상세 텍스트 파싱)
    try:
        parts = order.product_details.split(' | ')
        for part in parts:
            item_match = re.search(r'\] (.*?)\((\d+)\)', part)
            if item_match:
                p_name, qty = item_match.groups()
                p = Product.query.filter_by(name=p_name.strip()).first()
                if p: p.stock += int(qty)
    except Exception as e:
        print(f"Stock recovery error: {str(e)}")
            
    db.session.commit()
    flash("결제가 성공적으로 취소되었습니다. 환불은 카드사 정책에 따라 3~7일 소요될 수 있습니다."); 
    return redirect('/mypage')

@app.route('/review/add', methods=['POST'])
@login_required
def review_add():
    """사진 리뷰 등록 (주문당 1개 제한 로직 적용)"""
    oid = request.form.get('order_id')
    content = request.form.get('content')
    
    # 1. [검증] 해당 주문에 이미 작성된 후기가 있는지 체크
    existing_review = Review.query.filter_by(order_id=oid).first()
    if existing_review:
        flash("이미 후기를 작성하신 주문입니다. 😊")
        return redirect('/mypage')
        
    order = Order.query.get(oid)
    if not order or order.user_id != current_user.id: 
        return redirect('/mypage')
    
    img_path = save_uploaded_file(request.files.get('review_image'))
    if not img_path: 
        flash("후기 사진 등록은 필수입니다."); return redirect('/mypage')
    
    # 리뷰 대상 상품 정보 파싱
    p_name = order.product_details.split('(')[0].split(']')[-1].strip()
    match = re.search(r'\[(.*?)\] (.*?)\(', order.product_details)
    p_id = 0
    if match:
        first_p = Product.query.filter_by(name=match.group(2).strip()).first()
        if first_p: p_id = first_p.id

    # 2. [저장] Review 생성 시 order_id를 함께 기록 (필수)
    new_review = Review(
        user_id=current_user.id, 
        user_name=current_user.name, 
        product_id=p_id, 
        product_name=p_name, 
        content=content, 
        image_url=img_path,
        order_id=oid # 어떤 주문에 대한 후기인지 저장
    )
    db.session.add(new_review)
    db.session.commit()
    flash("소중한 후기가 등록되었습니다. 감사합니다!"); 
    return redirect('/mypage')

@app.route('/cart/add/<int:pid>', methods=['POST'])
@login_required
def add_cart(pid):
    """장바구니 추가 (판매중 체크 포함)"""
    p = Product.query.get_or_404(pid)
    if (p.deadline and p.deadline < datetime.now()) or p.stock <= 0: 
        return jsonify({"success": False, "message": "판매가 마감된 상품입니다."})
    
    item = Cart.query.filter_by(user_id=current_user.id, product_id=pid).first()
    if item: item.quantity += 1
    else: db.session.add(Cart(user_id=current_user.id, product_id=pid, product_name=p.name, product_category=p.category, price=p.price, tax_type=p.tax_type))
    
    db.session.commit()
    total_qty = db.session.query(db.func.sum(Cart.quantity)).filter(Cart.user_id == current_user.id).scalar() or 0
    return jsonify({"success": True, "cart_count": total_qty})

@app.route('/cart/minus/<int:pid>', methods=['POST'])
@login_required
def minus_cart(pid):
    """장바구니 수량 차감"""
    item = Cart.query.filter_by(user_id=current_user.id, product_id=pid).first()
    if item:
        if item.quantity > 1: item.quantity -= 1
        else: db.session.delete(item)
    db.session.commit()
    total_qty = db.session.query(db.func.sum(Cart.quantity)).filter(Cart.user_id == current_user.id).scalar() or 0
    return jsonify({"success": True, "cart_count": total_qty})

@app.route('/cart/delete/<int:pid>', methods=['POST'])
@login_required
def delete_cart(pid): 
    """장바구니 항목 삭제"""
    Cart.query.filter_by(user_id=current_user.id, product_id=pid).delete(); db.session.commit(); return redirect('/cart')

@app.route('/cart')
@login_required
def cart():
    """장바구니 화면 (한글화 및 폰트 사이즈 최적화 버전)"""
    items = Cart.query.filter_by(user_id=current_user.id).all()
    
    # 배송비 계산 로직 유지
    cat_price_sums = {}
    for i in items: 
        cat_price_sums[i.product_category] = cat_price_sums.get(i.product_category, 0) + (i.price * i.quantity)
    
    delivery_fee = sum([( (amt // 50001) + 1) * 1900 for amt in cat_price_sums.values()]) if items else 0
    subtotal = sum(i.price * i.quantity for i in items)
    total = subtotal + delivery_fee
    
    # 상단 헤더 및 빈 장바구니 처리
    content = f"""
    <div class="max-w-4xl mx-auto py-10 md:py-20 px-4 md:px-6 font-black text-left">
        <h2 class="text-2xl md:text-3xl font-black mb-10 border-l-8 border-green-600 pl-4 md:pl-6 tracking-tighter uppercase italic">
            장바구니
        </h2>
        
        <div class="bg-white rounded-[2rem] md:rounded-[3rem] shadow-xl border border-gray-50 overflow-hidden">
            {" " if items else f'''
            <div class="py-32 md:py-48 text-center">
                <p class="text-7xl md:text-8xl mb-8 opacity-20">🧺</p>
                <p class="text-lg md:text-2xl mb-10 text-gray-400 font-bold">장바구니가 비어있습니다.</p>
                <a href="/" class="inline-block bg-green-600 text-white px-10 py-4 rounded-full shadow-lg font-black text-base md:text-lg hover:bg-green-700 transition">
                    인기 상품 보러가기
                </a>
            </div>
            '''}
    """

    # 장바구니 상품 리스트
    if items:
        content += '<div class="p-6 md:p-12 space-y-8">'
        for i in items:
            content += f"""
            <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-gray-50 pb-8 gap-4">
                <div class="flex-1 text-left">
                    <p class="text-[10px] text-green-600 font-black mb-1 uppercase tracking-widest">[{ i.product_category }]</p>
                    <p class="font-black text-lg md:text-xl text-gray-800 leading-tight mb-2">{ i.product_name }</p>
                    <p class="text-gray-400 font-bold text-sm">{ "{:,}".format(i.price) }원</p>
                </div>
                
                <div class="flex items-center justify-between w-full md:w-auto gap-4">
                    <div class="flex items-center gap-6 bg-gray-50 px-5 py-3 rounded-2xl border border-gray-100">
                        <button onclick="minusFromCart({i.product_id})" class="text-gray-400 hover:text-red-500 transition text-xl">
                            <i class="fas fa-minus"></i>
                        </button>
                        <span class="font-black text-lg w-6 text-center">{ i.quantity }</span>
                        <button onclick="addToCart({i.product_id})" class="text-gray-400 hover:text-green-600 transition text-xl">
                            <i class="fas fa-plus"></i>
                        </button>
                    </div>
                    
                    <form action="/cart/delete/{i.product_id}" method="POST" class="md:ml-4">
                        <button class="text-gray-200 hover:text-red-500 transition text-2xl p-2">
                            <i class="fas fa-trash-alt"></i>
                        </button>
                    </form>
                </div>
            </div>
            """
        
        # 결제 요약 영역
        content += f"""
            <div class="bg-gray-50 p-8 md:p-10 rounded-[2rem] md:rounded-[2.5rem] space-y-4 mt-12 border border-gray-100">
                <div class="flex justify-between text-sm md:text-base text-gray-500 font-bold">
                    <span>주문 상품 합계</span>
                    <span>{ "{:,}".format(subtotal) }원</span>
                </div>
                <div class="flex justify-between text-sm md:text-base text-orange-500 font-bold">
                    <span>카테고리별 배송료</span>
                    <span>+ { "{:,}".format(delivery_fee) }원</span>
                </div>
                <div class="flex justify-between items-center pt-6 border-t border-gray-200 mt-6">
                    <span class="text-lg md:text-xl text-gray-800 font-black">최종 결제 금액</span>
                    <span class="text-3xl md:text-5xl text-green-600 font-black italic tracking-tighter">
                        { "{:,}".format(total) }원
                    </span>
                </div>
                <p class="text-[10px] md:text-xs text-gray-400 mt-6 leading-relaxed font-medium">
                    ※ 배송비 안내: 카테고리별 기본 1,900원이며, 합계 금액 50,000원 초과 시 50,000원 단위로 1,900원이 추가 가산됩니다.
                </p>
            </div>
            
            <a href="/order/confirm" class="block text-center bg-green-600 text-white py-6 md:py-8 rounded-[1.5rem] md:rounded-[2rem] font-black text-xl md:text-2xl shadow-xl shadow-green-100 mt-12 hover:bg-green-700 hover:-translate-y-1 transition active:scale-95">
                주문하기
            </a>
        </div>
        """

    content += "</div>"
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, items=items, subtotal=subtotal, delivery_fee=delivery_fee, total=total)
@app.route('/order/confirm')
@login_required
def order_confirm():
    """결제 전 최종 확인 (한글화 및 폰트 최적화 버전)"""
    items = Cart.query.filter_by(user_id=current_user.id).all()
    if not items: return redirect('/')
    
    cat_price_sums = {}
    for i in items: 
        cat_price_sums[i.product_category] = cat_price_sums.get(i.product_category, 0) + (i.price * i.quantity)
    
    delivery_fee = sum([( (amt // 50001) + 1) * 1900 for amt in cat_price_sums.values()])
    total = sum(i.price * i.quantity for i in items) + delivery_fee
    
    # 송도동 배송지 체크
    is_songdo = "송도동" in (current_user.address or "")

    content = f"""
    <div class="max-w-xl mx-auto py-12 md:py-20 px-4 md:px-6 font-black text-left">
        <h2 class="text-2xl md:text-3xl font-black mb-10 border-b-4 border-green-600 pb-4 text-center uppercase italic">
            주문 확인
        </h2>
        
        <div class="bg-white p-8 md:p-12 rounded-[2.5rem] md:rounded-[3.5rem] shadow-2xl border border-gray-50 space-y-10 text-left">
            
            <div class="p-6 md:p-8 {'bg-green-50 border-green-100' if is_songdo else 'bg-red-50 border-red-100'} rounded-3xl border relative overflow-hidden">
                <span class="{'text-green-600' if is_songdo else 'text-red-600'} text-[10px] block uppercase font-black mb-3 tracking-widest">
                    배송지 정보
                </span>
                <p class="text-lg md:text-xl text-gray-800 font-black leading-snug">
                    { current_user.address or '정보 없음' }<br>
                    <span class="text-gray-500">{ current_user.address_detail or '' }</span>
                </p>
                <p class="mt-4 font-black text-sm">
                    {'<span class="text-green-600 flex items-center gap-2"><i class="fas fa-check-circle"></i> 송도동 배송 가능 지역입니다.</span>' if is_songdo else '<span class="text-red-600 flex items-center gap-2"><i class="fas fa-exclamation-triangle"></i> 배송 불가 지역 (송도동 전용 서비스)</span>'}
                </p>
            </div>

            {f'<div class="p-6 bg-red-100 rounded-2xl text-red-700 text-xs md:text-sm font-bold leading-relaxed">⚠️ 최저가 쇼핑몰은 인천 송도동 지역만 배송하는 서비스입니다. 주소를 수정해 주세요.</div>' if not is_songdo else ''}

            <div class="space-y-4 pt-4">
                <div class="flex justify-between items-end font-black">
                    <span class="text-gray-400 text-xs uppercase tracking-widest">최종 결제 금액</span>
                    <span class="text-4xl md:text-5xl text-green-600 font-black italic underline underline-offset-8">
                        { "{:,}".format(total) }원
                    </span>
                </div>
                <div class="bg-orange-50 p-5 rounded-2xl border border-orange-100 text-[10px] md:text-xs text-orange-700 font-bold leading-relaxed">
                    📢 배송비 안내: 카테고리별 5만원 단위 1,900원 가산 (현재 배송비: { "{:,}".format(delivery_fee) }원)
                </div>
            </div>

            <div class="p-6 md:p-8 bg-gray-50 rounded-3xl text-[11px] md:text-xs text-gray-500 space-y-6 font-black border border-gray-100">
                <label class="flex items-start gap-4 cursor-pointer group">
                    <input type="checkbox" id="consent_agency" class="mt-1 w-4 h-4 rounded-full border-gray-300 text-green-600 focus:ring-green-500" required>
                    <span class="group-hover:text-gray-800 transition leading-relaxed">
                        [필수] 본인은 최저가 쇼핑몰이 상품 판매자가 아니며, 요청에 따라 구매 및 배송을 대행하는 서비스임을 확인하고 이에 동의합니다.
                    </span>
                </label>
                <label class="flex items-start gap-4 pt-4 border-t border-gray-200 cursor-pointer group">
                    <input type="checkbox" id="consent_third_party_order" class="mt-1 w-4 h-4 rounded-full border-gray-300 text-green-600 focus:ring-green-500" required>
                    <span class="group-hover:text-gray-800 transition leading-relaxed">
                        [필수] 개인정보 제3자 제공 동의: 원활한 배송 처리를 위해 판매처 및 배송 담당자에게 정보가 제공됨을 확인했습니다.
                    </span>
                </label>
            </div>

            {f'<button onclick="startPayment()" class="w-full bg-green-600 text-white py-6 md:py-8 rounded-[1.5rem] md:rounded-[2rem] font-black text-xl md:text-2xl shadow-xl shadow-green-100 hover:bg-green-700 transition active:scale-95">안전 결제하기</button>' if is_songdo else '<button class="w-full bg-gray-300 text-white py-6 md:py-8 rounded-[1.5rem] md:rounded-[2rem] font-black text-xl cursor-not-allowed" disabled>배송지를 확인해 주세요</button>'}
            
        </div>
    </div>

    <script>
    function startPayment() {{ 
        if(!document.getElementById('consent_agency').checked) {{ 
            alert("구매 대행 서비스 이용 동의가 필요합니다."); 
            return; 
        }} 
        if(!document.getElementById('consent_third_party_order').checked) {{ 
            alert("개인정보 제공 동의가 필요합니다."); 
            return; 
        }} 
        window.location.href = "/order/payment"; 
    }}
    </script>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML, total=total, delivery_fee=delivery_fee, is_songdo=is_songdo)
@app.route('/order/payment')
@login_required
def order_payment():
    """토스페이먼츠 결제창 호출 및 보안 강화 버전"""
    items = Cart.query.filter_by(user_id=current_user.id).all()
    if not items or "송도동" not in (current_user.address or ""): 
        return redirect('/order/confirm')
    
    subtotal = sum(i.price * i.quantity for i in items)
    cat_price_sums = {}
    for i in items: 
        cat_price_sums[i.product_category] = cat_price_sums.get(i.product_category, 0) + (i.price * i.quantity)
    delivery_fee = sum([( (amt // 50001) + 1) * 1900 for amt in cat_price_sums.values()])
    
    total, tax_free = int(subtotal + delivery_fee), int(sum(i.price * i.quantity for i in items if i.tax_type == '면세'))
    order_id = f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}_{current_user.id}"
    order_name = f"{items[0].product_name} 외 {len(items)-1}건" if len(items) > 1 else items[0].product_name
    
    content = f"""
    <div class="max-w-md mx-auto py-24 md:py-40 px-6 text-center font-black">
        <div class="w-24 h-24 bg-blue-50 rounded-full flex items-center justify-center text-5xl mx-auto mb-10 text-blue-600 shadow-inner animate-pulse">
            <i class="fas fa-shield-alt"></i>
        </div>
        
        <h2 class="text-2xl md:text-3xl font-black mb-4 text-gray-800 tracking-tighter">
            안전 결제 시스템 연결
        </h2>
        <p class="text-gray-400 font-bold text-sm md:text-base mb-12 leading-relaxed">
            최저가 쇼핑몰은 토스페이먼츠의 보안망을 통해<br>고객님의 결제 정보를 안전하게 보호합니다.
        </p>

        <div class="bg-white p-8 rounded-3xl border border-gray-100 shadow-xl mb-12 text-left space-y-4">
            <div class="flex justify-between text-xs font-bold text-gray-400 uppercase tracking-widest">
                <span>주문 상품</span>
                <span class="text-gray-800">{ order_name }</span>
            </div>
            <div class="flex justify-between items-center border-t border-gray-50 pt-4 font-black">
                <span class="text-sm text-gray-600">총 결제 금액</span>
                <span class="text-2xl text-green-600 italic underline underline-offset-4">{ "{:,}".format(total) }원</span>
            </div>
        </div>

        <button id="payment-button" class="w-full bg-blue-600 text-white py-6 rounded-[1.5rem] md:rounded-[2rem] font-black text-xl shadow-xl shadow-blue-100 hover:bg-blue-700 transition active:scale-95 flex items-center justify-center gap-3">
            <i class="fas fa-credit-card"></i> 결제창 열기
        </button>
        
        <p class="mt-8 text-[10px] text-gray-300 font-medium">
            결제창이 열리지 않거나 오류가 발생할 경우<br>고객센터(1666-8320)로 문의해 주세요.
        </p>
    </div>

    <script>
    // 1. 토스페이먼츠 초기화
    var tossPayments = TossPayments("{TOSS_CLIENT_KEY}");
    var isProcessing = false; // 중복 결제 방지 상태 변수

    document.getElementById('payment-button').addEventListener('click', function() {{
        // 2. 중복 클릭 체크
        if (isProcessing) {{
            alert("현재 결제가 진행 중입니다. 잠시만 기다려 주세요.");
            return;
        }}

        try {{
            isProcessing = true; // 처리 시작
            this.innerHTML = '<i class="fas fa-spinner animate-spin"></i> 연결 중...';
            this.classList.add('opacity-50', 'cursor-not-allowed');

            tossPayments.requestPayment('카드', {{
                amount: { total },
                taxFreeAmount: { tax_free },
                orderId: '{ order_id }',
                orderName: '{ order_name }',
                customerName: '{ current_user.name }',
                successUrl: window.location.origin + '/payment/success',
                failUrl: window.location.origin + '/payment/fail'
            }}).catch(function (error) {{
                // 결제창 호출 실패 시 상태 복구
                isProcessing = false;
                document.getElementById('payment-button').innerHTML = '<i class="fas fa-credit-card"></i> 결제창 열기';
                document.getElementById('payment-button').classList.remove('opacity-50', 'cursor-not-allowed');
                
                if (error.code === 'USER_CANCEL') {{
                    alert("결제가 취소되었습니다.");
                }} else {{
                    alert("결제 오류: " + error.message);
                }}
            }});
        }} catch (err) {{
            alert("시스템 오류가 발생했습니다: " + err.message);
            isProcessing = false;
        }}
    }});
    </script>
    """
    return render_template_string(HEADER_HTML + content + FOOTER_HTML)

# [수정] 결제 성공 화면 내 '바로가기 추가' 버튼 포함
@app.route('/payment/success')
@login_required
def payment_success():
    """결제 성공 및 주문 생성 (세련된 디자인 및 폰트 최적화 버전)"""
    pk, oid, amt = request.args.get('paymentKey'), request.args.get('orderId'), request.args.get('amount')
    url, auth_key = "https://api.tosspayments.com/v1/payments/confirm", base64.b64encode(f"{TOSS_SECRET_KEY}:".encode()).decode()
    res = requests.post(url, json={"paymentKey": pk, "amount": amt, "orderId": oid}, headers={"Authorization": f"Basic {auth_key}", "Content-Type": "application/json"})
    
    if res.status_code == 200:
        items = Cart.query.filter_by(user_id=current_user.id).all()
        if not items: return redirect('/') # 중복 새로고침 방지

        cat_groups = {i.product_category: [] for i in items}
        for i in items: cat_groups[i.product_category].append(f"{i.product_name}({i.quantity})")
        details = " | ".join([f"[{cat}] {', '.join(prods)}" for cat, prods in cat_groups.items()])
        
        cat_price_sums = {}
        for i in items: cat_price_sums[i.product_category] = cat_price_sums.get(i.product_category, 0) + (i.price * i.quantity)
        delivery_fee = sum([( (amt_ // 50001) + 1) * 1900 for amt_ in cat_price_sums.values()])

        # 주문 데이터 저장
        db.session.add(Order(user_id=current_user.id, customer_name=current_user.name, customer_phone=current_user.phone, customer_email=current_user.email, product_details=details, total_price=int(amt), delivery_fee=delivery_fee, tax_free_amount=sum(i.price * i.quantity for i in items if i.tax_type == '면세'), order_id=oid, payment_key=pk, delivery_address=f"({current_user.address}) {current_user.address_detail} (현관:{current_user.entrance_pw})", request_memo=current_user.request_memo, status='결제완료'))
        
        # 재고 차감
        for i in items:
            p = Product.query.get(i.product_id)
            if p: p.stock -= i.quantity
        
        Cart.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
        
        # ✅ 세련된 성공 화면 구성
        success_content = f"""
        <div class="max-w-md mx-auto py-20 md:py-32 px-6 text-center font-black">
            <div class="w-24 h-24 bg-green-500 rounded-full flex items-center justify-center text-white text-4xl mx-auto mb-10 shadow-2xl animate-bounce">
                <i class="fas fa-check"></i>
            </div>
            
            <h2 class="text-3xl md:text-4xl font-black mb-4 text-gray-800 tracking-tighter italic uppercase">
                주문 성공!
            </h2>
            <p class="text-gray-400 font-bold text-sm md:text-base mb-12 leading-relaxed">
                결제가 안전하게 완료되었습니다.<br>신선한 상품을 문 앞까지 빠르게 배송해 드릴게요.
            </p>

            <div class="bg-white p-8 rounded-[2.5rem] border border-gray-100 shadow-xl mb-12 text-left space-y-5">
                <div class="pb-4 border-b border-gray-50">
                    <p class="text-[10px] text-gray-400 uppercase tracking-widest mb-1 font-black">Order ID</p>
                    <p class="text-sm font-black text-gray-700">{ oid }</p>
                </div>
                <div>
                    <p class="text-[10px] text-gray-400 uppercase tracking-widest mb-1 font-black">Payment Amount</p>
                    <p class="text-2xl font-black text-green-600 italic">{ "{:,}".format(int(amt)) }원</p>
                </div>
            </div>

            <div class="flex flex-col gap-4">
                <a href="/mypage" class="bg-gray-800 text-white py-6 rounded-3xl font-black text-lg shadow-xl hover:bg-black transition active:scale-95">
                    주문 내역 확인하기
                </a>
                <a href="/" class="bg-white text-gray-400 py-4 rounded-3xl font-black text-sm hover:text-green-600 transition">
                    메인으로 돌아가기
                </a>
            </div>
            
            <p class="mt-12 text-[10px] text-gray-300 font-medium">
                문의 사항이 있으시면 1666-8320으로 연락주세요.
            </p>
        </div>
        """
        return render_template_string(HEADER_HTML + success_content + FOOTER_HTML)

    return redirect('/')

# --------------------------------------------------------------------------------
# 6. 관리자 전용 기능 (Dashboard / Bulk Upload / Excel)
# --------------------------------------------------------------------------------
# --- [신규 추가] 카테고리 관리자의 배송 요청 기능 ---
# ✅ 개별 정산 승인을 위한 라우트 신설
@app.route('/admin/settle_order/<int:order_id>', methods=['POST'])
@login_required
def admin_settle_order(order_id):
    """주문별 정산 확정 처리 및 DB 저장"""
    if not current_user.is_admin:
        flash("관리자 권한이 필요합니다.")
        return redirect('/')
    
    order = Order.query.get_or_404(order_id)
    
    if not order.is_settled:
        order.is_settled = True
        order.settled_at = datetime.now() # 정산 시점 기록
        
        try:
            db.session.commit() # ✅ 실제 DB에 강제 기록
            flash(f"주문 {order.order_id[-8:]} 입금 승인 완료!")
        except Exception as e:
            db.session.rollback()
            flash(f"저장 오류: {str(e)}")
    else:
        flash("이미 처리된 주문입니다.")
        
    # ✅ 사용자가 보던 날짜 필터가 유지되도록 이전 페이지(referrer)로 리다이렉트
    return redirect(request.referrer or url_for('admin_dashboard', tab='orders'))

# admin() 함수 내 주문 조회 부분은 기존과 동일하게 유지하되 UI에서 필드를 사용함
@app.route('/admin/order/bulk_request_delivery', methods=['POST'])
@login_required
def admin_bulk_request_delivery():
    """여러 주문을 한꺼번에 배송 요청 상태로 변경 (새로고침 없음)"""
    if not (current_user.is_admin or Category.query.filter_by(manager_email=current_user.email).first()):
        return jsonify({"success": False, "message": "권한이 없습니다."}), 403
    
    data = request.get_json()
    order_ids = data.get('order_ids', [])
    
    if not order_ids:
        return jsonify({"success": False, "message": "선택된 주문이 없습니다."})

    # '결제완료' 상태인 주문들만 찾아서 '배송요청'으로 일괄 변경
    orders = Order.query.filter(Order.order_id.in_(order_ids), Order.status == '결제완료').all()
    
    count = 0
    for o in orders:
        o.status = '배송요청'
        count += 1
    
    db.session.commit()
    return jsonify({"success": True, "message": f"{count}건의 배송 요청이 완료되었습니다."})
@app.route('/admin')
@login_required
def admin_dashboard():
    """관리자 대시보드 - [매출+물류+카테고리+리뷰] 전체 기능 통합 복구본"""
    categories = Category.query.order_by(Category.order.asc(), Category.id.asc()).all()
    managers = [c.manager_email for c in categories if c.manager_email]
    
    if not (current_user.is_admin or current_user.email in managers):
        flash("관리자 권한이 없습니다.")
        return redirect('/')
    
    is_master = current_user.is_admin
    tab = request.args.get('tab', 'products')
    my_categories = [c.name for c in categories if c.manager_email == current_user.email]
    
    # 1. 날짜 변수 정의
    now = datetime.now()
    start_date_str = request.args.get('start_date', now.strftime('%Y-%m-%d 00:00')).replace('T', ' ')
    end_date_str = request.args.get('end_date', now.strftime('%Y-%m-%d 23:59')).replace('T', ' ')
    
    # 2. 공통 변수 초기화
    sel_cat = request.args.get('category', '전체')
    sel_order_cat = request.args.get('order_cat', '전체')
    products, filtered_orders, summary, daily_stats, reviews = [], [], {}, {}, []
    stats = {"sales": 0, "delivery": 0, "count": 0, "grand_total": 0}

    if tab == 'products':
        q = Product.query
        if sel_cat != '전체': q = q.filter_by(category=sel_cat)
        products = [p for p in q.order_by(Product.id.desc()).all() if is_master or p.category in my_categories]
     
    elif tab == 'orders':
        try:
            # 날짜 파싱 시도
            start_dt = datetime.strptime(start_date_str, '%Y-%m-%d %H:%M')
            end_dt = datetime.strptime(end_date_str, '%Y-%m-%d %H:%M')
        except Exception as e:
            # 파싱 실패 시 기본값 (오늘 00:00 ~ 23:59)
            print(f"Date parsing error: {e}")
            start_dt = now.replace(hour=0, minute=0, second=0)
            end_dt = now.replace(hour=23, minute=59, second=59)

        # 결제취소 제외 주문 필터링
        all_orders = Order.query.filter(
            Order.created_at >= start_dt, 
            Order.created_at <= end_dt,
            Order.status != '결제취소'
        ).order_by(Order.created_at.desc()).all()
        
        for o in all_orders:
            order_date = o.created_at.strftime('%Y-%m-%d')
            if order_date not in daily_stats:
                daily_stats[order_date] = {"sales": 0, "count": 0}

            order_show_flag = False
            current_order_sales = 0  # 매니저별 정산 대상 금액 변수
            
            # 주문 상세 텍스트 파싱
            parts = o.product_details.split(' | ')
            for part in parts:
                match = re.search(r'\[(.*?)\] (.*)', part)
                if match:
                    cat_n = match.group(1).strip()
                    items_str = match.group(2).strip()
                    
                    # 권한 확인 (마스터 혹은 해당 카테고리 매니저)
                    if is_master or cat_n in my_categories:
                        order_show_flag = True
                        if cat_n not in summary: 
                            summary[cat_n] = {"product_list": {}, "subtotal": 0}
                        
                        for item in items_str.split(', '):
                            it_match = re.search(r'(.*?)\((\d+)\)', item)
                            if it_match:
                                pn = it_match.group(1).strip()
                                qt = int(it_match.group(2))
                                # 상품 단가 조회하여 정산금 계산
                                p_obj = Product.query.filter_by(name=pn).first()
                                if p_obj:
                                    item_price = p_obj.price * qt
                                    summary[cat_n]["subtotal"] += item_price
                                    summary[cat_n]["product_list"][pn] = summary[cat_n]["product_list"].get(pn, 0) + qt
                                    current_order_sales += item_price

            # 권한이 있는 주문 데이터만 통계에 반영
            if order_show_flag:
                filtered_orders.append(o)
                stats["sales"] += current_order_sales
                stats["count"] += 1
                daily_stats[order_date]["sales"] += current_order_sales
                daily_stats[order_date]["count"] += 1
                if is_master: stats["delivery"] += (o.delivery_fee or 0)

        daily_stats = dict(sorted(daily_stats.items(), reverse=True))
        stats["grand_total"] = stats["sales"] + stats["delivery"]
            
    elif tab == 'reviews':
        # 리뷰 탭은 예외 처리 없이 단순 조회
        reviews = Review.query.order_by(Review.created_at.desc()).all()

    # 3. HTML 템플릿 코드
    # 3. HTML 템플릿 코드 (카테고리 설정 탭 완벽 복구본)
    admin_html = """
    <div class="max-w-7xl mx-auto py-12 px-4 md:px-6 font-black text-xs md:text-sm text-left">
        <div class="flex justify-between items-center mb-10">
            <h2 class="text-2xl md:text-3xl font-black text-orange-700 italic">Admin Panel</h2>
            <div class="flex gap-2">
                 <a href="/" class="px-4 py-2 bg-gray-100 rounded-xl text-[10px] hover:bg-gray-200 transition">홈으로</a>
                 <a href="/logout" class="px-4 py-2 bg-red-50 text-red-500 rounded-xl text-[10px] hover:bg-red-100 transition">로그아웃</a>
            </div>
        </div>
        
        <div class="flex border-b border-gray-100 mb-12 bg-white rounded-t-3xl overflow-x-auto">
            <a href="/admin?tab=products" class="px-8 py-5 {% if tab == 'products' %}border-b-4 border-orange-500 text-orange-600{% endif %}">상품 관리</a>
            {% if is_master %}<a href="/admin?tab=categories" class="px-8 py-5 {% if tab == 'categories' %}border-b-4 border-orange-500 text-orange-600{% endif %}">카테고리 설정</a>{% endif %}
            <a href="/admin?tab=orders" class="px-8 py-5 {% if tab == 'orders' %}border-b-4 border-orange-500 text-orange-600{% endif %}">주문 및 매출 집계</a>
            <a href="/admin?tab=reviews" class="px-8 py-5 {% if tab == 'reviews' %}border-b-4 border-orange-500 text-orange-600{% endif %}">리뷰 관리</a>
        </div>

        {% if tab == 'products' %}
            <div id="excel_upload_form" class="hidden mb-8 bg-blue-50 p-8 rounded-[2rem] border border-blue-100">
                <p class="font-black text-blue-700 mb-4">📦 엑셀 상품 대량 등록</p>
                <form action="/admin/product/bulk_upload" method="POST" enctype="multipart/form-data" class="flex gap-4">
                    <input type="file" name="excel_file" class="bg-white p-3 rounded-xl flex-1 text-xs" required>
                    <button type="submit" class="bg-blue-600 text-white px-8 rounded-xl font-black">업로드 시작</button>
                </form>
            </div>
            <div class="flex justify-between items-center mb-8">
                <form action="/admin" class="flex gap-3">
                    <input type="hidden" name="tab" value="products">
                    <select name="category" onchange="this.form.submit()" class="border-none bg-white shadow-sm p-3 rounded-2xl text-[11px] font-black">
                        <option value="전체">전체 카테고리</option>
                        {% for c in categories %}<option value="{{c.name}}" {% if sel_cat == c.name %}selected{% endif %}>{{c.name}}</option>{% endfor %}
                    </select>
                </form>
                <div class="flex gap-3">
                    <button onclick="document.getElementById('excel_upload_form').classList.toggle('hidden')" class="bg-blue-600 text-white px-5 py-3 rounded-2xl font-black text-[10px] shadow-lg">엑셀 업로드</button>
                    <a href="/admin/add" class="bg-green-600 text-white px-5 py-3 rounded-2xl font-black text-[10px] shadow-lg">+ 상품 등록</a>
                </div>
            </div>
            <div class="bg-white rounded-[2rem] shadow-sm border border-gray-50 overflow-hidden">
                <table class="w-full text-left">
                    <thead class="bg-gray-50 border-b border-gray-100 text-gray-400 text-[10px]">
                        <tr><th class="p-6">상품정보</th><th class="p-6 text-center">재고</th><th class="p-6 text-center">관리</th></tr>
                    </thead>
                    <tbody>
                        {% for p in products %}
                        <tr class="border-b border-gray-50 hover:bg-gray-50/50 transition">
                            <td class="p-6"><b class="text-gray-800 text-sm">{{ p.name }}</b><br><span class="text-green-600 text-[10px]">{{ p.description or '' }}</span></td>
                            <td class="p-6 text-center font-black">{{ p.stock }}개</td>
                            <td class="p-6 text-center space-x-2"><a href="/admin/edit/{{p.id}}" class="text-blue-500">수정</a><a href="/admin/delete/{{p.id}}" class="text-red-300" onclick="return confirm('삭제?')">삭제</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

        {% elif tab == 'categories' %}
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-10 text-left">
                <div class="bg-white p-8 md:p-12 rounded-[2.5rem] md:rounded-[3.5rem] border border-gray-50 shadow-sm h-fit">
                    <h3 class="text-[11px] md:text-sm text-gray-400 uppercase tracking-widest mb-10 font-black">판매 카테고리 및 사업자 추가</h3>
                    <form action="/admin/category/add" method="POST" class="space-y-5">
                        <input name="cat_name" placeholder="카테고리명 (예: 산지직송 농산물)" class="border border-gray-100 p-5 rounded-2xl w-full font-black text-sm" required>
                        <textarea name="description" placeholder="카테고리 설명 (배송 정책 등)" class="border border-gray-100 p-5 rounded-2xl w-full h-24 font-black text-sm"></textarea>
                        <input name="manager_email" placeholder="관리 매니저 이메일 (로그인 ID)" class="border border-gray-100 p-5 rounded-2xl w-full font-black text-sm">
                        <select name="tax_type" class="border border-gray-100 p-5 rounded-2xl w-full font-black text-sm bg-white">
                            <option value="과세">일반 과세 상품</option>
                            <option value="면세">면세 농축산물</option>
                        </select>
                        <div class="border-t border-gray-100 pt-8 space-y-4">
                            <p class="text-[10px] text-green-600 font-bold tracking-widest uppercase">Seller Business Profile</p>
                            <input name="biz_name" placeholder="사업자 상호명" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm">
                            <input name="biz_representative" placeholder="대표자 성함" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm">
                            <input name="biz_reg_number" placeholder="사업자 등록번호 ( - 포함 )" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm">
                            <input name="biz_address" placeholder="사업장 소재지" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm">
                            <input name="biz_contact" placeholder="고객 센터 번호" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm">
                            <input name="seller_link" placeholder="판매자 문의 링크" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm">
                        </div>
                        <button class="w-full bg-green-600 text-white py-5 rounded-3xl font-black text-base md:text-lg shadow-xl hover:bg-green-700 transition">신규 카테고리 생성</button>
                    </form>
                </div>
                
                <div class="bg-white rounded-[2.5rem] md:rounded-[3.5rem] border border-gray-50 shadow-sm overflow-hidden h-fit">
                    <table class="w-full text-left">
                        <thead class="bg-gray-50 border-b border-gray-100 font-bold uppercase text-[10px] md:text-xs">
                            <tr><th class="p-6">순서</th><th class="p-6">카테고리 정보</th><th class="p-6 text-center">관리</th></tr>
                        </thead>
                        <tbody>
                            {% for c in categories %}
                            <tr class="border-b border-gray-50 hover:bg-gray-50/50 transition">
                                <td class="p-6 flex gap-2">
                                    <a href="/admin/category/move/{{c.id}}/up" class="text-blue-500 hover:scale-125 transition"><i class="fas fa-chevron-up"></i></a>
                                    <a href="/admin/category/move/{{c.id}}/down" class="text-red-500 hover:scale-125 transition"><i class="fas fa-chevron-down"></i></a>
                                </td>
                                <td class="p-6">
                                    <b class="text-gray-800">{{ c.name }}</b><br>
                                    <span class="text-gray-400 text-[10px]">매니저: {{ c.manager_email or '미지정' }}</span>
                                </td>
                                <td class="p-6 text-center space-x-3 text-[10px]">
                                    <a href="/admin/category/edit/{{c.id}}" class="text-blue-500 font-bold hover:underline">수정</a>
                                    <a href="/admin/category/delete/{{c.id}}" class="text-red-200 hover:text-red-500 transition" onclick="return confirm('삭제하시겠습니까?')">삭제</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

        {% elif tab == 'orders' %}
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8 text-left">
                <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm"><p class="text-[9px] text-gray-400 font-black uppercase mb-1">Total Sales</p><p class="text-xl font-black text-green-600">{{ "{:,}".format(stats.sales) }}원</p></div>
                <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm"><p class="text-[9px] text-gray-400 font-black uppercase mb-1">Orders</p><p class="text-xl font-black text-gray-800">{{ stats.count }}건</p></div>
                <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm"><p class="text-[9px] text-gray-400 font-black uppercase mb-1">Delivery Fees</p><p class="text-xl font-black text-orange-500">{{ "{:,}".format(stats.delivery) }}원</p></div>
                <div class="bg-gray-800 p-6 rounded-[2rem] shadow-xl"><p class="text-[9px] text-gray-400 font-black uppercase mb-1 text-white/50">Grand Total</p><p class="text-xl font-black text-white">{{ "{:,}".format(stats.grand_total) }}원</p></div>
            </div>

            <div class="bg-white p-8 rounded-[2.5rem] border border-gray-100 shadow-sm mb-12">
                <div class="flex gap-2 mb-6">
                    <button type="button" onclick="setDateRange('today')" class="px-4 py-2 bg-gray-100 rounded-xl text-[10px] font-black hover:bg-green-100 transition">오늘</button>
                    <button type="button" onclick="setDateRange('7days')" class="px-4 py-2 bg-gray-100 rounded-xl text-[10px] font-black hover:bg-green-100 transition">최근 7일</button>
                    <button type="button" onclick="setDateRange('month')" class="px-4 py-2 bg-gray-100 rounded-xl text-[10px] font-black hover:bg-green-100 transition">이번 달</button>
                </div>
                <form action="/admin" method="GET" id="date-filter-form" class="grid grid-cols-1 md:grid-cols-4 gap-6 items-end">
                    <input type="hidden" name="tab" value="orders">
                    <div><label class="text-[10px] text-gray-400 font-black ml-2">시작 일시</label><input type="datetime-local" name="start_date" id="start_date" value="{{ start_date_str.replace(' ', 'T') }}" class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs"></div>
                    <div><label class="text-[10px] text-gray-400 font-black ml-2">종료 일시</label><input type="datetime-local" name="end_date" id="end_date" value="{{ end_date_str.replace(' ', 'T') }}" class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs"></div>
                    <div><label class="text-[10px] text-gray-400 font-black ml-2">카테고리</label><select name="order_cat" class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs bg-white"><option value="전체">모든 품목 합산</option>{% for c in nav_categories %}<option value="{{c.name}}" {% if sel_order_cat == c.name %}selected{% endif %}>{{c.name}}</option>{% endfor %}</select></div>
                    <button type="submit" class="bg-green-600 text-white py-4 rounded-2xl font-black shadow-lg">조회하기</button>
                </form>
            </div>

            <div class="mb-12">
                <h3 class="text-lg font-black text-gray-800 mb-6 italic">💰 카테고리 매니저별 정산 현황</h3>
                <div class="bg-white rounded-[2rem] border border-gray-100 shadow-sm overflow-hidden text-left">
                    <table class="w-full text-left">
                        <thead class="bg-gray-50 border-b border-gray-100 text-[10px] text-gray-400 font-black">
                            <tr><th class="p-5">카테고리명</th><th class="p-5">매니저</th><th class="p-5 text-right">정산 대상 금액</th><th class="p-5 text-center">상태</th><th class="p-5 text-center">액션</th></tr>
                        </thead>
                        <tbody>
                            {% for cat_n, data in summary.items() %}
                            {% set cat_obj = nav_categories|selectattr("name", "equalto", cat_n)|first %}
                            <tr class="border-b border-gray-50">
                                <td class="p-5 font-bold">{{ cat_n }}</td>
                                <td class="p-5 text-gray-500 text-xs">{{ cat_obj.manager_email if cat_obj else '-' }}</td>
                                <td class="p-5 text-right font-black text-blue-600">{{ "{:,}".format(data.subtotal) }}원</td>
                                <td class="p-5 text-center"><span class="bg-orange-100 text-orange-600 px-3 py-1 rounded-full text-[10px] font-black">정산대기</span></td>
                                <td class="p-5 text-center">
                                    {% if is_master %}
                                    <button onclick="approveSettlement('{{ cat_n }}', {{ data.subtotal }}, '{{ cat_obj.manager_email if cat_obj else '' }}')" class="bg-blue-600 text-white px-4 py-2 rounded-xl text-[10px] font-black shadow-md hover:bg-blue-700 transition">입금완료 승인</button>
                                    {% else %}<span class="text-gray-300 text-[10px]">권한없음</span>{% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="flex flex-wrap items-center gap-4 mb-8 bg-gray-50 p-6 rounded-[2.5rem] border border-gray-100">
                <label class="flex items-center gap-2 cursor-pointer bg-white px-6 py-3 rounded-2xl shadow-sm">
                    <input type="checkbox" id="selectAllOrders" class="w-5 h-5 accent-blue-600">
                    <span class="text-xs font-black">전체 선택</span>
                </label>
                <button onclick="requestBulkDelivery()" class="bg-blue-600 text-white px-8 py-3 rounded-2xl font-black text-xs shadow-lg">일괄 배송요청</button>
                <button onclick="printSelectedInvoices()" class="bg-gray-800 text-white px-8 py-3 rounded-2xl font-black text-xs shadow-lg">송장 출력</button>
                <a href="/admin/orders/excel?start_date={{start_date_str}}&end_date={{end_date_str}}" class="bg-green-100 text-green-700 px-8 py-3 rounded-2xl font-black text-xs ml-auto">Excel</a>
            </div>

            <div class="bg-white rounded-[2.5rem] shadow-xl border border-gray-50 overflow-x-auto">
                <table class="w-full text-[10px] font-black min-w-[1200px]">
                    <thead class="bg-gray-800 text-white">
                        <tr><th class="p-6 text-center">선택</th><th class="p-6">일시/상태</th><th class="p-6">고객정보</th><th class="p-6">배송지</th><th class="p-6">주문내역</th><th class="p-6 text-right">관리</th></tr>
                    </thead>
                    <tbody>
                      {% for o in filtered_orders %}
<tr id="row-{{ o.order_id }}" class="border-b border-gray-100 hover:bg-green-50/30 transition">
    <td class="p-6 text-center">
        {% if o.status == '결제완료' and not o.is_settled %}
            <input type="checkbox" class="order-checkbox w-5 h-5 accent-blue-600" value="{{ o.order_id }}">
        {% endif %}
    </td>

    <td class="p-6">
        <span class="text-gray-400 text-[11px]">{{ o.created_at.strftime('%m/%d %H:%M') }}</span><br>
        <span id="status-{{ o.order_id }}" class="{% if o.status == '결제취소' %}text-red-500{% else %}text-green-600{% endif %} font-black">[{{ o.status }}]</span>
    </td>

    <td class="p-6"><b>{{ o.customer_name }}</b><br><span class="text-gray-400">{{ o.customer_phone }}</span></td>

    <td class="p-6 text-gray-500 text-[11px]">{{ o.delivery_address }}</td>
    <td class="p-6 text-gray-600 font-medium text-[11px]">{{ o.product_details }}</td>

    <td class="p-6 text-right">
        {% if o.is_settled %}
            <div class="flex flex-col items-end">
                <span class="bg-gray-100 text-gray-400 px-3 py-1.5 rounded-full text-[10px] font-black shadow-inner">✅ 정산완료</span>
                <span class="text-[8px] text-gray-300 mt-1 font-bold">{{ o.settled_at.strftime('%m/%d %H:%M') if o.settled_at else '' }}</span>
            </div>
        {% else %}
            {% if o.status in ['결제완료', '배송요청', '배송완료'] %}
                <form action="/admin/settle_order/{{ o.id }}" method="POST" onsubmit="return confirm('입금 승인 처리를 하시겠습니까?');" class="inline">
                    <button type="submit" class="bg-blue-600 text-white px-4 py-2 rounded-xl text-[10px] font-black shadow-md hover:bg-blue-700 active:scale-95 transition whitespace-nowrap">
                        입금완료승인
                    </button>
                </form>
            {% endif %}
        {% endif %}
    </td>
</tr>
{% endfor %}
                    </tbody>
                </table>
            </div>

        {% elif tab == 'reviews' %}
            <div class="bg-white rounded-[2.5rem] border border-gray-50 shadow-sm overflow-hidden">
                <table class="w-full text-left">
                    <thead class="bg-gray-50 border-b border-gray-100 text-[10px]">
                        <tr><th class="p-6">상품/작성자</th><th class="p-6">내용</th><th class="p-6 text-center">관리</th></tr>
                    </thead>
                    <tbody>
                        {% for r in reviews %}
                        <tr class="border-b border-gray-100 hover:bg-red-50/30">
                            <td class="p-6"><span class="text-green-600">[{{ r.product_name }}]</span><br><b>{{ r.user_name }}</b></td>
                            <td class="p-6 text-gray-600 leading-relaxed">{{ r.content }}</td>
                            <td class="p-6 text-center"><a href="/admin/review/delete/{{ r.id }}" class="text-red-500 underline" onclick="return confirm('삭제?')">삭제</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        {% endif %}
    </div>

    <script>
    function setDateRange(range) {
        const startInput = document.getElementById('start_date');
        const endInput = document.getElementById('end_date');
        const now = new Date();
        let start = new Date();
        let end = new Date();
        if (range === 'today') { start.setHours(0,0,0,0); end.setHours(23,59,59,999); }
        else if (range === '7days') { start.setDate(now.getDate()-7); start.setHours(0,0,0,0); }
        else if (range === 'month') { start.setDate(1); start.setHours(0,0,0,0); }
        const format = (d) => new Date(d.getTime() - (d.getTimezoneOffset() * 60000)).toISOString().slice(0, 16);
        if(startInput) startInput.value = format(start);
        if(endInput) endInput.value = format(end);
        document.getElementById('date-filter-form').submit();
    }

    document.getElementById('selectAllOrders')?.addEventListener('change', function() {
        document.querySelectorAll('.order-checkbox').forEach(cb => cb.checked = this.checked);
    });

    function printSelectedInvoices() {
        const selected = Array.from(document.querySelectorAll('.order-checkbox:checked')).map(cb => cb.value);
        if (selected.length === 0) return alert("출력할 주문을 선택하세요.");
        window.open(`/admin/order/print?ids=${selected.join(',')}`, '_blank', 'width=800,height=900');
    }

    async function requestBulkDelivery() {
        const selected = Array.from(document.querySelectorAll('.order-checkbox:checked')).map(cb => cb.value);
        if(selected.length === 0) return alert("선택된 주문이 없습니다.");
        if(!confirm(selected.length + "건을 일괄 배송 요청하시겠습니까?")) return;
        sendDeliveryRequest(selected);
    }

    function requestSingleDelivery(id) { if(confirm("배송 요청을 보내시겠습니까?")) sendDeliveryRequest([id]); }

    async function sendDeliveryRequest(ids) {
        try {
            const res = await fetch('/admin/order/bulk_request_delivery', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order_ids: ids })
            });
            const data = await res.json();
            if(data.success) { 
                alert(data.message); 
                ids.forEach(id => {
                    const statusSpan = document.getElementById(`status-${id}`);
                    if(statusSpan) statusSpan.innerText = '[배송요청]';
                    const row = document.getElementById(`row-${id}`);
                    const cb = row.querySelector('.order-checkbox');
                    if(cb) cb.remove();
                    const btn = row.querySelector('button');
                    if(btn) btn.remove();
                });
            }
        } catch (e) { alert("통신 오류"); }
    }

    async function approveSettlement(catName, amt, email) {
        if(!confirm(catName + "의 " + amt.toLocaleString() + "원 정산을 입금 완료처리하시겠습니까?")) return;
        try {
            const res = await fetch('/admin/settlement/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ category_name: catName, amount: amt, manager_email: email })
            });
            const result = await res.json();
            if(result.success) { alert(result.message); location.reload(); }
        } catch(e) { alert("서버 오류"); }
    }
    </script>
    """
    return render_template_string(HEADER_HTML + admin_html + FOOTER_HTML, **locals())
    
"""
<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8 text-left">
    <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm">
        <p class="text-[9px] text-gray-400 font-black uppercase mb-1">Total Sales</p>
        <p class="text-xl font-black text-green-600">{{ "{:,}".format(stats.sales) }}원</p>
    </div>
    <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm">
        <p class="text-[9px] text-gray-400 font-black uppercase mb-1">Orders</p>
        <p class="text-xl font-black text-gray-800">{{ stats.count }}건</p>
    </div>
    <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm">
        <p class="text-[9px] text-gray-400 font-black uppercase mb-1">Delivery Fees</p>
        <p class="text-xl font-black text-orange-500">{{ "{:,}".format(stats.delivery) }}원</p>
    </div>
    <div class="bg-gray-800 p-6 rounded-[2rem] shadow-xl">
        <p class="text-[9px] text-gray-400 font-black uppercase mb-1 text-white/50">Grand Total</p>
        <p class="text-xl font-black text-white">{{ "{:,}".format(stats.grand_total) }}원</p>
    </div>
</div>
    <div class="max-w-7xl mx-auto py-12 px-4 md:px-6 font-black text-xs md:text-sm text-left">
        <div class="flex justify-between items-center mb-10 text-left">
            <h2 class="text-2xl md:text-3xl font-black text-orange-700 italic text-left">Admin Panel</h2>
            <div class="flex gap-4 text-left"><a href="/logout" class="absolute top-6 right-6 z-[9999] text-[12px] md:text-[10px] bg-gray-100 px-6 py-3 md:px-5 md:py-2 rounded-full text-gray-500 font-black hover:bg-red-50 hover:text-red-500 transition-all shadow-md border border-gray-200 text-center">LOGOUT</a></div>
        </div>
        
        <div class="flex border-b border-gray-100 mb-12 bg-white rounded-t-3xl overflow-x-auto text-left">
            <a href="/admin?tab=products" class="px-8 py-5 {% if tab == 'products' %}border-b-4 border-orange-500 text-orange-600{% endif %}">상품 관리</a>
            {% if is_master %}<a href="/admin?tab=categories" class="px-8 py-5 {% if tab == 'categories' %}border-b-4 border-orange-500 text-orange-600{% endif %}">카테고리/판매자 설정</a>{% endif %}
            <a href="/admin?tab=orders" class="px-8 py-5 {% if tab == 'orders' %}border-b-4 border-orange-500 text-orange-600{% endif %}">주문 및 배송 집계</a>
            <a href="/admin?tab=reviews" class="px-8 py-5 {% if tab == 'reviews' %}border-b-4 border-orange-500 text-orange-600{% endif %}">리뷰 관리</a>
        </div>

        {% if tab == 'products' %}
            <div class="bg-white p-8 rounded-[2.5rem] border border-gray-100 shadow-sm mb-12">
    <form action="/admin" method="GET" class="grid grid-cols-1 md:grid-cols-4 gap-6 items-end">
        <input type="hidden" name="tab" value="orders">
        
        <div class="space-y-2">
            <label class="text-[10px] text-gray-400 font-black uppercase tracking-widest ml-2">시작 일시</label>
            <input type="datetime-local" name="start_date" value="{{ start_date_str.replace(' ', 'T') }}" 
                   class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs focus:ring-2 focus:ring-green-500 transition">
        </div>

        <div class="space-y-2">
            <label class="text-[10px] text-gray-400 font-black uppercase tracking-widest ml-2">종료 일시</label>
            <input type="datetime-local" name="end_date" value="{{ end_date_str.replace(' ', 'T') }}" 
                   class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs focus:ring-2 focus:ring-green-500 transition">
        </div>

        <div class="space-y-2">
            <label class="text-[10px] text-gray-400 font-black uppercase tracking-widest ml-2">카테고리</label>
            <select name="order_cat" class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs bg-white focus:ring-2 focus:ring-green-500 transition">
                <option value="전체">모든 품목 합산</option>
                {% for c in nav_categories %}
                <option value="{{c.name}}" {% if sel_order_cat == c.name %}selected{% endif %}>{{c.name}}</option>
                {% endfor %}
            </select>
        </div>

        <button type="submit" class="bg-green-600 text-white py-4 rounded-2xl font-black shadow-lg shadow-green-100 hover:bg-green-700 transition active:scale-95 text-xs">
            <i class="fas fa-search mr-2"></i> 기간 조회하기
        </button>
    </form>
</div>
                <div class="flex gap-3 text-left">
                    <button onclick="document.getElementById('excel_upload_form').classList.toggle('hidden')" class="bg-blue-600 text-white px-6 py-3 rounded-2xl font-black text-xs shadow-lg hover:bg-blue-700 transition">📦 엑셀 대량 등록</button>
                    <a href="/admin/add" class="bg-green-600 text-white px-6 py-3 rounded-2xl font-black text-xs shadow-lg hover:bg-green-700 transition">+ 개별 상품 등록</a>
                </div>
            </div>
            
            <div class="bg-white rounded-[2rem] shadow-sm border border-gray-50 overflow-hidden text-left">
                <table class="w-full text-left">
                    <thead class="bg-gray-50 border-b border-gray-100 text-gray-400 uppercase text-[10px] md:text-xs">
                        <tr><th class="p-6">상품 기본 정보</th><th class="p-6 text-center">재고</th><th class="p-6 text-center">관리</th></tr>
                    </thead>
                    <tbody class="text-left">
                        {% for p in products %}
                        <tr class="border-b border-gray-50 hover:bg-gray-50/50 transition">
                            <td class="p-6 text-left">
                                <b class="text-gray-800 text-sm md:text-base">{{ p.name }}</b> <span class="text-orange-500 text-[9px] md:text-[10px] font-black ml-2">{{ p.badge }}</span><br>
                                <span class="text-green-600 font-bold text-[10px] md:text-xs">{{ p.description or '설명 없음' }}</span><br>
                                <span class="text-gray-400 text-[10px] md:text-xs">{{ "{:,}".format(p.price) }}원 / {{ p.spec or '일반' }}</span>
                            </td>
                            <td class="p-6 text-center font-black text-gray-500">{{ p.stock }}개</td>
                            <td class="p-6 text-center space-x-3 text-[10px] md:text-xs text-center">
                                <a href="/admin/edit/{{p.id}}" class="text-blue-500 hover:underline">수정</a>
                                <a href="/admin/delete/{{p.id}}" class="text-red-300 hover:text-red-500 transition" onclick="return confirm('이 상품을 영구 삭제하시겠습니까?')">삭제</a>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

        {% elif tab == 'categories' %}
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-10 text-left">
                <div class="bg-white p-8 md:p-12 rounded-[2.5rem] md:rounded-[3.5rem] border border-gray-50 shadow-sm h-fit text-left">
                    <h3 class="text-[11px] md:text-sm text-gray-400 uppercase tracking-widest mb-10 font-black text-left">판매 카테고리 및 사업자 추가</h3>
                    <form action="/admin/category/add" method="POST" class="space-y-5 text-left">
                        <input name="cat_name" placeholder="카테고리명 (예: 산지직송 농산물)" class="border border-gray-100 p-5 rounded-2xl w-full font-black text-sm text-left" required>
                        <textarea name="description" placeholder="배송기한 정보 등 설명" class="border border-gray-100 p-5 rounded-2xl w-full h-24 font-black text-sm text-left"></textarea>
                        <input name="manager_email" placeholder="관리 매니저 이메일 (ID)" class="border border-gray-100 p-5 rounded-2xl w-full font-black text-sm text-left">
                        <select name="tax_type" class="border border-gray-100 p-5 rounded-2xl w-full font-black text-sm text-left bg-white"><option value="과세">일반 과세 상품</option><option value="면세">면세 농축산물</option></select>
                        <div class="border-t border-gray-100 pt-8 space-y-4 text-left">
                            <p class="text-[10px] text-green-600 font-bold tracking-widest uppercase text-left">Seller Business Profile</p>
                            <input name="biz_name" placeholder="사업자 상호명" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm text-left">
                            <input name="biz_representative" placeholder="대표자 성함" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm text-left">
                            <input name="biz_reg_number" placeholder="사업자 등록번호 ( - 포함 )" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm text-left">
                            <input name="biz_address" placeholder="사업장 소재지" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm text-left">
                            <input name="biz_contact" placeholder="고객 센터 번호" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm text-left">
                            <input name="seller_link" placeholder="판매자 문의 (카카오/채팅) 링크" class="border border-gray-100 p-4 rounded-xl w-full font-bold text-xs md:text-sm text-left">
                        </div>
                        <button class="w-full bg-green-600 text-white py-5 rounded-3xl font-black text-base md:text-lg shadow-xl hover:bg-green-700 transition text-center">신규 카테고리 생성</button>
                    </form>
                </div>
                
                <div class="bg-white rounded-[2.5rem] md:rounded-[3.5rem] border border-gray-50 shadow-sm overflow-hidden text-left">
                    <table class="w-full text-left">
                        <thead class="bg-gray-50 border-b border-gray-100 font-bold uppercase text-[10px] md:text-xs">
                            <tr><th class="p-6">전시 순서</th><th class="p-6">카테고리명</th><th class="p-6 text-center">관리</th></tr>
                        </thead>
                        <tbody class="text-left">
                            {% for c in categories %}
                            <tr class="border-b border-gray-50 text-left hover:bg-gray-50/50 transition">
                                <td class="p-6 flex gap-4 text-left">
                                    <a href="/admin/category/move/{{c.id}}/up" class="text-blue-500 p-2"><i class="fas fa-chevron-up"></i></a>
                                    <a href="/admin/category/move/{{c.id}}/down" class="text-red-500 p-2"><i class="fas fa-chevron-down"></i></a>
                                </td>
                                <td class="p-6 text-left"><b class="text-gray-800">{{ c.name }}</b><br><span class="text-gray-400 text-[10px]">매니저: {{ c.manager_email or '미지정' }}</span></td>
                                <td class="p-6 text-center space-x-3 text-[10px] text-center">
                                    <a href="/admin/category/edit/{{c.id}}" class="text-blue-500 hover:underline">수정</a>
                                    <a href="/admin/category/delete/{{c.id}}" class="text-red-200 hover:text-red-500 transition" onclick="return confirm('삭제하시겠습니까?')">삭제</a>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

        {% elif tab == 'orders' %}
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
                <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm"><p class="text-[9px] text-gray-400 font-black uppercase mb-1">Total Sales</p><p class="text-xl font-black text-green-600">{{ "{:,}".format(stats.sales) }}원</p></div>
                <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm"><p class="text-[9px] text-gray-400 font-black uppercase mb-1">Orders</p><p class="text-xl font-black text-gray-800">{{ stats.count }}건</p></div>
                <div class="bg-white p-6 rounded-[2rem] border border-gray-100 shadow-sm"><p class="text-[9px] text-gray-400 font-black uppercase mb-1">Delivery Fees</p><p class="text-xl font-black text-orange-500">{{ "{:,}".format(stats.delivery) }}원</p></div>
                <div class="bg-gray-800 p-6 rounded-[2rem] shadow-xl"><p class="text-[9px] text-gray-400 font-black uppercase mb-1 text-white/50">Grand Total</p><p class="text-xl font-black text-white">{{ "{:,}".format(stats.grand_total) }}원</p></div>
            </div>

            <div class="bg-white p-8 rounded-[2.5rem] border border-gray-100 shadow-sm mb-12">
                <div class="flex gap-2 mb-6">
                    <button type="button" onclick="setDateRange('today')" class="px-4 py-2 bg-gray-100 rounded-xl text-[10px] font-black hover:bg-green-100 transition">오늘</button>
                    <button type="button" onclick="setDateRange('7days')" class="px-4 py-2 bg-gray-100 rounded-xl text-[10px] font-black hover:bg-green-100 transition">최근 7일</button>
                    <button type="button" onclick="setDateRange('month')" class="px-4 py-2 bg-gray-100 rounded-xl text-[10px] font-black hover:bg-green-100 transition">이번 달</button>
                </div>
                <form action="/admin" method="GET" id="date-filter-form" class="grid grid-cols-1 md:grid-cols-4 gap-6 items-end">
                    <input type="hidden" name="tab" value="orders">
                    <div><label class="text-[10px] text-gray-400 font-black ml-2">시작 일시</label><input type="datetime-local" name="start_date" id="start_date" value="{{ start_date_str.replace(' ', 'T') }}" class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs"></div>
                    <div><label class="text-[10px] text-gray-400 font-black ml-2">종료 일시</label><input type="datetime-local" name="end_date" id="end_date" value="{{ end_date_str.replace(' ', 'T') }}" class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs"></div>
                    <div><label class="text-[10px] text-gray-400 font-black ml-2">카테고리</label><select name="order_cat" class="w-full border-none bg-gray-50 p-4 rounded-2xl font-black text-xs bg-white"><option value="전체">모든 품목 합산</option>{% for c in nav_categories %}<option value="{{c.name}}" {% if sel_order_cat == c.name %}selected{% endif %}>{{c.name}}</option>{% endfor %}</select></div>
                    <button type="submit" class="bg-green-600 text-white py-4 rounded-2xl font-black shadow-lg">조회하기</button>
                </form>
            </div>

            <div class="mb-12">
                <h3 class="text-lg font-black text-gray-800 mb-6 italic">💰 카테고리 매니저별 정산 현황</h3>
                <div class="bg-white rounded-[2rem] border border-gray-100 shadow-sm overflow-hidden">
                    <table class="w-full text-left">
                        <thead class="bg-gray-50 border-b border-gray-100 text-[10px] text-gray-400 font-black">
                            <tr><th class="p-5">카테고리명</th><th class="p-5">매니저</th><th class="p-5 text-right">정산 대상 금액</th><th class="p-5 text-center">상태</th><th class="p-5 text-center">액션</th></tr>
                        </thead>
                        <tbody>
                            {% for cat_n, data in summary.items() %}
                            {% set cat_obj = nav_categories|selectattr("name", "equalto", cat_n)|first %}
                            <tr class="border-b border-gray-50">
                                <td class="p-5 font-bold">{{ cat_n }}</td>
                                <td class="p-5 text-gray-500 text-xs">{{ cat_obj.manager_email if cat_obj else '-' }}</td>
                                <td class="p-5 text-right font-black text-blue-600">{{ "{:,}".format(data.subtotal) }}원</td>
                                <td class="p-5 text-center"><span class="bg-orange-100 text-orange-600 px-3 py-1 rounded-full text-[10px] font-black">정산대기</span></td>
                                <td class="p-5 text-center">
                                    {% if is_master %}
                                    <button onclick="approveSettlement('{{ cat_n }}', {{ data.subtotal }}, '{{ cat_obj.manager_email if cat_obj else '' }}')" class="bg-blue-600 text-white px-4 py-2 rounded-xl text-[10px] font-black shadow-md">정산 승인</button>
                                    {% else %}<span class="text-gray-300 text-[10px]">권한없음</span>{% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="flex flex-wrap items-center gap-4 mb-8 bg-gray-50 p-6 rounded-[2.5rem] border border-gray-100">
                <label class="flex items-center gap-2 cursor-pointer bg-white px-6 py-3 rounded-2xl shadow-sm"><input type="checkbox" id="selectAllOrders" class="w-5 h-5 accent-blue-600"><span class="text-xs font-black">전체 선택</span></label>
                <button onclick="requestBulkDelivery()" class="bg-blue-600 text-white px-8 py-3 rounded-2xl font-black text-xs shadow-lg">일괄 배송요청</button>
                <button onclick="printSelectedInvoices()" class="bg-gray-800 text-white px-8 py-3 rounded-2xl font-black text-xs shadow-lg">송장 출력</button>
                <a href="/admin/orders/excel?start_date={{start_date_str}}&end_date={{end_date_str}}" class="bg-green-100 text-green-700 px-8 py-3 rounded-2xl font-black text-xs ml-auto">Excel 다운로드</a>
            </div>

            <div class="bg-white rounded-[2.5rem] shadow-xl border border-gray-50 overflow-x-auto">
                <table class="w-full text-[10px] font-black min-w-[1200px]">
                    <thead class="bg-gray-800 text-white">
                        <tr><th class="p-6 text-center">선택</th><th class="p-6">일시/상태</th><th class="p-6">고객정보</th><th class="p-6">배송지</th><th class="p-6">주문내역</th><th class="p-6 text-right">관리</th></tr>
                    </thead>
                    <tbody>
                        {% for o in filtered_orders %}
                        <tr id="row-{{ o.order_id }}" class="border-b border-gray-100 hover:bg-green-50/30 transition">
                            <td class="p-6 text-center">{% if o.status == '결제완료' %}<input type="checkbox" class="order-checkbox w-5 h-5 accent-blue-600" value="{{ o.order_id }}">{% endif %}</td>
                            <td class="p-6">
                                <span class="text-gray-400">{{ o.created_at.strftime('%m/%d %H:%M') }}</span><br>
                                <span id="status-{{ o.order_id }}" class="{% if o.status == '결제취소' %}text-red-500{% else %}text-green-600{% endif %}">[{{ o.status }}]</span>
                            </td>
                            <td class="p-6"><b>{{ o.customer_name }}</b><br>{{ o.customer_phone }}</td>
                            <td class="p-6 text-gray-500">{{ o.delivery_address }}</td>
                            <td class="p-6 text-gray-600 font-medium">{{ o.product_details }}</td>
                            <td class="p-6 text-right">
                                {% if o.status == '결제완료' %}
                                <button onclick="requestSingleDelivery('{{ o.order_id }}')" class="bg-blue-600 text-white px-4 py-2 rounded-xl text-[10px] hover:bg-blue-700 transition">배송요청</button>
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <script>
            function setDateRange(range) {
                const startInput = document.getElementById('start_date');
                const endInput = document.getElementById('end_date');
                const now = new Date();
                let start = new Date();
                let end = new Date();
                if (range === 'today') { start.setHours(0,0,0,0); end.setHours(23,59,59,999); }
                else if (range === '7days') { start.setDate(now.getDate()-7); start.setHours(0,0,0,0); }
                else if (range === 'month') { start.setDate(1); start.setHours(0,0,0,0); }
                const format = (d) => new Date(d.getTime() - (d.getTimezoneOffset() * 60000)).toISOString().slice(0, 16);
                startInput.value = format(start);
                endInput.value = format(end);
                document.getElementById('date-filter-form').submit();
            }

            document.getElementById('selectAllOrders')?.addEventListener('change', function() {
                document.querySelectorAll('.order-checkbox').forEach(cb => cb.checked = this.checked);
            });

            function printSelectedInvoices() {
                const selected = Array.from(document.querySelectorAll('.order-checkbox:checked')).map(cb => cb.value);
                if (selected.length === 0) return alert("출력할 주문을 선택하세요.");
                window.open(`/admin/order/print?ids=${selected.join(',')}`, '_blank', 'width=800,height=900');
            }

            async function requestBulkDelivery() {
                const selected = Array.from(document.querySelectorAll('.order-checkbox:checked')).map(cb => cb.value);
                if(selected.length === 0) return alert("선택된 주문이 없습니다.");
                if(!confirm(selected.length + "건을 일괄 배송 요청하시겠습니까?")) return;
                sendDeliveryRequest(selected);
            }

            function requestSingleDelivery(id) { if(confirm("배송 요청을 보내시겠습니까?")) sendDeliveryRequest([id]); }

            async function sendDeliveryRequest(ids) {
                try {
                    const res = await fetch('/admin/order/bulk_request_delivery', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ order_ids: ids })
                    });
                    const data = await res.json();
                    if(data.success) { 
                        alert(data.message); 
                        ids.forEach(id => {
                            const statusSpan = document.getElementById(`status-${id}`);
                            if(statusSpan) statusSpan.innerText = '[배송요청]';
                            const row = document.getElementById(`row-${id}`);
                            const cb = row.querySelector('.order-checkbox');
                            if(cb) cb.remove();
                            const btn = row.querySelector('button');
                            if(btn) btn.remove();
                        });
                    }
                } catch (e) { alert("통신 오류"); }
            }

            async function approveSettlement(catName, amt, email) {
                if(!confirm(catName + "의 " + amt.toLocaleString() + "원 정산을 승인하시겠습니까?")) return;
                try {
                    const res = await fetch('/admin/settlement/complete', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ category_name: catName, amount: amt, manager_email: email })
                    });
                    const result = await res.json();
                    if(result.success) { alert(result.message); location.reload(); }
                } catch(e) { alert("서버 오류"); }
            }
            </script>
            <div class="mb-12">
                <h3 class="text-lg font-black text-gray-800 mb-6 italic">📅 날짜별 매출 현황</h3>
                <div class="bg-white rounded-[2rem] border border-gray-100 shadow-sm overflow-hidden">
                    <table class="w-full text-left">
                        <thead class="bg-gray-50 border-b border-gray-100 text-[10px] text-gray-400 font-black">
                            <tr>
                                <th class="p-5">날짜</th>
                                <th class="p-5 text-center">주문수</th>
                                <th class="p-5 text-right">매출액</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for date_str, d_data in daily_stats.items() %}
                            <tr class="border-b border-gray-50 hover:bg-orange-50/30 transition">
                                <td class="p-5 font-bold text-gray-600">{{ date_str }}</td>
                                <td class="p-5 text-center font-black text-gray-400">{{ d_data.count }}건</td>
                                <td class="p-5 text-right font-black text-green-600">{{ "{:,}".format(d_data.sales) }}원</td>
                            </tr>
                            {% endfor %}
                            {% if not daily_stats %}
                            <tr><td colspan="3" class="p-10 text-center text-gray-300 font-bold">해당 기간에 주문 데이터가 없습니다.</td></tr>
                            {% endif %}
                        </tbody>
                    </table>
                </div>
            </div>

            {% for cat_n, data in summary.items() %}
            <div class="bg-white rounded-[2rem] border border-gray-50 overflow-hidden mb-10 shadow-sm">
                <div class="bg-gray-50 px-8 py-5 border-b border-gray-100 font-black text-green-700 flex justify-between items-center">
                    <div class="flex items-center gap-3">
                        <input type="checkbox" onclick="toggleCategoryAll(this, '{{ cat_n }}')" class="w-4 h-4 rounded border-slate-300 accent-green-600">
                        <span>{{ cat_n }} 매출 요약</span>
                    </div>
                    <span class="text-xs bg-white px-3 py-1 rounded-full shadow-sm border border-green-100">
                        카테고리 총 매출: {{ "{:,}".format(data.subtotal) }}원
                    </span>
                </div>
                <table class="w-full">
                    {% for pn, qt in data.product_list.items() %}
                    <tr class="border-b border-gray-50">
                        <td class="p-5 font-bold text-gray-700">□ {{ pn }}</td>
                        <td class="p-5 text-right font-black text-blue-600">{{ qt }}개 판매완료</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
            {% endfor %}

            <div class="bg-white rounded-[2.5rem] shadow-xl border border-gray-50 overflow-x-auto">
                <table class="w-full text-[10px] md:text-xs font-black min-w-[1200px]">
                <div class="flex justify-between items-center mb-4 px-4">
<div class="flex items-center gap-4">
    <label class="flex items-center gap-2 cursor-pointer bg-gray-100 px-4 py-2 rounded-xl">
        <input type="checkbox" id="selectAllOrders" class="w-4 h-4 accent-blue-600">
        <span class="text-xs font-black">전체 선택</span>
    </label>
    <button onclick="requestBulkDelivery()" class="bg-blue-600 text-white px-6 py-2.5 rounded-xl font-black text-xs shadow-lg hover:bg-blue-700 transition">
        선택 항목 일괄 배송요청
    </button>
    <button onclick="printSelectedInvoices()" class="bg-gray-800 text-white px-6 py-2.5 rounded-xl font-black text-xs shadow-lg hover:bg-black transition">
        <i class="fas fa-print mr-1"></i> 선택 항목 송장 출력
    </button>
</div>
    </div>
</div>

<div class="bg-white rounded-[2.5rem] shadow-xl border border-gray-50 overflow-x-auto">
    <table class="w-full text-[10px] md:text-xs font-black min-w-[1200px]">
        <thead class="bg-gray-800 text-white">
            <tr>
                <th class="p-6 text-center">선택</th>
                <th class="p-6">Info</th>
                <th class="p-6">Customer</th>
                <th class="p-6">Address</th>
                <th class="p-6">Details</th>
                <th class="p-6 text-right">Action</th>
            </tr>
        </thead>
        <tbody>
    {% for o in filtered_orders %}
    <tr id="row-{{ o.order_id }}" class="border-b border-gray-100 hover:bg-green-50/30 transition">
        <td class="p-6 text-center">
            {% if o.status == '결제완료' and not o.is_settled %}
            <input type="checkbox" class="order-checkbox w-4 h-4 accent-blue-600" value="{{ o.order_id }}">
            {% endif %}
        </td>

        <td class="p-6 text-gray-400">
            {{ o.created_at.strftime('%m/%d %H:%M') }}<br>
            <span id="status-{{ o.order_id }}" class="{% if o.status == '결제취소' %}text-red-500{% else %}text-green-600{% endif %}">[{{ o.status }}]</span>
        </td>

        <td class="p-6"><b>{{ o.customer_name }}</b><br>{{ o.customer_phone }}</td>

        <td class="p-6">{{ o.delivery_address }}</td>

        <td class="p-6 text-gray-600">{{ o.product_details }}</td>

        <td class="p-6 text-right">
            {% if o.is_settled %}
                <div class="flex flex-col items-end">
                    <span class="bg-gray-100 text-gray-400 px-3 py-1.5 rounded-full text-[10px] font-black shadow-inner">✅ 정산완료</span>
                    <span class="text-[8px] text-gray-300 mt-1 font-bold">{{ o.settled_at.strftime('%m/%d %H:%M') if o.settled_at else '' }}</span>
                </div>
            {% else %}
                {% if o.status == '결제완료' %}
                    <div class="flex flex-col gap-2 items-end">
                        <button onclick="requestSingleDelivery('{{ o.order_id }}')" class="bg-blue-600 text-white px-3 py-1.5 rounded-lg text-[10px] hover:bg-blue-700 transition">요청</button>
                        
                        <form action="/admin/settle_order/{{ o.id }}" method="POST" onsubmit="return confirm('입금 승인 처리를 하시겠습니까?');">
                            <button type="submit" class="bg-emerald-600 text-white px-3 py-1.5 rounded-lg text-[10px] font-black shadow-sm hover:bg-emerald-700 transition whitespace-nowrap">
                                입금완료승인
                            </button>
                        </form>
                    </div>
                {% endif %}
            {% endif %}
        </td>
    </tr>
    {% endfor %}
</tbody>
    </table>
</div>

<script>
                                  // ✅ 송장 출력 함수 추가
function printSelectedInvoices() {
    const selected = Array.from(document.querySelectorAll('.order-checkbox:checked')).map(cb => cb.value);
    
    if (selected.length === 0) {
        alert("출력할 주문을 선택해주세요.");
        return;
    }
    
    if (confirm(`${selected.length}건의 송장을 출력하시겠습니까?`)) {
        // 선택된 ID들을 콤마로 연결하여 새 창으로 전송
        const idsParam = selected.join(',');
        const printUrl = `/admin/order/print?ids=${idsParam}`;
        
        // 새 창(팝업)으로 송장 페이지 열기
        const printWindow = window.open(printUrl, '_blank', 'width=800,height=900,scrollbars=yes');
    }
}
// 1. 전체 선택/해제 로직
document.getElementById('selectAllOrders').addEventListener('change', function() {
    const isChecked = this.checked;
    document.querySelectorAll('.order-checkbox').forEach(cb => {
        cb.checked = isChecked;
    });
});

// 2. 단일 건 비동기 처리
async function requestSingleDelivery(orderId) {
    if(!confirm("배송 요청을 보내시겠습니까?")) return;
    sendRequest([orderId]);
}

// 3. 일괄 건 비동기 처리
async function requestBulkDelivery() {
    const selected = Array.from(document.querySelectorAll('.order-checkbox:checked')).map(cb => cb.value);
    if(selected.length === 0) {
        alert("선택된 주문이 없습니다.");
        return;
    }
    if(!confirm(`${selected.length}건을 일괄 배송 요청하시겠습니까?`)) return;
    sendRequest(selected);
}

// 4. 공통 전송 함수 (새로고침 방지 핵심)
async function sendRequest(orderIds) {
    try {
        const response = await fetch('/admin/order/bulk_request_delivery', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ order_ids: orderIds })
        });
        const result = await response.json();
        
        if(result.success) {
            alert(result.message);
            // 페이지 새로고침 대신 상태 텍스트만 변경하고 체크박스 숨김
            orderIds.forEach(id => {
                const statusSpan = document.getElementById(`status-${id}`);
                if(statusSpan) statusSpan.innerText = '[배송요청]';
                
                const row = document.getElementById(`row-${id}`);
                const cb = row.querySelector('.order-checkbox');
                if(cb) cb.remove(); // 처리된 건은 체크박스 제거
                const btn = row.querySelector('button');
                if(btn) btn.remove(); // 버튼 제거
            });
        } else {
            alert(result.message);
        }
    } catch (e) {
        alert("오류가 발생했습니다.");
    }
}
</script>
                </table>
            </div>
            <div class="flex justify-end mt-12"><a href="/admin/orders/excel" class="bg-gray-800 text-white px-10 py-5 rounded-2xl font-black text-xs md:text-sm shadow-2xl transition text-center">Excel Download</a></div>

        {% elif tab == 'reviews' %}
            <div class="bg-white rounded-[2.5rem] shadow-xl border border-gray-50 overflow-hidden">
                <table class="w-full text-[10px] md:text-xs font-black text-left">
                    <thead class="bg-gray-800 text-white">
                        <tr><th class="p-6">상품/작성자</th><th class="p-6">내용</th><th class="p-6 text-center">관리</th></tr>
                    </thead>
                    <tbody>
                        {% for r in reviews %}
                        <tr class="border-b border-gray-100 hover:bg-red-50/30">
                            <td class="p-6"><span class="text-green-600">[{{ r.product_name }}]</span><br>{{ r.user_name }}</td>
                            <td class="p-6">{{ r.content }}</td>
                            <td class="p-6 text-center"><a href="/admin/review/delete/{{ r.id }}" class="bg-red-500 text-white px-4 py-2 rounded-full" onclick="return confirm('삭제하시겠습니까?')">삭제</a></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        {% endif %}
    </div>""" 

# --------------------------------------------------------------------------------
# 7. 엑셀 대량 업로드 (사용자 커스텀 양식 대응)
# --------------------------------------------------------------------------------
# 관리자 주문 탭에서 개별 건에 대해 배송요청 상태로 변경하는 라우트
@app.route('/admin/product/bulk_upload', methods=['POST'])
@login_required
def admin_product_bulk_upload():
    """사용자 엑셀 양식(한글 헤더) 기반 대량 업로드 로직"""
    if not current_user.is_admin: return redirect('/')
    file = request.files.get('excel_file')
    if not file: return redirect('/admin')
    try:
        df = pd.read_excel(file)
        # 사용자 요청 헤더: 카테고리, 상품명, 규격, 가격, 이미지파일명
        required_cols = ['카테고리', '상품명', '규격', '가격', '이미지파일명']
        if not all(col in df.columns for col in required_cols): 
            flash("엑셀 헤더 불일치 (필요: 카테고리, 상품명, 규격, 가격, 이미지파일명)"); return redirect('/admin')
        
        count = 0
        for _, row in df.iterrows():
            cat_name = str(row['카테고리']).strip()
            cat_exists = Category.query.filter_by(name=cat_name).first()
            if not cat_exists: continue
            
            # 이미지 경로 매핑 및 상세사진 자동 설정
            raw_img_name = str(row['이미지파일명']).strip()
            img_url = f"/static/uploads/{raw_img_name}" if raw_img_name != 'nan' else ""
            
            new_p = Product(
                category=cat_name, 
                name=str(row['상품명']), 
                price=int(row['가격']), 
                spec=str(row['규격']), 
                origin="국산", 
                farmer="최저가 쇼핑몰", 
                stock=50, # 기본 재고 50개 설정
                image_url=img_url, 
                detail_image_url=img_url, # 메인과 상세 동일하게 복사
                is_active=True, 
                tax_type=cat_exists.tax_type
            )
            db.session.add(new_p); count += 1
            
        db.session.commit()
        flash(f"{count}개의 상품이 성공적으로 등록되었습니다."); return redirect('/admin')
    except Exception as e: 
        db.session.rollback()
        flash(f"업로드 실패: {str(e)}"); return redirect('/admin')
        db.session.commit()
        flash(f"{count}개의 상품이 성공적으로 등록되었습니다."); return redirect('/admin')
    except Exception as e: 
        db.session.rollback()
        flash(f"업로드 실패: {str(e)}"); return redirect('/admin')

@app.route('/admin/review/delete/<int:rid>')
@login_required
def admin_review_delete(rid):
    if not (current_user.is_admin or Category.query.filter_by(manager_email=current_user.email).first()):
        return redirect('/')
    r = Review.query.get_or_404(rid)
    db.session.delete(r)
    db.session.commit()
    flash("리뷰가 삭제되었습니다.")
    return redirect('/admin?tab=reviews')

# --------------------------------------------------------------------------------
# 8. 개별 상품 등록/수정/삭제 및 카테고리 관리
# --------------------------------------------------------------------------------

@app.route('/admin/add', methods=['GET', 'POST'])
@login_required
def admin_product_add():
    """개별 상품 등록"""
    if request.method == 'POST':
        cat_name = request.form['category']
        if not check_admin_permission(cat_name): return redirect('/admin')
        main_img = save_uploaded_file(request.files.get('main_image'))
        detail_files = request.files.getlist('detail_images')
        detail_img_url_str = ",".join(filter(None, [save_uploaded_file(f) for f in detail_files if f.filename != '']))
        new_p = Product(name=request.form['name'], description=request.form['description'], category=cat_name, price=int(request.form['price']), spec=request.form['spec'], origin=request.form['origin'], farmer="최저가 쇼핑몰", stock=int(request.form['stock']), image_url=main_img or "", detail_image_url=detail_img_url_str, deadline=datetime.strptime(request.form['deadline'], '%Y-%m-%dT%H:%M') if request.form.get('deadline') else None, badge=request.form['badge'])
        db.session.add(new_p); db.session.commit(); return redirect('/admin')
    return render_template_string(HEADER_HTML + """<div class="max-w-xl mx-auto py-20 px-6 font-black text-left"><h2 class="text-3xl font-black mb-12 border-l-8 border-green-600 pl-6 uppercase italic text-left">Add Product</h2><form method="POST" enctype="multipart/form-data" class="bg-white p-10 rounded-[3rem] shadow-2xl space-y-7 text-left"><select name="category" class="w-full p-5 bg-gray-50 rounded-2xl font-black outline-none focus:ring-4 focus:ring-green-50 text-left">{% for c in nav_categories %}<option value="{{c.name}}">{{c.name}}</option>{% endfor %}</select>
   <input name="name" placeholder="상품 명칭 (예: 꿀부사 사과)" class="w-full p-5 bg-gray-50 rounded-2xl font-black text-left text-sm" value="{{ p.name if p else '' }}" required>

<div class="space-y-1">
    <label class="text-[10px] text-orange-500 font-black ml-4 uppercase tracking-widest">Short Intro (상품명 옆 한줄소개)</label>
    <input name="badge" placeholder="예: 아삭하고 달콤한, 산지직송" class="w-full p-5 bg-orange-50 border border-orange-100 rounded-2xl font-black text-left text-sm focus:ring-4 focus:ring-orange-100 outline-none transition" value="{{ p.badge if p else '' }}">
</div>

<div class="space-y-1">
    <label class="text-[10px] text-green-600 font-black ml-4 uppercase tracking-widest">Detailed Intro (사진 위 노출 문구)</label>
    <input name="origin" placeholder="상세페이지 사진 바로 위에 노출될 문구" class="w-full p-5 bg-green-50 border border-green-100 rounded-2xl font-black text-left text-sm focus:ring-4 focus:ring-green-100 outline-none transition" value="{{ p.origin if p else '' }}">
</div>

<div class="space-y-1">
    <label class="text-[10px] text-blue-600 font-black ml-4 uppercase tracking-widest">Delivery (배송 예정일)</label>
    <select name="description" class="w-full p-5 bg-blue-50 text-blue-700 rounded-2xl font-black text-sm outline-none border-none focus:ring-4 focus:ring-blue-100">
        <option value="+1일" {% if p and p.description == '+1일' %}selected{% endif %}>🚚 주문 완료 후 +1일 배송</option>
        <option value="+2일" {% if p and p.description == '+2일' %}selected{% endif %}>🚚 주문 완료 후 +2일 배송</option>
        <option value="+3일" {% if p and p.description == '+3일' %}selected{% endif %}>🚚 주문 완료 후 +3일 배송</option>
        <option value="당일배송" {% if p and p.description == '당일배송' %}selected{% endif %}>⚡ 송도 지역 당일 배송</option>
    </select>
</div>
                                  <div class="grid grid-cols-2 gap-5 text-left"><input name="price" type="number" placeholder="판매 가격(원)" class="p-5 bg-gray-50 rounded-2xl font-black text-left text-sm" required><input name="spec" placeholder="규격 (예: 5kg/1박스)" class="p-5 bg-gray-50 rounded-2xl font-black text-left text-sm"></div><div class="grid grid-cols-2 gap-5 text-left"><input name="stock" type="number" placeholder="재고 수량" class="p-5 bg-gray-50 rounded-2xl font-black text-left text-sm" value="50"><input name="deadline" type="datetime-local" class="p-5 bg-gray-50 rounded-2xl font-black text-left text-sm"></div>
                                  <div class="space-y-1">
   
</div><select name="badge" class="w-full p-5 bg-gray-50 rounded-2xl font-black text-left text-sm"><option value="">노출 뱃지 없음</option><option value="오늘마감">🔥 오늘마감</option><option value="삼촌추천">⭐ 삼촌추천</option></select><div class="p-6 border-2 border-dashed border-gray-100 rounded-3xl text-left"><label class="text-[10px] text-gray-400 uppercase font-black block mb-4 text-left">Main Image (목록 노출)</label><input type="file" name="main_image" class="text-xs text-left"></div><div class="p-6 border-2 border-dashed border-blue-50 rounded-3xl text-left"><label class="text-[10px] text-blue-400 uppercase font-black block mb-4 text-left">Detail Images (상세 내 노출)</label><input type="file" name="detail_images" multiple class="text-xs text-left"></div><button class="w-full bg-green-600 text-white py-6 rounded-3xl font-black text-xl shadow-xl hover:bg-green-700 transition active:scale-95 text-center">상품 등록 완료</button></form></div>""")

@app.route('/admin/edit/<int:pid>', methods=['GET', 'POST'])
@login_required
def admin_product_edit(pid):
    """개별 상품 수정 (상품 등록폼과 동일한 디자인 및 구성 적용)"""
    p = Product.query.get_or_404(pid)
    if request.method == 'POST':
        # 데이터 업데이트 로직
        p.name = request.form['name']
        p.description = request.form['description'] # 배송 예정일 저장
        p.price = int(request.form['price'])
        p.spec = request.form['spec']
        p.stock = int(request.form['stock'])
        p.origin = request.form['origin'] # 사진 위 노출 문구 저장
        p.badge = request.form['badge'] # 뱃지 저장
        p.deadline = datetime.strptime(request.form['deadline'], '%Y-%m-%dT%H:%M') if request.form.get('deadline') else None
        
        # 메인 이미지 변경 시 처리
        main_img = save_uploaded_file(request.files.get('main_image'))
        if main_img: p.image_url = main_img
        
        # 상세 이미지 변경 시 처리
        detail_files = request.files.getlist('detail_images')
        if detail_files and detail_files[0].filename != '':
            p.detail_image_url = ",".join(filter(None, [save_uploaded_file(f) for f in detail_files if f.filename != '']))
            
        db.session.commit()
        flash("상품 정보가 성공적으로 수정되었습니다.")
        return redirect('/admin')

    # 수정 폼 렌더링 (등록 폼과 디자인 통일)
    return render_template_string(HEADER_HTML + """
    <div class="max-w-xl mx-auto py-12 md:py-20 px-6 font-black text-left">
        <h2 class="text-2xl md:text-3xl font-black mb-10 border-l-8 border-blue-600 pl-5 uppercase italic text-gray-800">
            Edit Product
        </h2>
        
        <form method="POST" enctype="multipart/form-data" class="bg-white p-8 md:p-12 rounded-[2.5rem] md:rounded-[3.5rem] shadow-2xl space-y-7 text-left">
            <div class="space-y-1">
                <label class="text-[10px] text-gray-400 font-black ml-4 uppercase tracking-widest">Product Name</label>
                <input name="name" placeholder="상품 명칭 (예: 꿀부사 사과)" 
                       class="w-full p-5 bg-gray-50 rounded-2xl font-black text-left text-sm focus:ring-4 focus:ring-blue-50 outline-none transition" 
                       value="{{ p.name }}" required>
            </div>

            <div class="space-y-1">
                <label class="text-[10px] text-orange-500 font-black ml-4 uppercase tracking-widest">Short Intro (상품명 옆 한줄소개)</label>
                <input name="badge" placeholder="예: 아삭하고 달콤한, 산지직송" 
                       class="w-full p-5 bg-orange-50 border border-orange-100 rounded-2xl font-black text-left text-sm focus:ring-4 focus:ring-orange-100 outline-none transition" 
                       value="{{ p.badge or '' }}">
            </div>

            <div class="space-y-1">
                <label class="text-[10px] text-green-600 font-black ml-4 uppercase tracking-widest">Detailed Intro (사진 위 노출 문구)</label>
                <input name="origin" placeholder="상세페이지 사진 바로 위에 노출될 문구" 
                       class="w-full p-5 bg-green-50 border border-green-100 rounded-2xl font-black text-left text-sm focus:ring-4 focus:ring-green-100 outline-none transition" 
                       value="{{ p.origin or '' }}">
            </div>

            <div class="space-y-1">
                <label class="text-[10px] text-blue-600 font-black ml-4 uppercase tracking-widest">Delivery (배송 예정일)</label>
                <select name="description" class="w-full p-5 bg-blue-50 text-blue-700 rounded-2xl font-black text-sm outline-none border-none focus:ring-4 focus:ring-blue-100">
                    <option value="+1일" {% if p.description == '+1일' %}selected{% endif %}>🚚 주문 완료 후 +1일 배송</option>
                    <option value="+2일" {% if p.description == '+2일' %}selected{% endif %}>🚚 주문 완료 후 +2일 배송</option>
                    <option value="+3일" {% if p.description == '+3일' %}selected{% endif %}>🚚 주문 완료 후 +3일 배송</option>
                    <option value="당일배송" {% if p.description == '당일배송' %}selected{% endif %}>⚡ 송도 지역 당일 배송</option>
                </select>
            </div>

            <div class="grid grid-cols-2 gap-5">
                <div class="space-y-1">
                    <label class="text-[10px] text-gray-400 font-black ml-4 uppercase tracking-widest">Price (원)</label>
                    <input name="price" type="number" placeholder="판매 가격" 
                           class="w-full p-5 bg-gray-50 rounded-2xl font-black text-left text-sm outline-none" 
                           value="{{ p.price }}" required>
                </div>
                <div class="space-y-1">
                    <label class="text-[10px] text-gray-400 font-black ml-4 uppercase tracking-widest">Spec (규격)</label>
                    <input name="spec" placeholder="예: 5kg/1박스" 
                           class="w-full p-5 bg-gray-50 rounded-2xl font-black text-left text-sm outline-none" 
                           value="{{ p.spec or '' }}">
                </div>
            </div>

            <div class="grid grid-cols-2 gap-5">
                <div class="space-y-1">
                    <label class="text-[10px] text-gray-400 font-black ml-4 uppercase tracking-widest">Stock (재고)</label>
                    <input name="stock" type="number" placeholder="재고 수량" 
                           class="w-full p-5 bg-gray-50 rounded-2xl font-black text-left text-sm outline-none" 
                           value="{{ p.stock }}">
                </div>
                <div class="space-y-1">
                    <label class="text-[10px] text-red-400 font-black ml-4 uppercase tracking-widest">Deadline (마감)</label>
                    <input name="deadline" type="datetime-local" 
                           class="w-full p-5 bg-gray-50 rounded-2xl font-black text-left text-sm outline-none" 
                           value="{{ p.deadline.strftime('%Y-%m-%dT%H:%M') if p.deadline else '' }}">
                </div>
            </div>

            <div class="pt-4 space-y-4">
                <div class="p-6 border-2 border-dashed border-gray-100 rounded-3xl">
                    <label class="text-[10px] text-gray-400 uppercase font-black block mb-3">Main Image (기존 이미지 유지 가능)</label>
                    <input type="file" name="main_image" class="text-[10px] font-bold">
                    {% if p.image_url %}
                    <p class="text-[9px] text-blue-500 mt-2 font-bold italic">현재 등록됨: {{ p.image_url.split('/')[-1] }}</p>
                    {% endif %}
                </div>
                
                <div class="p-6 border-2 border-dashed border-blue-50 rounded-3xl">
                    <label class="text-[10px] text-blue-400 uppercase font-black block mb-3">Detail Images (새로 등록 시 기존파일 대체)</label>
                    <input type="file" name="detail_images" multiple class="text-[10px] font-bold">
                </div>
            </div>

            <button type="submit" class="w-full bg-blue-600 text-white py-6 rounded-3xl font-black text-xl shadow-xl hover:bg-blue-700 transition active:scale-95 text-center">
                상품 정보 수정 완료
            </button>
            
            <div class="text-center mt-4">
                <a href="/admin" class="text-gray-300 text-xs font-bold hover:text-gray-500 transition">수정 취소하고 돌아가기</a>
            </div>
        </form>
    </div>
    """ + FOOTER_HTML, p=p)
@app.route('/admin/delete/<int:pid>')
@login_required
def admin_delete(pid):
    """상품 삭제"""
    p = Product.query.get(pid)
    if p and check_admin_permission(p.category): db.session.delete(p); db.session.commit()
    return redirect('/admin')

@app.route('/admin/category/add', methods=['POST'])
@login_required
def admin_category_add():
    """카테고리 추가"""
    if not current_user.is_admin: return redirect('/')
    last_cat = Category.query.order_by(Category.order.desc()).first()
    next_order = (last_cat.order + 1) if last_cat else 0
    db.session.add(Category(name=request.form['cat_name'], description=request.form.get('description'), tax_type=request.form['tax_type'], manager_email=request.form.get('manager_email'), seller_name=request.form.get('biz_name'), seller_inquiry_link=request.form.get('seller_link'), biz_name=request.form.get('biz_name'), biz_representative=request.form.get('biz_representative'), biz_reg_number=request.form.get('biz_reg_number'), biz_address=request.form.get('biz_address'), biz_contact=request.form.get('biz_contact'), order=next_order))
    db.session.commit(); return redirect('/admin?tab=categories')

@app.route('/admin/category/edit/<int:cid>', methods=['GET', 'POST'])
@login_required
def admin_category_edit(cid):
    """카테고리 수정"""
    if not current_user.is_admin: return redirect('/')
    cat = Category.query.get_or_404(cid)
    if request.method == 'POST':
        cat.name, cat.description, cat.tax_type, cat.manager_email = request.form['cat_name'], request.form['description'], request.form['tax_type'], request.form.get('manager_email')
        cat.biz_name, cat.biz_representative, cat.biz_reg_number, cat.biz_address, cat.biz_contact, cat.seller_inquiry_link = request.form.get('biz_name'), request.form.get('biz_representative'), request.form.get('biz_reg_number'), request.form.get('biz_address'), request.form.get('biz_contact'), request.form.get('seller_link')
        cat.seller_name = cat.biz_name
        db.session.commit(); return redirect('/admin?tab=categories')
    return render_template_string(HEADER_HTML + """<div class="max-w-xl mx-auto py-20 px-6 font-black text-left"><h2 class="text-2xl md:text-3xl font-black mb-12 tracking-tighter uppercase text-green-600 text-left">Edit Category Profile</h2><form method="POST" class="bg-white p-10 rounded-[3rem] shadow-2xl space-y-8 text-left"><div><label class="text-[10px] text-gray-400 uppercase font-black ml-4 text-left">Settings</label><input name="cat_name" value="{{cat.name}}" class="border border-gray-100 p-5 rounded-2xl w-full font-black mt-2 text-sm text-left" required><textarea name="description" class="border border-gray-100 p-5 rounded-2xl w-full h-24 font-black mt-3 text-sm text-left" placeholder="한줄 소개">{{cat.description or ''}}</textarea><input name="manager_email" value="{{cat.manager_email or ''}}" class="border border-gray-100 p-5 rounded-2xl w-full font-black mt-3 text-sm text-left" placeholder="매니저 이메일"><select name="tax_type" class="border border-gray-100 p-5 rounded-2xl w-full font-black mt-3 text-sm text-left bg-white"><option value="과세" {% if cat.tax_type == '과세' %}selected{% endif %}>과세</option><option value="면세" {% if cat.tax_type == '면세' %}selected{% endif %}>면세</option></select></div><div class="border-t border-gray-50 pt-10 space-y-4 text-left"><label class="text-[10px] text-green-600 uppercase font-black ml-4 text-left">Business Info</label><input name="biz_name" value="{{cat.biz_name or ''}}" class="border border-gray-100 p-4 rounded-xl w-full font-black text-xs text-left" placeholder="상호명"><input name="biz_representative" value="{{cat.biz_representative or ''}}" class="border border-gray-100 p-4 rounded-xl w-full font-black text-xs text-left" placeholder="대표자"><input name="biz_reg_number" value="{{cat.biz_reg_number or ''}}" class="border border-gray-100 p-4 rounded-xl w-full font-black text-xs text-left" placeholder="사업자번호"><input name="biz_address" value="{{cat.biz_address or ''}}" class="border border-gray-100 p-4 rounded-xl w-full font-black text-xs text-left" placeholder="주소"><input name="biz_contact" value="{{cat.biz_contact or ''}}" class="border border-gray-100 p-4 rounded-xl w-full font-black text-xs text-left" placeholder="고객센터"><input name="seller_link" value="{{cat.seller_inquiry_link or ''}}" class="border border-gray-100 p-4 rounded-xl w-full font-black text-xs text-left" placeholder="문의 링크 URL"></div><button class="w-full bg-blue-600 text-white py-6 rounded-3xl font-black shadow-xl hover:bg-blue-700 transition text-center text-center">Save Profile Updates</button></form></div>""", cat=cat)

@app.route('/admin/category/move/<int:cid>/<string:direction>')
@login_required
def admin_category_move(cid, direction):
    """카테고리 순서 이동"""
    if not current_user.is_admin: return redirect('/')
    curr = Category.query.get_or_404(cid)
    if direction == 'up': target = Category.query.filter(Category.order < curr.order).order_by(Category.order.desc()).first()
    else: target = Category.query.filter(Category.order > curr.order).order_by(Category.order.asc()).first()
    if target: curr.order, target.order = target.order, curr.order; db.session.commit()
    return redirect('/admin?tab=categories')

@app.route('/admin/category/delete/<int:cid>')
@login_required
def admin_category_delete(cid):
    """카테고리 삭제"""
    if not current_user.is_admin: return redirect('/')
    db.session.delete(Category.query.get(cid)); db.session.commit(); return redirect('/admin?tab=categories')

from urllib.parse import quote

@app.route('/admin/orders/excel')
@login_required
def admin_orders_excel():
    """주문 내역 엑셀 다운로드 (정산여부/일시 포함 + 품목 분리 최종 완성본)"""
    categories = Category.query.all()
    my_categories = [c.name for c in categories if c.manager_email == current_user.email]
    
    if not (current_user.is_admin or my_categories):
        flash("엑셀 다운로드 권한이 없습니다.")
        return redirect('/admin')

    is_master = current_user.is_admin
    now = datetime.now()
    
    # [기존 로직 유지] 날짜 변수 정의
    start_date_str = request.args.get('start_date', now.strftime('%Y-%m-%d 00:00')).replace('T', ' ')
    end_date_str = request.args.get('end_date', now.strftime('%Y-%m-%d 23:59')).replace('T', ' ')
    
    query = Order.query.filter(Order.status != '결제취소')
    
    # [기존 로직 유지] 날짜 필터 적용
    try:
        sd = datetime.strptime(start_date_str, '%Y-%m-%d %H:%M')
        ed = datetime.strptime(end_date_str, '%Y-%m-%d %H:%M')
        query = query.filter(Order.created_at >= sd, Order.created_at <= ed)
    except:
        pass

    orders = query.order_by(Order.created_at.desc()).all()
    
    data = []
    all_product_columns = set()

    for o in orders:
        # ✅ 정산 데이터를 포함한 행 데이터 생성 (안전한 필드 참조 방식 적용)
        row = {
            "일시": o.created_at.strftime('%Y-%m-%d %H:%M') if o.created_at else "-",
            "주문번호": o.order_id[-8:] if o.order_id else "-",
            "고객명": o.customer_name or "-",
            "전화번호": o.customer_phone or "-",
            "주소": o.delivery_address or "-",
            "메모": o.request_memo or "-",
            "결제금액": o.total_price or 0,
            "상태": o.status or "-",
            "정산여부": "정산완료" if getattr(o, 'is_settled', False) else "대기",
            "정산일시": o.settled_at.strftime('%Y-%m-%d %H:%M') if (getattr(o, 'is_settled', False) and o.settled_at) else "-"
        }
        
        parts = o.product_details.split(' | ') if o.product_details else []
        row_show_flag = False
        
        for part in parts:
            match = re.search(r'\[(.*?)\] (.*)', part)
            if match:
                cat_n, items_str = match.groups()
                if is_master or cat_n in my_categories:
                    row_show_flag = True
                    items = items_str.split(', ')
                    for item in items:
                        item_match = re.search(r'(.*?)\((\d+)\)', item)
                        if item_match:
                            p_name = item_match.group(1).strip()
                            p_qty = int(item_match.group(2))
                            col_name = f"[{cat_n}] {p_name}"
                            row[col_name] = p_qty
                            all_product_columns.add(col_name)

        if row_show_flag:
            data.append(row)

    if not data:
        flash("다운로드할 데이터가 없습니다.")
        return redirect('/admin?tab=orders')

    # 데이터프레임 생성 및 열 순서 확정
    df = pd.DataFrame(data)
    
    # 헤더 순서 고정 (정보성 열들을 앞으로 배치)
    base_cols = ["일시", "주문번호", "고객명", "전화번호", "주소", "메모", "결제금액", "상태", "정산여부", "정산일시"]
    
    # 실제 생성된 상품 열들만 추출하여 가나다순 정렬
    existing_base_cols = [c for c in base_cols if c in df.columns]
    product_cols = sorted([c for c in df.columns if c not in base_cols])
    
    df = df[existing_base_cols + product_cols]
    df = df.fillna('') # 수량 없는 칸 빈칸 처리

    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False)
    
    out.seek(0)
    filename = f"최저가 쇼핑몰_주문정산_{datetime.now().strftime('%m%d_%H%M')}.xlsx"
    return send_file(out, download_name=filename, as_attachment=True)
    # 데이터프레임 생성 및 열 순서 정리
    df = pd.DataFrame(data)
    
    # 기본 정보 열 리스트
    base_cols = ["일시", "주문번호", "고객명", "전화번호", "주소", "메모", "결제금액", "상태"]
    # 실제 생성된 상품 열들만 추출하여 가나다순 정렬
    exist_prod_cols = sorted([c for c in all_product_columns if c in df.columns])
    
    # 최종 열 순서 확정 (기본정보 + 상품열)
    df = df[base_cols + exist_prod_cols]
    # 수량이 없는 칸(NaN)은 0 또는 빈칸으로 처리 (수량 집계를 위해 0 추천)
    df = df.fillna('') 

    # 메모리 버퍼에 엑셀 쓰기
    out = BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as w:
        df.to_excel(w, index=False, sheet_name='주문리스트')
        
        # 엑셀 열 너비 자동 최적화
        worksheet = w.sheets['주문리스트']
        for idx, col in enumerate(df.columns):
            column_len = df[col].astype(str).str.len().max()
            column_len = max(column_len, len(col)) + 5
            worksheet.column_dimensions[chr(65 + idx)].width = min(column_len, 60)

    out.seek(0)
    
    # 파일명 한글 깨짐 방지 인코딩
    filename = f"최저가 쇼핑몰_주문데이터_{datetime.now().strftime('%m%d_%H%M')}.xlsx"
    encoded_filename = quote(filename)
    
    response = send_file(
        out, 
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True, 
        download_name=filename
    )
    response.headers["Content-Disposition"] = f"attachment; filename={encoded_filename}; filename*=UTF-8''{encoded_filename}"
    
    return response

# --------------------------------------------------------------------------------
# 9. 데이터베이스 초기화 및 서버 실행
# --------------------------------------------------------------------------------

def init_db():
    with app.app_context():
        db.create_all()
        
        # 1. 관리자 계정 생성
        if not User.query.filter_by(email="admin@uncle.com").first():
            db.session.add(User(
                email="admin@uncle.com", 
                password=generate_password_hash("1234"), 
                name="운영자", 
                is_admin=True
            ))

        # 2. 임의의 카테고리 10개 생성
        test_categories = [
            "신선채소", "제철과일", "정육계란", "수산물", "곡류/견과",
            "간편요리", "유제품", "음료/주류", "생활용품", "베이커리"
        ]
        
        if not Category.query.first():
            for i, cat_name in enumerate(test_categories):
                new_cat = Category(
                    name=cat_name,
                    order=i,
                    description=f"{cat_name} 카테고리의 최저가 상품 모음입니다.",
                    biz_name="최저가쇼핑몰 물류센터",
                    biz_contact="1666-8320",
                    tax_type="면세" if i < 5 else "과세"
                )
                db.session.add(new_cat)
            db.session.commit()

        # 3. 카테고리당 상품 10개씩 (총 100개) 생성
        if not Product.query.first():
            categories = Category.query.all()
            # 배송 옵션 리스트
            delivery_options = ["당일배송", "+1일 배송", "+2일 배송"]
            # 상세 설명 문구 샘플
            desc_samples = [
                "생산지에서 갓 수확하여 신선함이 그대로 살아있습니다.",
                "최저가 보장! 유통 마진을 뺀 합리적인 가격으로 만나보세요.",
                "깐깐한 검수를 거친 최상급 품질의 상품만 엄선했습니다.",
                "보관이 용이한 특수 포장으로 안전하게 배송해 드립니다."
            ]

            for cat in categories:
                for j in range(1, 11):
                    random_img_id = random.randint(1, 500)
                    delivery_info = random.choice(delivery_options)
                    
                    new_p = Product(
                        category=cat.name,
                        name=f"{cat.name} 특선 상품 {j:02d}",
                        price=random.randrange(5000, 80000, 500),
                        spec=f"{random.randint(1, 5)}kg / 1박스",
                        description=delivery_info,  # 배송 옵션으로 활용
                        origin="국내산(산지직송)",    # 원산지
                        farmer="최저가쇼핑몰 엄선농가",
                        stock=random.randint(20, 200),
                        image_url=f"https://picsum.photos/id/{random_img_id}/400/400",
                        detail_image_url=f"https://picsum.photos/id/{random_img_id}/600/800",
                        badge="BEST" if j <= 2 else "NEW",
                        # 상세페이지 내 텍스트 설명 (기존 모델의 'origin' 필드를 활용하거나 별도 필드 필요)
                        # 여기서는 기존 모델 구조를 깨지 않기 위해 badge나 description을 조합해 사용합니다.
                        is_active=True
                    )
                    db.session.add(new_p)
            db.session.commit()
            print("✅ 상세 옵션이 포함된 상품 100개 생성 완료!")
# [수정 위치: app.py 파일 가장 마지막 부분]

import subprocess

# --- 수정 전 기존 코드 ---
# if __name__ == "__main__":
#     init_db()
#     if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
#         subprocess.Popen(["python", delivery_script])
#     app.run(host="0.0.0.0", port=5000, debug=True)

# --- 수정 후 (이 부분으로 교체하세요) ---
if __name__ == "__main__":
    with app.app_context():
        # 쇼핑몰 테이블과 배송 테이블을 각각의 DB 파일에 생성합니다.
        db.create_all() # BINDS 설정에 따라 자동으로 분리 생성됨
        
        # [복구] 배송 시스템 최초 관리자 생성 로직 추가
        from delivery_system import AdminUser
        if not AdminUser.query.filter_by(username='admin').first():
            db.session.add(AdminUser(username="admin", password="1234"))
            db.session.commit()
# [수정] Render 배포 환경을 위한 통합 초기화 및 실행 로직
def start_app():
    with app.app_context():
        try:
            # 1. DB 테이블 생성 (category, product 등 모든 테이블)
            db.create_all()
            print("✅ [Render] 모든 데이터베이스 테이블 생성 완료")

            # 2. SQLite 호환성 패치 (기존 DB에 컬럼이 없을 경우 대비)
            from sqlalchemy import text
            alter_queries = [
                'ALTER TABLE "order" ADD COLUMN is_settled INTEGER DEFAULT 0',
                'ALTER TABLE "order" ADD COLUMN settled_at DATETIME'
            ]
            for query in alter_queries:
                try:
                    db.session.execute(text(query))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            # 3. 10개 카테고리 & 100개 상품 자동 생성 함수 실행
            # 이전에 작성한 init_db() 함수가 정의되어 있어야 합니다.
            init_db() 
            print("✅ [Render] 테스트 데이터(100개) 생성 프로세스 완료")

        except Exception as e:
            print(f"❌ DB 초기화 중 오류 발생: {e}")

if __name__ == "__main__":
    # 서버 시작 전 초기화 함수 강제 실행
    start_app()
    
    # Render 환경 변수 PORT 확인 (기본값 5000)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False) # 배포 시에는 debug=False 권장