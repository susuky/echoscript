
from echoscript.utils import segments2srt, format_timestamp


def test_single_segment():
    segments = [
        {
            'start': 0.5,
            'end': 2.75,
            'text': 'Hello, world!'
        }
    ]
    expected_srt = (
        '1\n'
        '00:00:00,500 --> 00:00:02,750\n'
        'Hello, world!\n'
    )
    assert segments2srt(segments) == expected_srt


def test_multiple_segments():
    segments = [
        {
            'start': 0.5,
            'end': 2.75,
            'text': 'Hello, world!'
        },
        {
            'start': 3.0,
            'end': 5.0,
            'text': 'Another segment'
        }
    ]
    expected_srt = (
        '1\n'
        '00:00:00,500 --> 00:00:02,750\n'
        'Hello, world!\n\n'
        '2\n'
        '00:00:03,000 --> 00:00:05,000\n'
        'Another segment\n'
    )
    assert segments2srt(segments) == expected_srt


def test_empty_segment_list():
    segments = []
    expected_srt = ''
    assert segments2srt(segments) == expected_srt


def test_format_timestamp():
    assert format_timestamp(0.5) == '00:00:00,500'
    assert format_timestamp(3661.05) == '01:01:01,050'
    assert format_timestamp(10665.25) == '02:57:45,250'

