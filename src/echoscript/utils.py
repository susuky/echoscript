
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


def segments2subtitle(segments, fmt='srt') -> str:
    '''
    Convert a list of segments to a subtitle string in SRT or VTT format

    Args:
        segments: A list of segment dicts, each with the following keys:
            - start: The start time of the segment, in seconds
            - end: The end time of the segment, in seconds
            - text: The text of the segment
        fmt: The subtitle format, either 'srt' or 'vtt' (default: 'srt')

    Returns:
        str: The subtitle string in the specified format
    '''
    formats = {
        'srt': {
            'time_template': '{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}',
            'segment_template': '{index}\n{start} --> {end}\n{text}\n',
            'header': '',
        },
        'vtt': {
            'time_template': '{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}',
            'segment_template': '{start} --> {end}\n{text}\n',
            'header': 'WEBVTT\n\n',
        }
    }

    if fmt not in formats:
        raise ValueError(f'Format `{fmt}` is not supported.')
    
    format_config = formats[fmt]
    time_template = format_config['time_template']
    segment_template = format_config['segment_template']
    header = format_config['header']

    formatted_segments = [
        segment_template.format(
            index=i + 1,
            start=format_timestamp(segment['start'], template=time_template),
            end=format_timestamp(segment['end'], template=time_template),
            text=segment['text']
        )
        for i, segment in enumerate(segments)
    ]
    return header + '\n'.join(formatted_segments)


def format_timestamp(
        t: float, 
        template: str = '{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'
    ) -> str:
    '''
    Format a timestamp in seconds into a string of the form HH:MM:SS,mmm

    Args:
        t: The timestamp to format, in seconds
        template: The format string to use. Defaults to 
                '{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'

    Returns:
        A string representation of the timestamp
    '''
    total_seconds = int(t)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = round((t - total_seconds) * 1000)
    
    format_dict = {
        'hours': hours,
        'minutes': minutes,
        'seconds': seconds,
        'milliseconds': milliseconds
    }
    return template.format(**format_dict)

