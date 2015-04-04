"""make btrfs backups"""
# TODO: Parse SI units for --bwlimit (Current code assumes number is in KB)
# TODO: VT100 progress bar
# TODO: Remove duplicate code
import argparse
import collections
import logging
import os
import os.path
import re
import string
import subprocess
import sys
import tempfile
import time
import uuid


class Host(object):
    def __init__(self):
        self._devnull = open(os.devnull, 'wb')

    def get_date(self, fmt):
        args = ['date', '-u', '+{0}'.format(fmt)]
        p = self._popen(args, stdout=subprocess.PIPE)
        stdout, _ = p.communicate()
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, args)
        stdout = stdout.decode().strip()
        return stdout

    def read_file(self, path):
        """Read file and returns its contents"""
        args = ['cat', path]
        p = self._popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, _ = p.communicate()
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, args)
        stdout = stdout.decode().strip()
        return stdout

    def kill(self, pid):
        """Kills process ID. Ignores errors."""
        args = ['kill', str(pid)]
        p = self._popen(args, stderr=subprocess.PIPE)
        _, _ = p.communicate()

    def list_subvolumes(self, path, snapshot=False, readonly=False):
        """Returns list of subvolumes in filesystem `path`"""
        args = ['btrfs', 'subvolume', 'list', '-a', '-o']
        if snapshot:
            args.append('-s')
        if readonly:
            args.append('-r')
        args.append(path)
        p = self._popen(args, stdout=subprocess.PIPE)
        stdout, _ = p.communicate()
        if p.returncode != 0:
            raise subprocess.CalledProcessError(p.returncode, args)
        x = br'^.+ path ([^\n]+)$'
        paths = re.findall(x, stdout, re.MULTILINE)
        paths = [os.fsdecode(x) for x in paths]
        return paths

    def delete_subvolume(self, *paths):
        if not paths:
            raise ValueError('At least one path required')
        args = ['btrfs', 'subvolume', 'delete'] + list(paths)
        p = self._popen(args)
        if p.wait() != 0:
            raise subprocess.CalledProcessError(p.returncode, args)

    def snapshot(self, src, dst):
        args = ['btrfs', 'subvolume', 'snapshot', '-r', src, dst]
        p = self._popen(args)
        if p.wait() != 0:
            raise subprocess.CalledProcessError(p.returncode, args)

    def send(self, subvol, parent=None):
        """Performs btrfs send"""
        args = 'btrfs send'
        if parent:
            args += ' -p {0}'.format(subprocess.list2cmdline([parent]))
        args += ' ' + subprocess.list2cmdline([subvol]) + ' &'
        pidpath = os.path.join(os.sep, 'tmp', uuid.uuid1().hex)
        args += ' echo $! > {0} ;'.format(subprocess.list2cmdline([pidpath]))
        args += ' wait; rm {0};'.format(subprocess.list2cmdline([pidpath]))
        p = self._popen(args, stdout=subprocess.PIPE, shell=True)
        return p, pidpath

    def receive(self, path):
        """Performs btrfs receive into `path`"""
        args = ['btrfs', 'receive', path]
        p = self._popen(args, stdin=subprocess.PIPE)
        return p

    def sync(self, path):
        args = ['btrfs', 'filesystem', 'sync', path]
        p = self._popen(args)
        if p.wait() != 0:
            raise subprocess.CalledProcessError(p.returncode, args)


class LocalHost(Host):
    def _popen(self, *args, **kwargs):
        if 'stdout' not in kwargs:
            kwargs['stdout'] = self._devnull.fileno()
        if 'stdin' not in kwargs:
            kwargs['stdin'] = subprocess.PIPE
        return subprocess.Popen(*args, **kwargs)

    def __str__(self):
        return 'localhost'


class SSHHost(Host):
    __p = None

    def __init__(self, host):
        Host.__init__(self)
        self.host = host
        self.__d = tempfile.TemporaryDirectory()
        self.__ctl_path = os.path.join(self.__d.name, 'ssh-%h-%p-%r')
        self.__args = ['ssh', '-T', '-oControlMaster=yes',
                       '-oControlPath={0}'.format(self.__ctl_path), host,
                       'echo OK; cat >/dev/null']
        self.__p = subprocess.Popen(self.__args,
                                    stdout=subprocess.PIPE,
                                    stdin=subprocess.PIPE,
                                    preexec_fn=os.setsid)
        # Wait for connection to be successful
        self.__p.stdout.read(3)

    def _popen(self, cmd, *args, **kwargs):
        if self.__p.poll() is not None:
            raise subprocess.CalledProcessError(self.__p.returncode,
                                                self.__args)
        if not isinstance(cmd, str):
            cmd = subprocess.list2cmdline(cmd)
        cmd = ['ssh', '-oControlMaster=no',
               '-oControlPath={0}'.format(self.__ctl_path),
               self.host, cmd]
        if 'stdout' not in kwargs:
            kwargs['stdout'] = self._devnull.fileno()
        if 'stdin' not in kwargs:
            kwargs['stdin'] = subprocess.PIPE
        if 'shell' in kwargs:
            del kwargs['shell']  # For SSH, shell is always True
        p = subprocess.Popen(cmd, *args, **kwargs)
        return p

    def __del__(self):
        if self.__p and self.__p.poll():
            try:
                self.__p.terminate()
                self.__p.wait()
            except OSError:
                pass

    def __str__(self):
        return self.host


def parse_host_path(x):
    host, sep, path = x.partition(':')
    if sep:
        return SSHHost(host), path
    else:
        return LocalHost(), host


def parse_subvols(subvols, fmt):
    """Returns (volname, voltime) for each subvol"""
    def parse(x):
        try:
            return x, time.strptime(x, fmt)
        except ValueError:
            return None
    y = (parse(x) for x in subvols)
    return [x for x in y if x]


def find_parent(src_subvols, dst_subvols, fmt):
    """Return a suitable backup parent between `src` and `dst`, or None"""
    subvols = [x for x in src_subvols if x in dst_subvols]
    subvols = parse_subvols(subvols, fmt)
    subvols.sort(key=lambda x: x[1], reverse=True)
    return subvols[0][0] if subvols else None


class SI(int):
    def __str__(self):
        return '{0:.3f}'.format(self)

    def __format__(self, fmt):
        num = int(self)
        for unit in ' KMGTPEZ':
            if abs(num) < 1000:
                return '{0:{1}}{2}'.format(num, fmt, unit)
            num /= 1000
        else:
            return '{0:{1}}Y'.format(num, fmt)


def print_progress(total, cur_speed, avg_speed):
    """Prints progress to sys.stdout"""
    print('{0:.1f}B [{1:.1f}B/s, {2:.1f}B/s]'.format(SI(total),
                                                     SI(cur_speed),
                                                     SI(avg_speed)))


class StreamTracker(object):
    """Tracks progress, limits bandwidth of a stream."""

    def __init__(self, bwlimit=0, progress_callback=None):
        #: Bandwidth limit in bytes per second
        self.bwlimit = bwlimit
        #: Progress callback handler
        self.progress_callback = progress_callback
        self.__numbytes = 0
        self.__start_time = self.__last_count_time = time.time()
        self.__lastnumbytes = 0

    def __call__(self, numbytes):
        self.__numbytes += numbytes
        # Calculate transfer speed every second or more
        now = time.time()
        if now >= self.__last_count_time + 1.0 or numbytes == 0:
            # Current speed (from last count)
            cur_speed = ((self.__numbytes - self.__lastnumbytes) /
                         (now - self.__last_count_time))
            if self.bwlimit and cur_speed > self.bwlimit:
                diff = cur_speed - self.bwlimit
                delay = diff / self.bwlimit
                time.sleep(delay)
                now = time.time()
                # Calculate cur_speed again
                cur_speed = ((self.__numbytes - self.__lastnumbytes) /
                             (now - self.__last_count_time))
            if self.progress_callback:
                avg_speed = self.__numbytes / (now - self.__start_time)
                self.progress_callback(self.__numbytes, cur_speed, avg_speed)
            self.__last_count_time = now
            self.__lastnumbytes = self.__numbytes


def copyfileobj(src, dst, length=16*1024, callback=None):
    """Copy the contents of file `src` to file `dst`."""
    if not length:
        length = 16*1024
    while True:
        buf = src.read(length)
        if not buf:
            if callback:
                callback(0)
            break
        dst.write(buf)
        if callback:
            callback(len(buf))


def send_receive(volname, src, src_dir, parent, dst, dst_dir, blksize=0,
                 bwlimit=0, progress=False):
    """btrfs send-receive"""
    if progress:
        progress = print_progress
    else:
        progress = False
    tracker = StreamTracker(bwlimit, progress)
    src_p, pidpath = src.send(os.path.join(src_dir, volname), parent)
    dst_p = dst.receive(dst_dir)
    try:
        copyfileobj(src_p.stdout, dst_p.stdin, blksize, tracker)
        if src_p.wait() != 0:
            raise subprocess.CalledProcessError(src_p.returncode, [])
        dst_p.stdin.close()
        if dst_p.wait() != 0:
            raise subprocess.CalledProcessError(dst_p.returncode, [])
    except:
        try:
            src_p.terminate()
            src_p.wait()
        except OSError:
            # Ignore OSError which can occur if src_p does not exist
            pass
        try:
            dst_p.terminate()
            dst_p.wait()
        except OSError:
            # Ignore OSError which can occur if dst_p does not exist
            pass
        # Send termination signal to btrfs send process if it still exists as
        # src may not have noticed src_p has terminated.
        try:
            pid = int(src.read_file(pidpath))
            src.kill(pid)
        except subprocess.CalledProcessError:
            pass
        dst.sync(dst_dir)  # Force sync to prevent subvolume busy errors
        dst.delete_subvolume(os.path.join(dst_dir, volname))
        raise


def backup(src, src_path, dst, dst_path, fmt, parent_fmt, blksize=0, bwlimit=0,
           progress=False):
    """Make a backup"""
    # Ensure that clocks of src, dst and this process are synchronized
    date_fmt = '%Y%m%d%H%M'
    if not (src.get_date(date_fmt) == dst.get_date(date_fmt)
            == time.strftime(date_fmt, time.gmtime())):
        raise RuntimeError('Clocks are not synchronized')
    # Get src subvolumes and snapshots
    src_voldir, src_volname = os.path.split(src_path)
    src_subvols = src.list_subvolumes(src_voldir)
    if src_volname not in src_subvols:
        raise ValueError('{0} is not a subvolume'.format(src_path))
    src_snapshots = src.list_subvolumes(src_voldir, snapshot=True,
                                        readonly=True)
    # Substitute $name
    fmt = string.Template(fmt).safe_substitute(name=src_volname)
    parent_fmt = string.Template(parent_fmt).safe_substitute(name=src_volname)
    # Get dst subvolumes and snapshots
    dst_subvols = dst.list_subvolumes(dst_path)
    dst_snapshots = dst.list_subvolumes(dst_path, snapshot=True, readonly=True)
    # Find suitable parent snapshot
    src_parent = find_parent(src_snapshots, dst_snapshots, parent_fmt)
    if src_parent:
        src_parentpath = os.path.join(src_voldir, src_parent)
    else:
        src_parentpath = None
    # Snapshot name
    src_snapname = time.strftime(fmt, time.gmtime())
    src_snappath = os.path.join(src_voldir, src_snapname)
    # Ensure src_snapname does not exist in dst
    if src_snapname in dst_subvols:
        # This usually means that a transfer was stopped half-way (e.g. power
        # loss) and we did not have a chance to clean up, OR multiple btrups
        # were started at the same time
        raise RuntimeError('{0} already exists in dst!'.format(src_snapname))
    # Generate backup snapshot in src (or fail if there is already a snapshot)
    src.snapshot(src_path, src_snappath)
    src.sync(src_voldir)
    try:
        send_receive(src_snapname, src, src_voldir, src_parentpath, dst,
                     dst_path, blksize, bwlimit, progress)
    except:
        src.sync(src_voldir)  # Force sync to prevent subvolume busy errors
        src.delete_subvolume(src_snappath)
        raise


def clean(src, src_path, dst, dst_path, fmt, parent_fmt, keep=0):
    """Clean obsolete backups"""
    src_voldir, src_volname = os.path.split(src_path)
    fmt = string.Template(fmt).safe_substitute(name=src_volname)
    parent_fmt = string.Template(parent_fmt).safe_substitute(name=src_volname)
    src_snapshot_names = src.list_subvolumes(src_voldir, snapshot=True,
                                             readonly=True)
    src_snapshots = parse_subvols(src_snapshot_names, fmt)
    dst_subvols = dst.list_subvolumes(dst_path)
    dst_snapshot_names = dst.list_subvolumes(dst_path, snapshot=True,
                                             readonly=True)
    dst_snapshots = parse_subvols(dst_snapshot_names, parent_fmt)
    # Remove snapshots that exist in src but are not a subvolume in dst
    src_rm_snapshots = [x[0] for x in src_snapshots if x[0] not in dst_subvols]
    # Remove snapshots that exist in dst but not in src
    dst_rm_snapshots = [x[0] for x in dst_snapshots
                        if x[0] not in src_snapshot_names]
    # Get snapshots that exist in src and dst
    shared_snapshots = [x for x in src_snapshots if x[0] in dst_snapshot_names]
    shared_snapshots.sort(key=lambda x: x[1], reverse=True)
    if keep and len(shared_snapshots) > keep:
        src_rm_snapshots.extend(x[0] for x in shared_snapshots[keep:])
        dst_rm_snapshots.extend(x[0] for x in shared_snapshots[keep:])
    src_rm_snapshots = [os.path.join(src_voldir, x) for x in src_rm_snapshots]
    dst_rm_snapshots = [os.path.join(dst_path, x) for x in dst_rm_snapshots]
    if src_rm_snapshots:
        src.delete_subvolume(*src_rm_snapshots)
    if dst_rm_snapshots:
        dst.delete_subvolume(*dst_rm_snapshots)


def btrup(src, dst, fmt, parent_fmt=None, blksize=0, bwlimit=0,
          progress=False, keep=0):
    """Make a btrfs backup"""
    if parent_fmt is None:
        parent_fmt = fmt
    src, src_path = parse_host_path(src)
    dst, dst_path = parse_host_path(dst)
    backup(src, src_path, dst, dst_path, fmt, parent_fmt, blksize, bwlimit,
           progress)
    clean(src, src_path, dst, dst_path, fmt, parent_fmt, keep)


def main(args=None, prog=None):
    """Main entry point"""
    if args is None:
        args = sys.argv[1:]
    p = argparse.ArgumentParser(prog=prog)
    p.add_argument('--progress', default=False, action='store_true',
                   help='Show progress')
    p.add_argument('--bwlimit', default=0, type=int, dest='bwlimit',
                   help='Bandwidth limit')
    p.add_argument('-B', '--block-size', type=int, default=0, dest='blksize',
                   help='Force a fixed block size')
    p.add_argument('-f', '--format', default='.$name-%Y-%m-%d-%H-%M-%S',
                   dest='fmt', help='Backup name format')
    p.add_argument('--parent-format', default=None, dest='parent_fmt',
                   help='Parent backup name format')
    p.add_argument('-k', '--keep', default=0, type=int,
                   help='Number of backups to keep')
    p.add_argument('src',
                   help='btrfs subvolume src')
    p.add_argument('dest',
                   help='btrfs volume dest')
    args = p.parse_args(args)
    try:
        bwlimit = args.bwlimit * 1000  # in kB
        btrup(args.src, args.dest, args.fmt, args.parent_fmt, args.blksize,
              bwlimit, args.progress, args.keep)
    except subprocess.CalledProcessError as e:
        return 1
    except Exception as e:
        print(prog, ': error: ', e, sep='', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:], sys.argv[0]))
