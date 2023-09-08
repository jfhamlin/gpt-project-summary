'''This script is used to recursively summarize a directory of text
files using the OpenAI API. The script will emit to stdout a summary
of each text file in the directory. The script will also emit to
stderr the name of each file as it is being processed.

After each file in a directory is processed, the script will emit to
stdout a summary of the entire directory.

This continues up to the root directory.

The script will exit with a non-zero exit code if any of the files
fail to summarize.

The script accepts one positional argument, which is the path to the
root directory to summarize.

The script also accepts the following optional arguments:
- pattern: a glob pattern to match files to summarize

'''

import argparse
import openai
import os
import fnmatch
import sys
import json

parser = argparse.ArgumentParser(description='Summarize a directory of text files.')

parser.add_argument('path', metavar='PATH', type=str,
                    help='the path to the root directory to summarize')
parser.add_argument('--pattern', metavar='PATTERN', action='append',
                    help='one or more glob patterns to match files to summarize')
parser.add_argument('--exclude', metavar='EXCLUDE', action='append', default=[".*", "*~"],
                    help='one or more glob patterns to exclude files from summarization')

args = parser.parse_args()

openai.api_key = os.environ['OPENAI_API_KEY']

ROOT = os.path.abspath(args.path)

GPT_MODEL = "gpt-3.5-turbo-0613"

SUMMARIES = {}

CURRENT_PATH = None

# We provide functions for listing files and directories that can be
# provided to openai.ChatCompletion to allow it to list files and
# directories in a directory under the root. Listing "/" will list the
# root directory.
#
# We also provide a function for reading a file that can be provided
# to openai.ChatCompletion to allow it to read a file under the root.
#
# Functions:
# - list_dir(path)
# - read_file(path)
# - get_summary(path)

def is_text_file(path):
    '''Return True if path is a text file, False otherwise.

    A file is assumed to be text if reading it as text does not throw
    a UnicodeDecodeError
    '''
    try:
        with open(path, 'r', encoding='utf-8') as f:
            f.read()
        return True
    except UnicodeDecodeError:
        return False

def list_dir(path):
    '''List _text_ files and directories in a directory under the root.
    Return a JSON array of objects with the following keys:
    - name: the name of the file or directory
    - type: either "file" or "directory"

    The path is relative to the root.
    '''

    original_path = path

    path = ROOT+'/'+path

    # throw an exception if path is not a directory or not a descendant
    # of ROOT.
    if not os.path.abspath(path).startswith(ROOT):
        raise Exception(f"{path} is not a descendant of {ROOT}")
    if not os.path.isdir(path):
        raise Exception(f"{path} is not a directory")

    includes = args.pattern
    excludes = args.exclude

    # We use os.listdir() to list the files and directories in the
    # directory. We filter the results to only include text files and
    # directories.
    result = []
    for name in os.listdir(path):
        full_path = os.path.join(path, name)

        if includes:
            included = False
            for pattern in includes:
                if not fnmatch.fnmatch(name, pattern):
                    included = True
                    break
        else:
            included = True
        if not included:
            continue

        excluded = False
        if excludes:
            for pattern in excludes:
                if fnmatch.fnmatch(name, pattern):
                    excluded = True
                    break
        if excluded:
            continue

        name = os.path.join(original_path, name)
        if os.path.isdir(full_path):
            result.append({'name': name, 'type': 'directory'})
        elif is_text_file(full_path):
            result.append({'name': name, 'type': 'file'})
    return result

def read_file(path):
    '''Read a file under the root. Return the contents of the file as
    a string.'''

    path = ROOT+'/'+path

    if not os.path.abspath(path).startswith(ROOT):
        raise Exception(f"{path} is not a descendant of {ROOT}")
    if not os.path.isfile(path):
        raise Exception(f"{path} is not a file")

    with open(os.path.join(ROOT, path), 'r', encoding='utf-8') as f:
        return f.read()

def get_summary(path):
    '''Return the summary of a path.'''
    print(f"Getting summary of {path} while summarizing {CURRENT_PATH}", file=sys.stderr)
    if path == CURRENT_PATH:
        raise Exception(f"Cannot summarize {path} while it is being summarized. Please do not call get_summary with this path again.")

    original_path = path

    path = ROOT+'/'+path
    path = os.path.abspath(path)

    if original_path in SUMMARIES:
        print(f"Returning cached summary of {path}", file=sys.stderr)
        return SUMMARIES[original_path]['summary']

    type_string = 'file'
    if os.path.isdir(path):
        type_string = 'directory'

    summary = summarize(original_path)
    SUMMARIES[original_path] = {
        'type': type_string,
        'summary': summary,
    }
    return summary

def get_prompt(path):
    '''Return the prompt for a path.'''
    original_path = path

    path = ROOT+'/'+path
    path = os.path.abspath(path)

    is_dir = os.path.isdir(path)
    type_string = 'directory' if is_dir else 'file'

    # SUMMARIES is a dictionary mapping paths to {type, summary}
    prior_summaries = []
    for p in SUMMARIES:
        prior_summaries.append(f'- {p} ({SUMMARIES[p]["type"]})')
    prior_summaries = '\n'.join(prior_summaries)
    if prior_summaries == '':
        prior_summaries = 'None'

    if is_dir:
        contents = json.dumps(list_dir(original_path), indent=2)
    else:
        with open(path) as f:
            contents = f.read()

    return '''You are helping me recursively summarize a directory
of files in order to inform newcomer to a software project about the
contents of the project.

In prior sessions, you have summarized the following paths:
{2}

In this session, I would like you to summarize the following {0}: {1}
Its contents are shown below, after a line of dashes.

Please summarize the contents of the provided {0}.

Only respond with the summary or a function call. Do not include any
instructions to me in your summary of the {0}.

Summaries should be in an informative and confident tone, as though
summarized by the author. Summaries should be informative to a human
reader. They should be grammatically correct and should not contain
any spelling errors. They should be accurate in their description of
the contents and purpose of the {0}. If describing a file, they should
describe the contents of the file, its purpose, and anything notable
to someone unfamiliar with the project. If describing a directory,
they should describe the contents of the directory and the purpose of
the directory, including a meta-summary of the files and directories
in the directory.

- If accessing summaries of other files or directories would help you
summarize the {0}, you may do so by calling the function
"get_summary". VERY IMPORTANT: You cannot ask for the summary of the
{0} {1} itself. DO NOT CALL get_summary("{1}").

- If reading the contents of another file or directory would help you
summarize the {0}, you may do so by calling the function "read_file".

- If listing the files and directories in a directory would help you
summarize the {0}, you may do so by calling the function "list_dir".

Do not ask for the contents of any file you have not learned about by
calling "list_dir." Do not ask for the summary of any file or
directory you have not learned about by calling "list_dir."

Be judicious in the files and directories you choose to read.

The final summary of the {0} should be your last message; it should
begin with "DONE", followed by a newline, followed by the summary.

Until you are done, you may send me any number of messages, including
function calls or other messages that do not begin with "DONE". I will
respond with the function call results or "CONTINUE".
--------------------------------------------------------------------------------
{3}'''.format(type_string, original_path, prior_summaries, contents)

FUNCTIONS = [
    {
        'name': 'list_dir',
        'description': list_dir.__doc__,
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'The path to the directory under the root to list. To list the root directory, use "/"',
                },
            },
        },
    },
    {
        'name': 'read_file',
        'description': read_file.__doc__,
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'The path to the file under the root to read.',
                },
            },
        },
    },
    {
        'name': 'get_summary',
        'description': get_summary.__doc__,
        'parameters': {
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': 'The path to the file under the root to read.',
                },
            },
        },
    },
]

FUNCTION_MAP = {
    'list_dir': list_dir,
    'read_file': read_file,
    'get_summary': get_summary,
}

def process_response(messages, response):
    message = response['choices'][0]['message']
    messages.append(message)
    if message.get('function_call'):
        try:
            fn_name = message['function_call']['name']
            fn = FUNCTION_MAP[fn_name]
            args = json.loads(message['function_call']['arguments'])
            print(f"Calling {fn_name} with {args}", file=sys.stderr)
            fn_resp = fn(**args)
        except Exception as e:
            print(f"Exception calling {fn_name}: {e}", file=sys.stderr)
            messages.append({
                'role': 'function',
                'name': fn_name,
                'content': f'ERROR: {e}',
            })
            return openai.ChatCompletion.create(
                model=GPT_MODEL,
                messages=messages,
            )

        messages.append({
            'role': 'function',
            'name': fn_name,
            'content': json.dumps(fn_resp),
        })
        return openai.ChatCompletion.create(
            model=GPT_MODEL,
            messages=messages,
        )
    elif message.get('content').startswith('DONE'):
        return message.get('content')[5:]
    else:
        print('CONTINUING AFTER',message.get('content'), file=sys.stderr)
        messages.append({'role': 'user', 'content': 'CONTINUE\nRespond with DONE, a newline, followed by your final summary when you are done.'})
        return openai.ChatCompletion.create(
            model=GPT_MODEL,
            messages=messages,
            functions=FUNCTIONS,
            function_call='auto',
        )

def summarize(path):
    # We use openai.ChatCompletion to summarize the directory. We provide
    # the functions defined above to allow it to list files and
    # directories and read files.

    print(f"Summarizing {path}", file=sys.stderr)
    global CURRENT_PATH
    CURRENT_PATH = path

    messages = [{'role': 'user', 'content': get_prompt(path)}]
    # print(f"Prompt: {messages[-1]['content']}", file=sys.stderr)
    # exit()

    response = openai.ChatCompletion.create(
        model=GPT_MODEL,
        messages=messages,
        functions=FUNCTIONS,
        function_call='auto',
    )

    while True:
        response = process_response(messages, response)
        if isinstance(response, str):
            print("========================================", file=sys.stderr)
            print(f"Summary of {path} is:\n{response}", file=sys.stderr)
            print("========================================", file=sys.stderr)
            return response

def summarize_dir(path):
    '''Summarize the entire directory, starting with the leaves. Use
    list_dir to find files and directories that are permitted to be
    summarized.
    '''

    root = list_dir(path)
    for entry in root:
        child = entry['name']
        is_dir = entry['type'] == 'directory'
        if is_dir:
            summarize_dir(child)
        else:
            get_summary(child)
    get_summary(path)

def print_summary():
    for path, summary in SUMMARIES.items():
        print(f"## {path}")
        print(summary['summary'])

if __name__ == '__main__':
    summarize_dir(".")
    print_summary()
