
import os

from pytubefix import YouTube


class classproperty(property):
    '''
    A class property decorator.
    '''
    def __get__(self, cls, owner):
        return self.fget(owner)


def get_yt_audio(url: str, 
                 output_path: str = 'tmp',  
                 filename: str = 'tmp.mp4') -> str:
    '''
    Download the audio from a YouTube video and return the filename

    Args:
        url (str): The URL of the YouTube video
        output_path (str, optional): The path to save the audio to
        filename    (str, optional): The filename of the downloaded audio

    Returns:
        str: The filename of the downloaded audio
    '''
    
    if not os.path.exists(output_path):
        os.mkdir(output_path)
    
    return (
        YouTube(url)
        .streams.filter(only_audio=True)[0]
        .download(output_path=output_path, filename=filename)
    )


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

