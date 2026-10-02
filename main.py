import os
import re
import sqlite3
import hashlib
import hmac
import base64
from pathlib import Path
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

from fastapi import (
    FastAPI,
    Request,
    Form,
    UploadFile,
    File
)
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    JSONResponse
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


# =========================================================
# 1. 기본 경로 설정
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

CSV_FILE = STATIC_DIR / "예금목록.csv"
DB_FILE = BASE_DIR / "users.db"

PROFILE_DIR = STATIC_DIR / "profiles"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# 2. 환경변수
# =========================================================

load_dotenv(BASE_DIR / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "hana-finance-secret-key-1439"
)


# =========================================================
# 3. OpenAI
# =========================================================

try:
    from openai import OpenAI

    if OPENAI_API_KEY:
        openai_client = OpenAI(
            api_key=OPENAI_API_KEY
        )
    else:
        openai_client = None

except Exception:
    openai_client = None


# =========================================================
# 4. FastAPI 설정
# =========================================================

app = FastAPI(
    title="하나 금융상품 추천 서비스"
)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static"
)

templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR)
)


# =========================================================
# 5. SQLite 데이터베이스
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            age INTEGER,
            gender TEXT,
            investment_type TEXT,
            job TEXT,
            income TEXT,
            profile_photo TEXT,
            created_at TEXT
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


# =========================================================
# 6. 비밀번호 암호화
# =========================================================

def hash_password(password: str) -> str:

    return hashlib.sha256(
        password.encode("utf-8")
    ).hexdigest()


# =========================================================
# 7. 로그인 쿠키
# =========================================================

def create_login_token(user_id: str) -> str:

    user_id_encoded = base64.urlsafe_b64encode(
        user_id.encode("utf-8")
    ).decode("utf-8")

    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        user_id_encoded.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return f"{user_id_encoded}.{signature}"


def verify_login_token(token: str):

    try:

        user_id_encoded, signature = token.split(
            ".",
            1
        )

        expected_signature = hmac.new(
            SECRET_KEY.encode("utf-8"),
            user_id_encoded.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            signature,
            expected_signature
        ):
            return None

        user_id = base64.urlsafe_b64decode(
            user_id_encoded.encode("utf-8")
        ).decode("utf-8")

        return user_id

    except Exception:
        return None


# =========================================================
# 8. 현재 로그인 사용자
# =========================================================

def get_user(request: Request):

    token = request.cookies.get(
        "login_token"
    )

    if not token:
        return None

    user_id = verify_login_token(token)

    if not user_id:
        return None

    conn = get_db()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    return user


# =========================================================
# 9. CSV 상품 데이터 불러오기
# =========================================================

def load_products():

    if not CSV_FILE.exists():
        return pd.DataFrame()

    try:

        df = pd.read_csv(
            CSV_FILE,
            encoding="utf-8-sig"
        )

    except Exception:

        try:

            df = pd.read_csv(
                CSV_FILE,
                encoding="cp949"
            )

        except Exception:
            return pd.DataFrame()

    df = df.fillna("")

    return df


# =========================================================
# 10. 숫자 변환
# =========================================================

def to_number(value):

    if value is None:
        return 0.0

    text = str(value)

    text = text.replace(
        ",",
        ""
    )

    text = text.replace(
        "%",
        ""
    )

    text = text.strip()

    numbers = re.findall(
        r"-?\d+(?:\.\d+)?",
        text
    )

    if not numbers:
        return 0.0

    try:
        return float(numbers[0])

    except Exception:
        return 0.0


# =========================================================
# 11. CSV 컬럼 값 가져오기
# =========================================================

def get_column_value(row, *column_names):

    for column in column_names:

        if column in row.index:

            value = row[column]

            if str(value).strip():
                return str(value)

    return ""


# =========================================================
# 12. 상품 추천
# =========================================================

def recommend_products(
    age,
    investment_type,
    job,
    income,
    limit=3
):

    df = load_products()

    if df.empty:
        return []

    try:
        age = int(age)
    except Exception:
        age = 0

    investment_type = str(
        investment_type or ""
    ).lower()

    job = str(
        job or ""
    ).lower()

    income = str(
        income or ""
    ).lower()

    results = []

    for _, row in df.iterrows():

        product_name = get_column_value(
            row,
            "상품명",
            "상품명칭",
            "상품"
        )

        company = get_column_value(
            row,
            "금융사",
            "은행명",
            "금융회사"
        )

        target = get_column_value(
            row,
            "가입대상",
            "가입자격",
            "대상"
        )

        detail = get_column_value(
            row,
            "상세정보전체",
            "상세정보",
            "상품설명"
        )

        period = get_column_value(
            row,
            "가입기간",
            "기간"
        )

        amount = get_column_value(
            row,
            "가입금액",
            "금액"
        )

        method = get_column_value(
            row,
            "가입방법",
            "가입경로"
        )

        url = get_column_value(
            row,
            "상세URL",
            "URL",
            "url"
        )

        max_rate = to_number(
            get_column_value(
                row,
                "최고금리_숫자",
                "최고금리"
            )
        )

        base_rate = to_number(
            get_column_value(
                row,
                "기본금리_숫자",
                "기본금리"
            )
        )

        text = (
            f"{product_name} "
            f"{company} "
            f"{target} "
            f"{detail}"
        ).lower()

        score = 0

        # ---------------------------------------------
        # 나이
        # ---------------------------------------------

        if age:

            if age >= 60:

                if any(
                    word in text
                    for word in [
                        "시니어",
                        "실버",
                        "고령",
                        "어르신",
                        "연금"
                    ]
                ):
                    score += 5

            elif age >= 50:

                if any(
                    word in text
                    for word in [
                        "시니어",
                        "실버",
                        "연금",
                        "노후"
                    ]
                ):
                    score += 3

            elif age < 30:

                if any(
                    word in text
                    for word in [
                        "청년",
                        "사회초년생",
                        "첫",
                        "주거"
                    ]
                ):
                    score += 4

        # ---------------------------------------------
        # 투자성향
        # ---------------------------------------------

        if investment_type:

            if "안정" in investment_type:

                if any(
                    word in text
                    for word in [
                        "예금",
                        "적금",
                        "원금",
                        "안전"
                    ]
                ):
                    score += 5

            elif "중립" in investment_type:

                if any(
                    word in text
                    for word in [
                        "예금",
                        "적금",
                        "투자",
                        "재테크"
                    ]
                ):
                    score += 3

            elif "공격" in investment_type:

                if any(
                    word in text
                    for word in [
                        "투자",
                        "증권",
                        "주식",
                        "고수익"
                    ]
                ):
                    score += 3

        # ---------------------------------------------
        # 직업
        # ---------------------------------------------

        if job:

            if any(
                word in job
                for word in [
                    "학생",
                    "대학생"
                ]
            ):

                if any(
                    word in text
                    for word in [
                        "청년",
                        "학생",
                        "대학생"
                    ]
                ):
                    score += 4

            elif "직장" in job:

                if any(
                    word in text
                    for word in [
                        "직장인",
                        "급여",
                        "월급",
                        "급여이체"
                    ]
                ):
                    score += 4

            elif "자영업" in job:

                if any(
                    word in text
                    for word in [
                        "사업",
                        "소상공인",
                        "자영업"
                    ]
                ):
                    score += 4

        # ---------------------------------------------
        # 소득
        # ---------------------------------------------

        if income:

            if any(
                word in income
                for word in [
                    "낮음",
                    "2000",
                    "3000"
                ]
            ):

                if any(
                    word in text
                    for word in [
                        "소액",
                        "우대",
                        "청년"
                    ]
                ):
                    score += 2

            elif any(
                word in income
                for word in [
                    "5000",
                    "6000",
                    "7000",
                    "8000",
                    "1억"
                ]
            ):

                if any(
                    word in text
                    for word in [
                        "고액",
                        "우대",
                        "프리미엄"
                    ]
                ):
                    score += 2

        # ---------------------------------------------
        # 금리
        # ---------------------------------------------

        score += max_rate * 1.5
        score += base_rate * 0.5

        results.append(
            {
                "상품명": product_name,
                "금융사": company,
                "가입대상": target,
                "가입기간": period,
                "가입금액": amount,
                "최고금리": (
                    f"{max_rate:.2f}%"
                    if max_rate
                    else ""
                ),
                "기본금리": (
                    f"{base_rate:.2f}%"
                    if base_rate
                    else ""
                ),
                "가입방법": method,
                "상세URL": url,
                "상세정보": detail,
                "score": score
            }
        )

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return results[:limit]


# =========================================================
# 13. 홈
# =========================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def home(request: Request):

    user = get_user(request)

    if user:
        return RedirectResponse(
            url="/main",
            status_code=303
        )

    return RedirectResponse(
        url="/login",
        status_code=303
    )


# =========================================================
# 14. 로그인 화면
# =========================================================

@app.get(
    "/login",
    response_class=HTMLResponse
)
async def login_page(
    request: Request
):

    return templates.TemplateResponse(
        request,
        "login.html",
        {}
    )


# =========================================================
# 15. 로그인 처리
# =========================================================

@app.post(
    "/login",
    response_class=HTMLResponse
)
async def login_process(
    request: Request,
    user_id: str = Form(...),
    password: str = Form(...)
):

    password_hash = hash_password(
        password
    )

    conn = get_db()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        AND password = ?
        """,
        (
            user_id,
            password_hash
        )
    ).fetchone()

    conn.close()

    if not user:

        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "아이디 또는 비밀번호가 올바르지 않습니다."
            }
        )

    token = create_login_token(
        user_id
    )

    response = RedirectResponse(
        url="/main",
        status_code=303
    )

    response.set_cookie(
        key="login_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7
    )

    return response


# =========================================================
# 16. 회원가입 화면
# =========================================================

@app.get(
    "/signup",
    response_class=HTMLResponse
)
async def signup_page(
    request: Request
):

    return templates.TemplateResponse(
        request,
        "signup.html",
        {}
    )


# =========================================================
# 17. 회원가입 처리
# =========================================================

@app.post(
    "/signup",
    response_class=HTMLResponse
)
async def signup_process(
    request: Request,

    user_id: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),

    age: int = Form(...),

    gender: str = Form(""),
    investment_type: str = Form(""),
    job: str = Form(""),
    income: str = Form(""),

    profile_photo: UploadFile = File(None)
):

    conn = get_db()

    existing_user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    if existing_user:

        conn.close()

        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "이미 사용 중인 아이디입니다."
            }
        )

    # ---------------------------------------------
    # 프로필 사진
    # ---------------------------------------------

    saved_photo = ""

    if profile_photo:

        if profile_photo.filename:

            extension = Path(
                profile_photo.filename
            ).suffix.lower()

            allowed_extensions = [
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp"
            ]

            if extension in allowed_extensions:

                safe_user_id = re.sub(
                    r"[^a-zA-Z0-9가-힣_-]",
                    "_",
                    user_id
                )

                filename = (
                    f"{safe_user_id}"
                    f"{extension}"
                )

                save_path = (
                    PROFILE_DIR / filename
                )

                content = await profile_photo.read()

                with open(
                    save_path,
                    "wb"
                ) as f:
                    f.write(content)

                saved_photo = (
                    f"/static/profiles/{filename}"
                )

    # ---------------------------------------------
    # 회원정보 저장
    # ---------------------------------------------

    conn.execute(
        """
        INSERT INTO users (
            user_id,
            password,
            name,
            email,
            age,
            gender,
            investment_type,
            job,
            income,
            profile_photo,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            hash_password(password),
            name,
            email,
            age,
            gender,
            investment_type,
            job,
            income,
            saved_photo,
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )
    )

    conn.commit()
    conn.close()

    return RedirectResponse(
        url="/login?signup=success",
        status_code=303
    )


# =========================================================
# 18. 로그아웃
# =========================================================

@app.get("/logout")
async def logout():

    response = RedirectResponse(
        url="/login",
        status_code=303
    )

    response.delete_cookie(
        "login_token"
    )

    return response


# =========================================================
# 19. 메인 페이지
# =========================================================

@app.get(
    "/main",
    response_class=HTMLResponse
)
async def main_page(
    request: Request
):

    user = get_user(request)

    if not user:

        return RedirectResponse(
            url="/login",
            status_code=303
        )

    products = recommend_products(
        age=user["age"],
        investment_type=user["investment_type"],
        job=user["job"],
        income=user["income"],
        limit=3
    )

    return templates.TemplateResponse(
        request,
        "main.html",
        {
            "user": user,
            "products": products
        }
    )


# =========================================================
# 20. 챗봇 페이지
# =========================================================

@app.get(
    "/chatbot",
    response_class=HTMLResponse
)
async def chatbot_page(
    request: Request
):

    user = get_user(request)

    if not user:

        return RedirectResponse(
            url="/login",
            status_code=303
        )

    return templates.TemplateResponse(
        request,
        "chatbot.html",
        {
            "user": user
        }
    )


# =========================================================
# 21. 챗봇 API
# =========================================================

@app.post("/api/chat")
async def chat_api(
    request: Request
):

    user = get_user(request)

    if not user:

        return JSONResponse(
            {
                "answer": "로그인이 필요합니다."
            },
            status_code=401
        )

    try:

        data = await request.json()

    except Exception:

        return JSONResponse(
            {
                "answer": "잘못된 요청입니다."
            },
            status_code=400
        )

    message = str(
        data.get(
            "message",
            ""
        )
    ).strip()

    if not message:

        return {
            "answer": "궁금한 금융상품을 입력해주세요."
        }

    # ---------------------------------------------
    # 상품 데이터
    # ---------------------------------------------

    df = load_products()

    product_data = []

    if not df.empty:

        for _, row in df.head(100).iterrows():

            product_data.append(
                {
                    "상품명": get_column_value(
                        row,
                        "상품명",
                        "상품명칭",
                        "상품"
                    ),

                    "금융사": get_column_value(
                        row,
                        "금융사",
                        "은행명",
                        "금융회사"
                    ),

                    "가입대상": get_column_value(
                        row,
                        "가입대상",
                        "가입자격",
                        "대상"
                    ),

                    "가입기간": get_column_value(
                        row,
                        "가입기간",
                        "기간"
                    ),

                    "가입금액": get_column_value(
                        row,
                        "가입금액",
                        "금액"
                    ),

                    "최고금리": get_column_value(
                        row,
                        "최고금리",
                        "최고금리_숫자"
                    ),

                    "기본금리": get_column_value(
                        row,
                        "기본금리",
                        "기본금리_숫자"
                    ),

                    "상세정보": get_column_value(
                        row,
                        "상세정보전체",
                        "상세정보",
                        "상품설명"
                    ),

                    "상세URL": get_column_value(
                        row,
                        "상세URL",
                        "URL",
                        "url"
                    )
                }
            )

    # ---------------------------------------------
    # API KEY가 없는 경우
    # ---------------------------------------------

    if not openai_client:

        return {
            "answer":
                "챗봇을 사용하려면 .env 파일에 "
                "OPENAI_API_KEY를 설정해주세요."
        }

    # ---------------------------------------------
    # 시스템 프롬프트
    # ---------------------------------------------

    system_prompt = f"""
당신은 하나은행 금융상품 상담 챗봇입니다.

사용자의 회원정보:
- 이름: {user["name"]}
- 나이: {user["age"]}
- 성별: {user["gender"]}
- 투자성향: {user["investment_type"]}
- 직업: {user["job"]}
- 소득: {user["income"]}

아래 금융상품 데이터를 참고하여
사용자의 질문에 답변하세요.

금융상품 데이터:
{product_data}

답변 규칙:

1. 사용자의 질문을 먼저 정확히 파악하세요.
2. 금융상품을 추천할 때는 위 데이터에 있는 상품만 사용하세요.
3. 존재하지 않는 상품이나 금리를 만들어내지 마세요.
4. 상품명, 최고금리, 기본금리 등은 데이터에 있는 내용을 기준으로 답변하세요.
5. 사용자의 나이, 투자성향, 직업, 소득을 고려하여 설명하세요.
6. 금융상품을 추천할 때 왜 적합한지도 간단하게 설명하세요.
7. 너무 긴 답변은 하지 마세요.
8. 한국어로 자연스럽게 답변하세요.
9. 확실하지 않은 정보는 추측하지 말고 확인이 필요하다고 안내하세요.
10. 투자상품의 수익을 보장하는 것처럼 표현하지 마세요.
"""

    # ---------------------------------------------
    # OpenAI 호출
    # ---------------------------------------------

    try:

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,

            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },

                {
                    "role": "user",
                    "content": message
                }
            ]
        )

        answer = (
            response.choices[0]
            .message
            .content
            .strip()
        )

        return {
            "answer": answer
        }

    except Exception as e:

        print(
            "OpenAI 오류:",
            e
        )

        return {
            "answer":
                "챗봇 응답을 처리하는 중 오류가 발생했습니다."
        }


# =========================================================
# 22. 서버 실행
# =========================================================

if __name__ == "__main__":

    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=1439
    )