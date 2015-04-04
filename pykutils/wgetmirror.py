import subprocess
import sys


def main(args=None, prog=None):
    if args is None:
        args = sys.argv[1:]
    return subprocess.call(['wget', '-k', '-m', '-p', '-np', '-E'] + args)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:], sys.argv[0]))
