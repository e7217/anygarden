"""#527 — "설정 API는 시크릿 평문을 직렬화하지 않는다" 불변식 고정.

조사 결과 현재 코드는 이미 안전하다: ``SecretOut``은 평문을 담지 않고
비가역 ``value_preview``만 반환하며, ``_mask_secret``이 단일 마스킹 지점이고,
``mcp_templates``는 decrypted env 값을 응답에 echo하지 않는다. 이 테스트는
그 계약을 **불변식으로 고정**해, 향후 변경이 실수로 평문을 API 응답에
흘리지 못하게 한다(구조적 하드닝).
"""
from __future__ import annotations

from datetime import datetime

from anygarden.api.v1.llm_gateway import SecretOut, _mask_secret


class TestMaskSecret:
    def test_short_value_fully_masked(self) -> None:
        # < 12자는 접두/말미 힌트 없이 전면 마스크 — 짧은 키 복원 방지.
        assert _mask_secret("short") == "***"
        assert _mask_secret("") == "***"

    def test_long_value_keeps_only_prefix_and_last4(self) -> None:
        secret = "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv"
        masked = _mask_secret(secret)
        assert masked == "sk-ant-api03…StUv"
        # 중간부(키의 대부분)는 마스킹 후 남지 않아야 한다.
        assert "AbCdEfGhIjKl" not in masked
        assert secret not in masked

    def test_mask_is_not_reversible_to_full_value(self) -> None:
        secret = "supersecretlongtokenvalue1234"
        assert secret not in _mask_secret(secret)


class TestSecretOutNeverSerializesPlaintext:
    def test_dump_carries_preview_not_plaintext(self) -> None:
        plaintext = "sk-live-DEADBEEFdeadbeef0123456789"
        out = SecretOut(
            env_var_name="ANTHROPIC_API_KEY",
            value_preview=_mask_secret(plaintext),
            last_tested_at=None,
            last_test_status=None,
            created_at=datetime(2026, 7, 13),
            updated_at=datetime(2026, 7, 13),
        )
        dumped = out.model_dump_json()
        # 평문(및 그 식별 가능한 중간부)은 직렬화 결과 어디에도 없어야 한다.
        assert plaintext not in dumped
        assert "DEADBEEFdeadbeef" not in dumped
        # 프리뷰 힌트는 유지되어 UI가 키를 식별할 수 있어야 한다.
        assert out.value_preview in dumped
        # SecretOut에는 원문을 담을 수 있는 필드가 존재하지 않는다.
        assert set(SecretOut.model_fields) == {
            "env_var_name",
            "value_preview",
            "last_tested_at",
            "last_test_status",
            "created_at",
            "updated_at",
        }
