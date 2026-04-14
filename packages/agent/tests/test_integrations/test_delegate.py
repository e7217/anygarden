"""Tests for the /delegate command parsing."""

from doorae_agent.integrations.delegate import parse_delegate


class TestParseDelegate:
    def test_basic(self):
        r = parse_delegate("/delegate 디자인검토 API 스키마 리뷰해줘")
        assert r is not None
        assert r.sub_room_name == "디자인검토"
        assert r.task == "API 스키마 리뷰해줘"

    def test_with_mention_prefix(self):
        r = parse_delegate("@테스트에이전트 /delegate 서브룸 작업내용 여기")
        assert r is not None
        assert r.sub_room_name == "서브룸"
        assert r.task == "작업내용 여기"

    def test_multiline_task(self):
        r = parse_delegate("/delegate 코드리뷰 이 코드를 봐줘\n```\nprint('hello')\n```")
        assert r is not None
        assert r.sub_room_name == "코드리뷰"
        assert "print('hello')" in r.task

    def test_no_match_regular_message(self):
        assert parse_delegate("안녕하세요") is None

    def test_no_match_missing_room_name(self):
        assert parse_delegate("/delegate") is None

    def test_no_match_missing_task(self):
        assert parse_delegate("/delegate 서브룸") is None

    def test_no_match_similar_but_wrong(self):
        assert parse_delegate("/delegating 서브룸 작업") is None

    def test_whitespace_handling(self):
        r = parse_delegate("  @agent  /delegate  myroom  do something  ")
        assert r is not None
        assert r.sub_room_name == "myroom"
        assert r.task == "do something"
