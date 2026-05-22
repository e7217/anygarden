# Deployment Guide

## 개발 환경

```bash
cd anygarden-cluster
make install    # 백엔드 + 프론트엔드 의존성 설치
make migrate    # DB 마이그레이션
make dev        # 서버(8001) + 프론트엔드(5173) 동시 실행
```

브라우저에서 `http://localhost:5173` 접속.

## 프로덕션

### 서버 실행

```bash
uvx anygarden-cluster --host 0.0.0.0 --port 8000
```

또는 직접 설치:

```bash
pip install anygarden-cluster
anygarden-server init          # ~/.anygarden/ 초기화, JWT 시크릿 생성
anygarden-server migrate       # DB 마이그레이션
anygarden-server --host 0.0.0.0 --port 8000
```

### 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ANYGARDEN_JWT_SECRET` | (필수) | JWT 서명 키 |
| `ANYGARDEN_DB_URL` | `sqlite+aiosqlite:///~/.anygarden/anygarden.db` | DB 연결 문자열 |
| `ANYGARDEN_HOST` | `127.0.0.1` | 서버 바인딩 주소 |
| `ANYGARDEN_PORT` | `8000` | 서버 포트 |
| `ANYGARDEN_LOG_LEVEL` | `INFO` | 로그 레벨 |
| `ANYGARDEN_DEV` | `false` | 개발 모드 (dev-token 활성화) |

### 프론트엔드 빌드

```bash
cd frontend
npm install
npm run build     # → ../anygarden/static/ 에 출력
```

빌드 후 서버가 정적 파일을 직접 서빙한다.

### 역방향 프록시 (nginx)

```nginx
server {
    listen 443 ssl;
    server_name anygarden.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

WebSocket 업그레이드 헤더 설정이 필수.

### 첫 유저 등록

서버 시작 후 첫 번째로 등록하는 유저가 자동으로 admin 권한을 받는다.
