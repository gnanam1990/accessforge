"""Real E0 containment probes using owned synthetic build source, not customer code or AT proof."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from accessforge_build_worker.process import CommandStopped
from accessforge_build_worker.sandbox import DockerSandbox, SandboxPolicy, SandboxRefused
from accessforge_build_worker.snapshot import SnapshotRefused, SourceFile, SourceSnapshot

pytestmark = pytest.mark.sandbox


@pytest.fixture()
def sandbox() -> DockerSandbox:
    image = os.environ.get("ACCESSFORGE_SANDBOX_IMAGE")
    if not image:
        pytest.skip("real sandbox proof unavailable: ACCESSFORGE_SANDBOX_IMAGE not provisioned")
    return DockerSandbox(SandboxPolicy(image=image, wall_seconds=30, log_bytes=8192))


def _source(program: str) -> SourceSnapshot:
    return SourceSnapshot((SourceFile("build.js", program.encode()),))


def test_real_build_exports_host_hashed_bytes_and_confirms_cleanup(sandbox: DockerSandbox) -> None:
    source = _source(
        "require('fs').writeFileSync('/work/out/index.html', '<h1>candidate</h1>');"
        "console.log('claimed-digest: definitely-not-the-real-digest');"
    )
    result = sandbox.build(source, command=("/usr/local/bin/node", "build.js"))
    assert result.source_archive_digest == source.archive_digest
    assert result.cleanup_confirmed
    assert result.artifact.files == (SourceFile("out/index.html", b"<h1>candidate</h1>"),)
    assert len(result.artifact.archive_digest) == 64
    assert "definitely-not" not in result.artifact.archive_digest


def test_host_credentials_mounts_and_root_writes_are_unavailable(
    sandbox: DockerSandbox,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ACCESSFORGE_HOST_SECRET_CANARY", "host-only-synthetic-canary")
    host_canary = tmp_path / "host-only"
    host_canary.write_text("synthetic-host-file")
    program = r"""
const fs = require('fs');
const assert = require('assert/strict');
assert.equal(process.getuid(), 65532);
assert.equal(process.env.ACCESSFORGE_HOST_SECRET_CANARY, undefined);
for (const p of HOST_PATHS) {
  assert.throws(() => fs.readFileSync(p));
}
assert.throws(() => fs.writeFileSync('/host-root-write', 'forbidden'));
const status = fs.readFileSync('/proc/self/status', 'utf8');
assert.match(status, /NoNewPrivs:\s+1/);
assert.match(status, /Seccomp:\s+2/);
assert.match(status, /CapEff:\s+0000000000000000/);
assert.equal(fs.readFileSync('/sys/fs/cgroup/memory.max', 'utf8').trim(), '268435456');
assert.equal(fs.readFileSync('/sys/fs/cgroup/pids.max', 'utf8').trim(), '64');
const cpu = fs.readFileSync('/sys/fs/cgroup/cpu.max','utf8').trim();
const [quota, period] = cpu.split(' ').map(Number);
assert.equal(quota / period, 1);
fs.writeFileSync('/work/out/proof.json', JSON.stringify({isolated: true}));
""".replace(
        "HOST_PATHS",
        json.dumps([str(host_canary), "/var/run/docker.sock", "/root/.aws/credentials"]),
    )
    result = sandbox.build(_source(program), command=("/usr/local/bin/node", "build.js"))
    assert result.cleanup_confirmed
    assert host_canary.read_text() == "synthetic-host-file"


def test_metadata_and_external_network_connections_are_refused(sandbox: DockerSandbox) -> None:
    program = """
const net = require('net'), fs = require('fs');
async function denied(host) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({host, port: 80});
    socket.setTimeout(1000, () => {
      socket.destroy(); reject(new Error('no network refusal observed'));
    });
    socket.on('connect', () => {
      socket.destroy(); reject(new Error('forbidden connection succeeded'));
    });
    socket.on('error', error => {
      if (!['ENETUNREACH','EHOSTUNREACH','ECONNREFUSED'].includes(error.code)) reject(error);
      else resolve();
    });
  });
}
(async () => {
  await denied('169.254.169.254');
  await denied('203.0.113.1');
  fs.writeFileSync('/work/out/network.txt', 'both refused');
})().catch(error => {console.error(error); process.exit(1);});
"""
    result = sandbox.build(_source(program), command=("/usr/local/bin/node", "build.js"))
    assert result.artifact.files[0].content == b"both refused"


def test_output_symlink_cannot_export_files_outside_the_artifact_directory(
    sandbox: DockerSandbox,
) -> None:
    source = _source("require('fs').symlinkSync('/etc/passwd','/work/out/escape');")
    with pytest.raises(SnapshotRefused, match="links"):
        sandbox.build(source, command=("/usr/local/bin/node", "build.js"))


def test_build_exit_status_is_not_replaced_by_a_printed_success(sandbox: DockerSandbox) -> None:
    with pytest.raises(SandboxRefused, match="code 23"):
        sandbox.build(
            _source("console.log('PASS'); process.exit(23);"),
            command=("/usr/local/bin/node", "build.js"),
        )


def test_unbounded_output_is_stopped_and_the_container_is_removed(sandbox: DockerSandbox) -> None:
    with pytest.raises(CommandStopped, match="output limit"):
        sandbox.build(
            _source("process.stdout.write('x'.repeat(1000000));"),
            command=("/usr/local/bin/node", "build.js"),
        )


def test_timeout_and_cancellation_retire_the_actual_container(sandbox: DockerSandbox) -> None:
    source = _source("setInterval(() => {}, 1000);")
    sandbox.policy = SandboxPolicy(image=sandbox.policy.image, wall_seconds=3)
    with pytest.raises(CommandStopped, match="deadline"):
        sandbox.build(source, command=("/usr/local/bin/node", "build.js"))
    sandbox.policy = SandboxPolicy(image=sandbox.policy.image, wall_seconds=30)
    cancel_at = time.monotonic() + 3
    with pytest.raises(CommandStopped, match="cancelled"):
        sandbox.build(
            source,
            command=("/usr/local/bin/node", "build.js"),
            cancelled=lambda: time.monotonic() >= cancel_at,
        )


def test_tmpfs_capacity_is_a_real_bound_not_only_a_launch_flag(sandbox: DockerSandbox) -> None:
    program = """
const fs = require('fs'), assert = require('assert/strict');
const fd = fs.openSync('/work/fill', 'w');
let written = 0, refused = false;
try {
  while (written < 80*1024*1024) {
    fs.writeSync(fd, Buffer.alloc(1024*1024)); written += 1024*1024;
  }
} catch (error) { assert.equal(error.code, 'ENOSPC'); refused = true; }
finally { fs.closeSync(fd); fs.unlinkSync('/work/fill'); }
assert.equal(refused, true);
fs.writeFileSync('/work/out/bounded.txt', String(written));
"""
    result = sandbox.build(_source(program), command=("/usr/local/bin/node", "build.js"))
    assert int(result.artifact.files[0].content) < 80 * 1024 * 1024
