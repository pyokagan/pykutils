"""Converts a list of code files into a LaTeX document"""
import argparse
import os
import re
import signal
import sys
import pygments
import pygments.formatters
import pygments.lexers
import pygments.styles


headerfmt = r"""\documentclass[a4paper,english,10pt,final]{{article}}
\usepackage{{fixltx2e}} %LaTeX patches
\usepackage[left=3cm,top=2cm,bottom=2cm,right=3cm]{{geometry}}
\usepackage{{color}}
\usepackage{{fancyvrb}}
\usepackage{{fancyhdr}}
\usepackage[utf8]{{inputenc}}
\usepackage{{relsize}}
\pagestyle{{fancy}}
\usepackage[marginclue,footnote]{{fixme}}
{styledefs}
\RecustomVerbatimEnvironment{{Verbatim}}{{Verbatim}}{{fontsize=\relsize{{-1}}}}
%%Body
\begin{{document}}
\title{{{title}}}
\author{{{author}}}
\date{{}}
\maketitle
"""


blockfmt = r"""\section{{{path}}}
{content}
"""


footerfmt = r"""
\end{{document}}
"""


def filter_tex(x):
    return re.sub(r'([_\\])', lambda m: '\\{}'.format(m.group(1)), x)


def get_lexer_for_filename(x):
    try:
        return pygments.lexers.get_lexer_for_filename(x)
    except:
        # Fallback to plain text formatting
        return pygments.lexers.get_lexer_by_name('text')


def list_styles(out):
    for x in pygments.styles.get_all_styles():
        out.write(x)
        out.write('\n')


def code2tex(out, paths, style='default', title='', author='',
             headerfmt=headerfmt, footerfmt=footerfmt, blockfmt=blockfmt):
    """Formats `paths` into LaTeX"""
    style = pygments.styles.get_style_by_name(style)
    formatter = pygments.formatters.get_formatter_by_name('tex', linenos=True,
                                                          style=style)
    header = headerfmt.format(title=filter_tex(title),
                              author=filter_tex(author),
                              styledefs=formatter.get_style_defs())
    footer = footerfmt.format()
    out.write(header)
    for path in paths:
        lexer = get_lexer_for_filename(path)
        f = open(path, 'r', errors='ignore')
        contents = f.read()
        f.close()
        contents = pygments.highlight(contents, lexer, formatter)
        contents = blockfmt.format(path=filter_tex(path), content=contents)
        out.write(contents)
    out.write(footer)


def main(args=None, prog=None):
    """Main entry point"""
    if args is None:
        args = sys.argv[1:]
    p = argparse.ArgumentParser(prog=prog, description='Formats code files '
                                'into a LateX document')
    p.add_argument('-o', '--output', type=argparse.FileType('w'), dest='out',
                   default=sys.stdout, metavar='FILE', help='Output file')
    p.add_argument('-S', '--styles', dest='liststyles', action='store_true',
                   default=False, help='List available styles')
    p.add_argument('-t', '--title', default=os.getcwd(), help='Document title')
    p.add_argument('-s', '--style', default='default', help='Formatting style')
    p.add_argument('--author', default='', help='Document author')
    p.add_argument('paths', nargs='*')
    args = p.parse_args(args)
    try:
        if args.liststyles:
            list_styles(args.out)
        else:
            if not args.paths:
                p.error('the following arguments are required: paths')
            code2tex(args.out, args.paths, style=args.style,
                     title=args.title, author=args.author)
        return 0
    except Exception as e:
        print(prog, ': error: ', e, sep='', file=sys.stderr)
        return 1
    except:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.argv[0]))
