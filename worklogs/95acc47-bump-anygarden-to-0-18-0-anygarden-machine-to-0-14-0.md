# chore(release): bump anygarden to 0.18.0, anygarden-machine to 0.14.0

- Commit: `95acc47` (95acc47ce822e8ff4a484db218e88a450e1934b6)
- Author: Changyong Um
- Date: 2026-07-24T13:21:39+09:00
- PR: — (release)

## Situation

#553(엔진 CLI 수명주기 추상화 — 최신버전 확인 + 서버 주도 업데이트)이 machine·cluster 양쪽을 변경하며 병합됐고(#554, 85c9ded), 배포를 위해 두 패키지 버전을 올린다.

## Task

- `anygarden`(cluster) 0.17.0 → 0.18.0
- `anygarden-machine` 0.13.0 → 0.14.0
- `pyproject.toml`만 수정 (관례 — #552와 동일하게 uv.lock은 release 커밋에 미포함)

## Action

`packages/cluster/pyproject.toml`, `packages/machine/pyproject.toml`의 `[project] version` bump.

## Decisions

N/A — mechanical change. minor bump인 이유: #553이 두 패키지 모두에 새 기능(엔진 최신확인·업데이트)을 추가했기 때문. `anygarden-agent`는 이번 변경에서 무변경이라 bump 대상에서 제외.

## Result

이 커밋 병합 후 태그 `anygarden-v0.18.0` / `anygarden-machine-v0.14.0`를 push하면 `release.yml`이 GitHub Release 생성 + PyPI Trusted Publishing 배포를 수행한다.
