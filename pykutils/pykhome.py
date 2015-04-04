"""home directory manager

"""
import argparse
import logging
import os
import os.path
import stat
import subprocess
import sys
import re
import urllib.request
import tempfile
import hashlib
import glob
import mako.template


logger = logging.getLogger(__name__)


homedir = os.path.expanduser('~')


"""
hometpl
"""


def update_hometpl(path, work_tree=None):
    """Update `path`. Warn if it is not gitignored"""
    if not work_tree:
        work_tree = homedir
    tpl = mako.template.Template(filename=path)
    root, _ = os.path.splitext(path)
    out = tpl.render()
    # Ensure output file can be written
    if os.path.exists(root):
        st_mode = os.stat(root).st_mode
        st_mode |= stat.S_IWUSR
        os.chmod(root, st_mode)
    # git rm the output file
    subprocess.check_call(['git', '--work-tree={0}'.format(work_tree),
                           'rm', '-f', '-q', '--ignore-unmatch', root])
    # Write output file
    f = open(root, 'w')
    f.write(out)
    f.close()
    # Ensure output file cannot be written
    st_mode = os.stat(path).st_mode
    st_mode &= ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    os.chmod(root, st_mode)
    # Warn if output file is not gitignored
    ret = subprocess.call(['git', '--work-tree={0}'.format(work_tree),
                           'check-ignore', '-q', root])
    if ret != 0:
        logger.warn('%s is not ignored', root)


def update_all_hometpl(work_tree=None):
    """Update all .hometpl files"""
    if work_tree is None:
        work_tree = homedir
    # Get files in work_tree
    paths = subprocess.check_output(['git',
                                     '--work-tree={0}'.format(work_tree),
                                     'ls-files', '--full-name'],
                                    cwd=work_tree,
                                    universal_newlines=True)
    paths = [os.path.join(work_tree, x.strip()) for x in paths.split('\n')
             if x]
    for path in paths:
        if path.endswith('.hometpl'):
            logger.debug('Updating %s', path)
            update_hometpl(path, work_tree)
    # Get submodules
    paths = subprocess.check_output(['git',
                                     '--work-tree={0}'.format(work_tree),
                                     'submodule', '--quiet', 'foreach',
                                     'echo $path'],
                                    cwd=work_tree,
                                    universal_newlines=True)
    paths = [os.path.join(work_tree, x.strip()) for x in paths.split('\n')
             if x]
    for path in paths:
        update_all_hometpl(path)


"""
ssh
"""


def update_ssh():
    """Update SSH files, ensure permissions are correct"""
    ssh_dir = os.path.join(homedir, '.ssh')
    logger.debug('Ensure .ssh directory has perm 700')
    os.chmod(ssh_dir, 0o700)
    logger.debug('Ensure .ssh/authorized_keys has perm 644')
    if os.path.exists(os.path.join(ssh_dir, 'authorized_keys')):
        os.chmod(os.path.join(ssh_dir, 'authorized_keys'), 0o644)


"""
Debian Packages
"""


def get_debian_packages():
    """Returns configured list of packages to install."""
    # Of course, comments need to be removed
    f = open(os.path.join(homedir, '.config', 'pykhome', 'packages.debian'))
    out = []
    for line in f:
        line, _, _ = line.partition('#')
        line = line.strip()
        if line:
            out.append(line)
    f.close()
    return out


def get_cached_debian_packages():
    """Returns already-checked installed packages (from cache)"""
    try:
        f = open(os.path.join(homedir, '.cache', 'pykhome', 'packages.debian'))
    except IOError:
        return []
    lines = [x.strip() for x in f.readlines() if x.strip()]
    return lines


def write_cached_debian_packages(packages):
    packages = [x + '\n' for x in sorted(packages)]
    dst = os.path.join(homedir, '.cache', 'pykhome', 'packages.debian')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    f = open(dst, 'w')
    f.writelines(packages)
    f.close()


def download_debian_packages(packages, dst=None):
    if not dst:
        dst = os.path.join(homedir, '.cache', 'pykhome', 'debian')
    os.makedirs(dst, exist_ok=True)
    cmd = (['apt-get', 'install', '-qq', '-d', '--print-uris', '-y'] +
           list(packages))
    lines = subprocess.check_output(cmd, universal_newlines=True).split('\n')
    lines = [x for x in lines if x]
    for line in lines:
        m = re.match(r"^'([^']+)' ([^ ]+) (\d+) MD5Sum:([a-z0-9]+)$", line)
        if not m:
            raise ValueError('Could not match {0}'.format(line))
        url, filename, size, md5 = m.groups()
        outpath = os.path.join(dst, filename)
        if os.path.exists(outpath):
            m = hashlib.md5()
            f = open(outpath, 'rb')
            while 1:
                buf = f.read(16*1024)
                if not buf:
                    break
                m.update(buf)
            f.close()
            if m.hexdigest() == md5:
                continue
        tries = 0
        while tries < 3:
            logger.info('Downloading %s', url)
            dstf = open(outpath, 'wb')
            srcf = urllib.request.urlopen(url)
            m = hashlib.md5()
            while 1:
                buf = srcf.read(16*1024)
                if not buf:
                    break
                m.update(buf)
                dstf.write(buf)
            hexdigest = m.hexdigest()
            if hexdigest != md5:
                logger.warn('MD5 checksum error %s != %s', hexdigest, md5)
                tries += 1
            else:
                break
        else:
            raise RuntimeError('Could not download {0}'.format(url))


def install_debian_packages(packages, cache_dir=None):
    """Move all home-cached Debian packages to the system cache

    Move all ~/.cache/pykhome/debian deb packages to /var/cache/apt/archives
    """
    if not cache_dir:
        cache_dir = os.path.join(homedir, '.cache', 'pykhome', 'debian')
    archive_dir = '/var/cache/apt/archives'
    paths = glob.glob(os.path.join(cache_dir, '*.deb'))
    if paths:
        cmd = ['sudo', 'mv'] + paths + [archive_dir]
        subprocess.check_call(cmd)
    # First check if there any packages need to be installed
    # (prevent unnecessary sudo)
    cmd = ['apt-get', 'install', '-qq', '-s'] + list(packages)
    output = subprocess.check_output(cmd, universal_newlines=True)
    if re.search(r'^Inst ', output, re.MULTILINE):
        # Execute apt-get install
        cmd = ['sudo', 'apt-get', 'install', '-qq', '-y'] + list(packages)
        subprocess.check_call(cmd)


def update_debian():
    """Update installed Debian packages"""
    packages = get_debian_packages()
    cached_packages = get_cached_debian_packages()
    packages_set = set(packages)
    cached_packages_set = set(cached_packages)
    if packages_set.difference(cached_packages_set):
        download_debian_packages(packages)
        install_debian_packages(packages)
    write_cached_debian_packages(packages)


"""
crontab
"""


def update_crontab():
    """Update crontab"""
    path = os.path.join(homedir, '.config', 'pykhome', 'crontab')
    cmd = ['crontab', path]
    subprocess.check_call(cmd)


"""
Entry points
"""


def update():
    """Update entry point. Use this if you modified any config files"""
    update_all_hometpl()
    update_ssh()
    update_debian()
    update_crontab()


def main(args=None, prog=None):
    """Main entry point

    """
    if args is None:
        args = sys.argv[1:]
    p = argparse.ArgumentParser(prog=prog)
    s = p.add_subparsers(metavar='CMD', dest='cmd')
    p_update = s.add_parser('update', help='Update')
    args = p.parse_args(args)
    if args.cmd == 'update':
        update()
    else:
        p.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:], sys.argv[0]))
