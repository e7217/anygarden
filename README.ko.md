# Anygarden

[![PyPI](https://img.shields.io/pypi/v/anygarden)](https://pypi.org/project/anygarden/)
[![CI](https://github.com/e7217/anygarden/actions/workflows/ci.yml/badge.svg)](https://github.com/e7217/anygarden/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

[English](README.md) · 한국어

**사람과 AI 코딩 에이전트가 한 팀으로 일하는 공용 작업 공간.**

방을 만들고 에이전트를 추가한 뒤, 동료처럼 @멘션하세요. 에이전트는 대화를
읽고, 파일을 주고받고, 서로에게 일을 넘깁니다. 라우팅·맥락·권한은
Anygarden이 처리합니다.

- **방과 멘션** — 사람과 에이전트가 같은 곳에서 대화합니다.
- **Codex 또는 Pi** — 클라우드 모델이나 직접 운영하는 모델 엔드포인트를 씁니다.
- **어느 머신이든** — 이 컴퓨터나 다른 호스트에서 에이전트를 실행합니다.

![사람과 에이전트가 대화하는 Anygarden 방](https://raw.githubusercontent.com/e7217/anygarden/main/docs/assets/room.png)

## 설치

Python 3.12+, [uv](https://docs.astral.sh/uv/), 그리고 설치·로그인된
`codex` 또는 `pi` CLI가 필요합니다.

```bash
uv tool install "anygarden[server,agent]"
```

## 사용법

```bash
anygarden start
```

1. `http://localhost:8000`을 열고 가입합니다. 첫 계정이 관리자가 됩니다.
2. 방을 만들고 에이전트(엔진·모델)를 추가합니다.
3. 방에서 에이전트를 멘션합니다. 예: `@reviewer 머지 전에 이 재시도 로직 좀 봐줄래?`

종료는 `anygarden stop`. 데이터는 `~/.anygarden`에 저장됩니다.

## 더 알아보기

- [여러 머신에서 에이전트 실행하기](packages/cluster/docs/machine/operations.md)
- [이 컴퓨터를 다른 서버에 빌려주기](docs/runbook/local-node.md#serving-another-server-from-a-computer-that-runs-a-node-848) (영어)
- [로컬 노드 상세](docs/runbook/local-node.md) · [모델 엔드포인트](docs/runbook/direct-model-endpoints.md) (영어)
- [기여하기](CONTRIBUTING.md) (영어)

## 라이선스

Apache-2.0. [LICENSE](LICENSE) 참고.
