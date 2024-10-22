
import pytest
from echoscript.utils import *


# Testing data
test_segments = [
    {'start': 0, 'end': 2.5, 'text': 'Hello, world!'},
    {'start': 2.5, 'end': 5.0, 'text': 'This is a test.'},
]


def test_segments2subtitle_srt():
    expected_srt = (
        '1\n'
        '00:00:00,000 --> 00:00:02,500\n'
        'Hello, world!\n'
        '\n'
        '2\n'
        '00:00:02,500 --> 00:00:05,000\n'
        'This is a test.\n'
    )
    assert segments2subtitle(test_segments, fmt='srt') == expected_srt


def test_segments2subtitle_vtt():
    expected_vtt = (
        'WEBVTT\n'
        '\n'
        '00:00:00.000 --> 00:00:02.500\n'
        'Hello, world!\n'
        '\n'
        '00:00:02.500 --> 00:00:05.000\n'
        'This is a test.\n'
    )
    assert segments2subtitle(test_segments, fmt='vtt') == expected_vtt


def test_segments2subtitle_default_format():
    assert segments2subtitle(test_segments) == segments2subtitle(test_segments, fmt='srt')


def test_segments2subtitle_invalid_format():
    with pytest.raises(ValueError):
        segments2subtitle(test_segments, fmt='invalid')


def test_segments2subtitle_empty_list():
    assert segments2subtitle([]) == ''


def test_segments2subtitle_long_duration():
    long_segments = [
        {'start': 3600, 'end': 7200, 'text': 'This is a long segment.'},
    ]
    expected_srt = (
        '1\n'
        '01:00:00,000 --> 02:00:00,000\n'
        'This is a long segment.\n'
    )
    assert segments2subtitle(long_segments, fmt='srt') == expected_srt


def test_segments2subtitle_milliseconds():
    millisecond_segments = [
        {'start': 1.234, 'end': 5.678, 'text': 'Testing milliseconds.'},
    ]
    expected_vtt = (
        'WEBVTT\n'
        '\n'
        '00:00:01.234 --> 00:00:05.678\n'
        'Testing milliseconds.\n'
    )
    assert segments2subtitle(millisecond_segments, fmt='vtt') == expected_vtt


def test_format_timestamp():
    assert format_timestamp(0.5) == '00:00:00,500'
    assert format_timestamp(3661.05) == '01:01:01,050'
    assert format_timestamp(10665.25) == '02:57:45,250'

    