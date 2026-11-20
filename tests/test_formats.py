from datetime import datetime

from app.parser import parse_chat

IPHONE_US = (
    "\u200e[2/2/26, 8:45:05\u202fPM] Ann: Hello\r\n"
    "[7/28/26, 6:41:36\u202fAM] Bob: Morning\r\n"
)
IPHONE_24H_DMY = (
    "[02/02/2026, 20:45:05] Ann: Hello\n"
    "[28/07/2026, 06:41:36] Bob: Morning\n"
)
ANDROID_12H = (
    "2/2/26, 8:45 PM - Messages and calls are end-to-end encrypted.\n"
    "2/2/26, 8:46 PM - Ann: Hello\n"
    "that continues here\n"
    "7/28/26, 6:41 AM - Bob: <Media omitted>\n"
)
ANDROID_24H_DMY = (
    "02/02/2026, 20:45 - Ann: Hello\n"
    "28/07/2026, 06:41 - Bob: IMG-20260728-WA0001.jpg (file attached)\n"
    "28/07/2026, 06:42 - Ann created group \"Friends\"\n"
)
DOTS_AND_AMPM = "02.02.26, 8:45 p. m. - Ann: Hello\n"
AMBIGUOUS_DMY = (
    "01/02/2026, 10:00 - Ann: a\n"
    "05/02/2026, 10:00 - Bob: b\n"
    "02/03/2026, 10:00 - Ann: c\n"
    "10/03/2026, 10:00 - Bob: d\n"
)


def test_iphone_us():
    m = parse_chat(IPHONE_US)
    assert [x.timestamp for x in m] == [datetime(2026, 2, 2, 20, 45, 5), datetime(2026, 7, 28, 6, 41, 36)]


def test_iphone_24h_day_first():
    m = parse_chat(IPHONE_24H_DMY)
    assert [x.timestamp for x in m] == [datetime(2026, 2, 2, 20, 45, 5), datetime(2026, 7, 28, 6, 41, 36)]


def test_android_12h_with_system_and_multiline():
    m = parse_chat(ANDROID_12H)
    assert len(m) == 3
    assert m[0].kind == "system" and m[0].sender == ""
    assert m[1].text == "Hello\nthat continues here"
    assert m[2].kind == "media" and m[2].timestamp == datetime(2026, 7, 28, 6, 41)


def test_android_24h_day_first_media_and_group_line():
    m = parse_chat(ANDROID_24H_DMY)
    assert m[1].timestamp == datetime(2026, 7, 28, 6, 41)
    assert m[1].kind == "media"
    assert m[2].kind == "system" and m[2].sender == ""


def test_dots_and_spanish_style_pm():
    m = parse_chat(DOTS_AND_AMPM)
    assert m[0].timestamp == datetime(2026, 2, 2, 20, 45)


def test_ambiguous_dates_resolved_as_day_first():
    m = parse_chat(AMBIGUOUS_DMY)
    assert m[1].timestamp == datetime(2026, 2, 5, 10, 0)
    assert m[2].timestamp == datetime(2026, 3, 2, 10, 0)


def test_noon_and_midnight():
    m = parse_chat("[1/1/26, 12:05:00\u202fAM] A: x\n[1/1/26, 12:05:00\u202fPM] A: y\n")
    assert m[0].timestamp.hour == 0 and m[1].timestamp.hour == 12


def test_garbage_gives_no_messages():
    assert parse_chat("hello\nthis is not a chat\n") == []

def test_trailing_blank_lines_are_not_part_of_the_last_message():
    m = parse_chat("[1/1/26, 1:00:00\u202fAM] A: first\n\n[1/1/26, 1:01:00\u202fAM] B: last\n\n")
    assert m[0].text == "first" and m[1].text == "last"