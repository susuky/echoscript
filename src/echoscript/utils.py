

def segments2srt(segments) -> str:
    '''
    Convert a list of segments to a SRT string

    Args:
        segments: A list of segments, each with the following keys:
            - start: The start time of the segment, in seconds
            - end: The end time of the segment, in seconds
            - text: The text of the segment

    Returns:
        str: The SRT string
    '''
    formatted_segments = [
        f'{i + 1}\n'
        f'{format_timestamp(segment["start"])} --> {format_timestamp(segment["end"])}\n'
        f'{segment["text"]}\n'
        for i, segment in enumerate(segments)
    ]
    return '\n'.join(formatted_segments)


def format_timestamp(t):
    '''
    Format a timestamp in seconds into a string of the form HH:MM:SS,mmm

    Args:
        t: The timestamp to format, in seconds

    Returns:
        A string representation of the timestamp
    '''
    total_seconds = int(t)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = round((t - total_seconds) * 1000)
    
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'

