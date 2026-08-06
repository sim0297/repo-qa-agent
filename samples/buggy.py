"""QA 검증용 샘플. 일부러 결함 심음."""
import sqlite3

DB_PASSWORD = "hunter2_super_secret"  # 하드코딩 시크릿


def login(user, pw):
    conn = sqlite3.connect("app.db")
    # SQL injection: 사용자 입력 문자열 연결
    q = "SELECT * FROM users WHERE name='" + user + "' AND pw='" + pw + "'"
    return conn.execute(q).fetchone()


def divide(a, b):
    # 0 나눗셈 미검증
    return a / b


def load(path):
    try:
        with open(path) as f:
            return f.read()
    except:  # bare except: 모든 예외 삼킴
        return None
