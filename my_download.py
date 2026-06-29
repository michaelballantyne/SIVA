import os
import re
import shlex
import hashlib
import subprocess
from dataclasses import dataclass


# A remote endpoint with its probed auth method. Built once by
# establish_connection() and reused by transfer() — so pulling a whole timestep
# series authenticates once rather than per file.
@dataclass
class Connection:
    user: str        # may be None
    host: str
    target: str      # "user@host" or "host"
    method: str      # 'ssh-key' (BatchMode works) or 'password' (paramiko fallback)


# Probe a remote endpoint's auth ONCE. No bytes transferred. Splits the source,
# then tries a trivial BatchMode ssh command: if it succeeds, key auth works
# (rsync/scp will too); otherwise we fall back to a paramiko password login at
# transfer time.
def establish_connection(remote_source):
    user, host, _ = _parse_remote(remote_source)
    target = f"{user}@{host}" if user else host
    method = "ssh-key" if _ssh_query(target, "true") is not None else "password"
    return Connection(user=user, host=host, target=target, method=method)


# Move bytes for one file/dir over an established Connection.
#
# Transfer strategy (unchanged from the old monolithic download):
#     1. rsync over ssh (progress display, resumes partial transfers)
#     2. scp (if rsync is missing)
#     3. paramiko SFTP with password prompt (if SSH keys are not set up)
# rsync/scp run in BatchMode (key auth only) because a password prompt inside a
# Jupyter kernel would hang with no terminal to type into.
def transfer(connection, remote_path, local_path, size_warn_mb=500):
    target = connection.target
    remote_source = f"{target}:{remote_path}"

    # Ensure destination directory exists
    dest_dir = os.path.dirname(local_path)
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)

    # Skip if we already have an identical copy (MD5 match with remote).
    # Best-effort: needs key auth for the remote md5sum; silently skipped otherwise.
    if os.path.isfile(local_path):
        remote_md5 = _remote_md5(target, remote_path)
        if remote_md5 and remote_md5 == _local_md5(local_path):
            print(f"✓ {local_path} already matches remote (MD5) — skipping download.")
            return local_path

    # Warn + confirm before pulling a large file.
    # Best-effort: needs key auth for the remote stat; silently skipped otherwise.
    size_bytes = _remote_size(target, remote_path)
    if size_bytes and size_bytes > size_warn_mb * 1e6:
        resp = input(f"Remote file is {size_bytes / 1e6:.1f} MB "
                     f"(> {size_warn_mb} MB). Download? [y/N]: ").strip().lower()
        if resp not in ('y', 'yes'):
            print("Download cancelled.")
            return None

    print(f"Downloading from {connection.host}:")
    print(f"  remote: {remote_path}")
    print(f"  local:  {local_path}")

    # Key-based transfer (rsync preferred, scp fallback) when the probe found a key.
    if connection.method == "ssh-key":
        if _have_cmd('rsync'):
            ok, err = _run_transfer([
                'rsync', '-a', '--progress', '--partial',
                '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=15',
                remote_source, local_path
            ])
            if ok:
                return _report_success(local_path)
            if not _is_auth_error(err):
                raise RuntimeError(f"rsync failed:\n{err}")
        elif _have_cmd('scp'):
            ok, err = _run_transfer([
                'scp', '-r', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                remote_source, local_path
            ])
            if ok:
                return _report_success(local_path)
            if not _is_auth_error(err):
                raise RuntimeError(f"scp failed:\n{err}")
        print("⚠ SSH key authentication not available, trying password login...")

    # Password fallback (works inside Jupyter).
    if _download_paramiko(connection.user, connection.host, remote_path, local_path):
        return _report_success(local_path)

    u = connection.user
    raise RuntimeError(
        "Could not authenticate to the remote server.\n\n"
        "To enable passwordless transfers, set up SSH keys (one time):\n"
        f"  ssh-keygen -t ed25519            # press enter at every prompt\n"
        f"  ssh-copy-id {u + '@' if u else ''}{connection.host}   # type your password once\n\n"
        "After that, transfers will work without a password."
    )


# Back-compat shim: establish a connection, then transfer one file.
#     download("user@host:/remote/file.hdf5", "data/file.hdf5")
def download(remote_source, local_path, size_warn_mb=500):
    conn = establish_connection(remote_source)
    _, _, remote_path = _parse_remote(remote_source)
    return transfer(conn, remote_path, local_path, size_warn_mb)


# Split "user@host:/path" into (user, host, path). User is optional.
def _parse_remote(remote_source):
    m = re.match(r'^(?:([^@:]+)@)?([^:]+):(.+)$', remote_source)
    if not m:
        raise ValueError(
            f"Invalid remote source: {remote_source!r}\n"
            "Expected format: 'user@host:/path/to/file' or 'host:/path/to/file'"
        )
    return m.group(1), m.group(2), m.group(3)


def _have_cmd(name):
    return subprocess.run(['which', name], capture_output=True).returncode == 0


# Run a one-off ssh command in BatchMode (key auth only, never prompts).
# Returns stdout on success, or None if it failed / auth unavailable.
def _ssh_query(target, command):
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', target, command],
        capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


# Remote file size in bytes (GNU stat), or None if unavailable.
def _remote_size(target, remote_path):
    out = _ssh_query(target, f"stat -c %s {shlex.quote(remote_path)}")
    try:
        return int(out.strip()) if out else None
    except ValueError:
        return None


# Remote MD5 hex digest, or None if unavailable.
def _remote_md5(target, remote_path):
    out = _ssh_query(target, f"md5sum {shlex.quote(remote_path)}")
    return out.split()[0] if out else None


# Local MD5 hex digest, streamed in chunks so large files don't blow up memory.
def _local_md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# Run a transfer command, streaming progress output. Returns (ok, stderr_text).
def _run_transfer(cmd):
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            print(f"  {line}")
    proc.wait()
    err = proc.stderr.read()
    return proc.returncode == 0, err


def _is_auth_error(stderr_text):
    markers = ['permission denied', 'host key verification failed',
               'authentication failed', 'publickey']
    text = stderr_text.lower()
    return any(m in text for m in markers)


# SFTP fallback: paramiko can prompt for a password inside Jupyter.
def _download_paramiko(user, host, remote_path, local_path):
    try:
        import paramiko
    except ImportError:
        print("⚠ paramiko not installed (pip install paramiko) — cannot do password login.")
        return False

    import getpass
    import stat as statmod

    user = user or getpass.getuser()
    password = getpass.getpass(f"Password for {user}@{host}: ")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, password=password, timeout=15)
        sftp = client.open_sftp()

        def fetch(rpath, lpath):
            if statmod.S_ISDIR(sftp.stat(rpath).st_mode):
                os.makedirs(lpath, exist_ok=True)
                for entry in sftp.listdir(rpath):
                    fetch(f"{rpath}/{entry}", os.path.join(lpath, entry))
            else:
                size = sftp.stat(rpath).st_size
                print(f"  {rpath} ({size / 1e6:.1f} MB)")
                sftp.get(rpath, lpath)

        fetch(remote_path, local_path)
        sftp.close()
        return True
    except paramiko.AuthenticationException:
        print("✗ Wrong password.")
        return False
    finally:
        client.close()


def _report_success(local_path):
    if os.path.isdir(local_path):
        total = sum(
            os.path.getsize(os.path.join(d, f))
            for d, _, files in os.walk(local_path) for f in files
        )
    else:
        total = os.path.getsize(local_path)
    print(f"\n✓ Downloaded {total / 1e6:.1f} MB to {local_path}")
    return local_path
