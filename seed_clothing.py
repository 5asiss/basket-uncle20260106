#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""기존 카테고리/상품 전체 삭제 후 의류 데이터로 재생성
실행: python seed_clothing.py"""
from app import app, db, init_db
from app import Category, Product, Cart

with app.app_context():
    print("기존 카테고리/상품/장바구니 삭제 중...")
    Cart.query.delete()
    Product.query.delete()
    Category.query.delete()
    db.session.commit()
    print("삭제 완료")
    
    print("의류 카테고리 및 상품 생성 중...")
    init_db()
    print("완료! 의류 카테고리 5개, 상품 100개(상세 5장) 생성됨.")
