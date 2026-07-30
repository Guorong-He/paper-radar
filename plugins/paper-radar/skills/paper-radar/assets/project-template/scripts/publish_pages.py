#!/usr/bin/env python3
"""Publish Paper Radar static site to GitHub Pages via git push.

Requires a fine-grained GitHub token with Contents: Read and write access to
Guorong-He/paper-radar. Read token from PAPER_RADAR_GITHUB_TOKEN or GITHUB_TOKEN,
with optional fallback to a local .env file.
"""

import json
import os
import base64
import hashlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path
from datetime import date

from paper_radar.cli import verify_publication


REPO = os.getenv("PAPER_RADAR_GITHUB_REPO", "Guorong-He/paper-radar")
BRANCH = os.getenv("PAPER_RADAR_GITHUB_BRANCH", "main")
SITE_DIR = Path(os.getenv("PAPER_RADAR_SITE_DIR", "site"))
WORKSPACE = Path.cwd()


def main() -> None:
    load_dotenv(Path(".env"))
    token = os.getenv("PAPER_RADAR_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise SystemExit(
            "Missing PAPER_RADAR_GITHUB_TOKEN. Create a fine-grained GitHub token "
            "for Guorong-He/paper-radar with Contents: Read and write."
        )
    manifest = load_manifest()
    issue_dir = manifest.get("current_issue", "").rstrip("/")
    if not issue_dir.startswith("issues/"):
        raise SystemExit(f"Invalid current_issue in site/manifest.json: {manifest.get('current_issue')!r}")

    optimize_site_images(issue_dir)

    if can_reach_github_web():
        publish_via_git(manifest, issue_dir, token)
    else:
        print("github.com:443 is unreachable; publishing through GitHub Contents API instead")
        publish_via_contents_api(manifest, issue_dir, token)

    owner, repo = REPO.split("/", 1)
    public_url = os.getenv("PAPER_RADAR_PUBLIC_URL") or f"https://{owner.lower()}.github.io/{repo}/"
    verify_publication(
        public_url,
        site_dir=str(SITE_DIR),
        issue_date=date.fromisoformat(manifest["issue_date"]),
        retries=int(os.getenv("PAPER_RADAR_PUBLISH_VERIFY_RETRIES", "6")),
        delay_seconds=float(os.getenv("PAPER_RADAR_PUBLISH_VERIFY_DELAY_SECONDS", "20")),
        timeout=int(os.getenv("PAPER_RADAR_PUBLISH_VERIFY_TIMEOUT_SECONDS", "20")),
        output_path="output/public_verification.json",
    )
    print("Published Paper Radar site")
    print(f"Latest: {public_url.rstrip('/')}/latest/")
    print(f"Issue:  {public_url.rstrip('/')}/{manifest['current_issue']}")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def load_manifest() -> dict:
    path = SITE_DIR / "manifest.json"
    if not path.exists():
        raise FileNotFoundError("Run `python3 -m paper_radar.cli build-site --public-url ...` first")
    return json.loads(path.read_text(encoding="utf-8"))


def can_reach_github_web() -> bool:
    try:
        with socket.create_connection(("github.com", 443), timeout=8):
            return True
    except OSError:
        return False


def publish_via_git(manifest: dict, issue_dir: str, token: str) -> None:
    with tempfile.TemporaryDirectory(prefix="paper-radar-pages-") as tmp:
        repo_dir = Path(tmp) / "repo"
        run(["git", "clone", "--depth", "1", "--branch", BRANCH, f"https://github.com/{REPO}.git", str(repo_dir)])
        stage_site(repo_dir, issue_dir)
        ensure_readme(repo_dir)
        run(["git", "config", "user.name", "Paper Radar Bot"], cwd=repo_dir)
        run(["git", "config", "user.email", "paper-radar@users.noreply.github.com"], cwd=repo_dir)
        if not has_changes(repo_dir):
            print("No changes to publish")
        else:
            run(["git", "add", "-A"], cwd=repo_dir)
            run(["git", "commit", "-m", f"Publish Paper Radar {manifest['issue_date']}"], cwd=repo_dir)
            push(repo_dir, token)


def publish_via_api(manifest: dict, issue_dir: str, token: str) -> None:
    owner_repo = REPO.strip("/")
    ref = github_api("GET", f"/repos/{owner_repo}/git/ref/heads/{BRANCH}", token)
    base_commit_sha = ref["object"]["sha"]
    base_commit = github_api("GET", f"/repos/{owner_repo}/git/commits/{base_commit_sha}", token)
    base_tree_sha = base_commit["tree"]["sha"]

    upload_paths = collect_site_files(issue_dir)
    remote_tree = collect_remote_tree(owner_repo, base_tree_sha, token)
    remote_sha_set = set(remote_tree.values())
    existing_paths = {
        path
        for path in remote_tree
        if path == Path("latest")
        or str(path).startswith("latest/")
        or path == Path(issue_dir)
        or str(path).startswith(issue_dir + "/")
    }
    tree = []
    created_blobs = {}
    uploaded_count = 0
    reused_count = 0
    for rel_path, local_path in sorted(upload_paths.items()):
        content = local_path.read_bytes()
        local_sha = git_blob_sha(content)
        if local_sha in remote_sha_set:
            blob_sha = local_sha
            reused_count += 1
        elif local_sha in created_blobs:
            blob_sha = created_blobs[local_sha]
            reused_count += 1
        else:
            try:
                blob = github_api(
                    "POST",
                    f"/repos/{owner_repo}/git/blobs",
                    token,
                    {
                        "content": base64.b64encode(content).decode("ascii"),
                        "encoding": "base64",
                    },
                )
            except SystemExit as exc:
                if "/git/blobs failed: 401" not in str(exc):
                    raise
                print("Git blob upload was rejected; publishing through GitHub Contents API instead", flush=True)
                publish_via_contents_api(manifest, issue_dir, token)
                return
            blob_sha = blob["sha"]
            created_blobs[local_sha] = blob_sha
            uploaded_count += 1
            print(
                f"Uploaded {uploaded_count} new blob(s); "
                f"processed {uploaded_count + reused_count}/{len(upload_paths)} files",
                flush=True,
            )
        tree.append({"path": rel_path.as_posix(), "mode": "100644", "type": "blob", "sha": blob_sha})

    for rel_path in sorted(existing_paths - set(upload_paths)):
        tree.append({"path": rel_path.as_posix(), "mode": "100644", "type": "blob", "sha": None})
    print(
        f"Prepared GitHub tree entries: {len(upload_paths)} files, "
        f"{uploaded_count} uploaded, {reused_count} reused",
        flush=True,
    )

    new_tree = github_api(
        "POST",
        f"/repos/{owner_repo}/git/trees",
        token,
        {"base_tree": base_tree_sha, "tree": tree},
    )
    if new_tree["sha"] == base_tree_sha:
        print("No changes to publish")
        return
    new_commit = github_api(
        "POST",
        f"/repos/{owner_repo}/git/commits",
        token,
        {
            "message": f"Publish Paper Radar {manifest['issue_date']}",
            "tree": new_tree["sha"],
            "parents": [base_commit_sha],
        },
    )
    github_api(
        "PATCH",
        f"/repos/{owner_repo}/git/refs/heads/{BRANCH}",
        token,
        {"sha": new_commit["sha"]},
    )


def publish_via_contents_api(manifest: dict, issue_dir: str, token: str) -> None:
    owner_repo = REPO.strip("/")
    ref = github_api("GET", f"/repos/{owner_repo}/git/ref/heads/{BRANCH}", token)
    commit = github_api("GET", f"/repos/{owner_repo}/git/commits/{ref['object']['sha']}", token)
    remote_tree = collect_remote_tree(owner_repo, commit["tree"]["sha"], token)
    upload_paths = collect_site_files(issue_dir)
    existing_paths = {
        path
        for path in remote_tree
        if path == Path("latest")
        or str(path).startswith("latest/")
        or path == Path(issue_dir)
        or str(path).startswith(issue_dir + "/")
    }

    changed = 0
    for rel_path, local_path in sorted(upload_paths.items()):
        content = local_path.read_bytes()
        local_sha = git_blob_sha(content)
        if remote_tree.get(rel_path) == local_sha:
            continue
        payload = {
            "message": f"Publish Paper Radar {manifest['issue_date']}: {rel_path.as_posix()}",
            "content": base64.b64encode(content).decode("ascii"),
            "branch": BRANCH,
        }
        if rel_path in remote_tree:
            payload["sha"] = remote_tree[rel_path]
        github_api("PUT", f"/repos/{owner_repo}/contents/{quote(rel_path.as_posix())}", token, payload)
        changed += 1
        print(f"Contents API uploaded {changed} file(s): {rel_path.as_posix()}", flush=True)

    for rel_path in sorted(existing_paths - set(upload_paths)):
        github_api(
            "DELETE",
            f"/repos/{owner_repo}/contents/{quote(rel_path.as_posix())}",
            token,
            {
                "message": f"Publish Paper Radar {manifest['issue_date']}: remove {rel_path.as_posix()}",
                "sha": remote_tree[rel_path],
                "branch": BRANCH,
            },
        )
        changed += 1
        print(f"Contents API removed {rel_path.as_posix()}", flush=True)

    if not changed:
        print("No changes to publish")


def collect_site_files(issue_dir: str) -> dict[Path, Path]:
    roots = [
        SITE_DIR / "index.html",
        SITE_DIR / ".nojekyll",
        SITE_DIR / "manifest.json",
        SITE_DIR / "issues" / "index.html",
        SITE_DIR / "latest",
        SITE_DIR / issue_dir,
    ]
    files: dict[Path, Path] = {}
    for root in roots:
        if root.is_file():
            files[root.relative_to(SITE_DIR)] = root
        elif root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    files[path.relative_to(SITE_DIR)] = path
        else:
            raise FileNotFoundError(root)
    return files


def optimize_site_images(issue_dir: str) -> None:
    try:
        from PIL import Image
    except ImportError:
        return

    roots = [SITE_DIR / "latest" / "figures", SITE_DIR / issue_dir / "figures"]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.png")):
            if path.stat().st_size < 900_000:
                continue
            with Image.open(path) as image:
                image = image.convert("RGB") if image.mode not in {"RGB", "L"} else image.copy()
                tmp_path = path.with_suffix(".tmp.png")
                for max_side in (1400, 1100, 900):
                    candidate = image.copy()
                    candidate.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                    candidate.save(tmp_path, format="PNG", optimize=True, compress_level=9)
                    if tmp_path.stat().st_size <= 800_000:
                        break
            if tmp_path.stat().st_size < path.stat().st_size:
                tmp_path.replace(path)
                print(f"Optimized image for publishing: {path.relative_to(SITE_DIR)}", flush=True)
            else:
                tmp_path.unlink(missing_ok=True)


def collect_remote_tree(owner_repo: str, base_tree_sha: str, token: str) -> dict[Path, str]:
    payload = github_api("GET", f"/repos/{owner_repo}/git/trees/{base_tree_sha}?recursive=1", token)
    paths = {}
    for entry in payload.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path = Path(entry["path"])
        paths[path] = entry["sha"]
    return paths


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("utf-8")
    return hashlib.sha1(header + content).hexdigest()


def github_api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    for attempt in range(1, 4):
        try:
            return github_api_once(method, path, token, payload)
        except SystemExit as exc:
            message = str(exc)
            retryable = "failed: 401" in message or "timed out" in message or "Temporary failure" in message
            if not retryable or attempt == 3:
                raise
            print(f"GitHub API {method} {path} failed transiently; retrying ({attempt}/3)", flush=True)
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def github_api_once(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"GitHub API {method} {path} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SystemExit(f"GitHub API {method} {path} timed out") from exc
    return json.loads(body) if body else {}


def stage_site(repo_dir: Path, issue_dir: str) -> None:
    copy_file(SITE_DIR / "index.html", repo_dir / "index.html")
    copy_file(SITE_DIR / ".nojekyll", repo_dir / ".nojekyll")
    copy_file(SITE_DIR / "manifest.json", repo_dir / "manifest.json")
    copy_file(SITE_DIR / "issues" / "index.html", repo_dir / "issues" / "index.html")
    replace_dir(SITE_DIR / "latest", repo_dir / "latest")
    replace_dir(SITE_DIR / issue_dir, repo_dir / issue_dir)
    # Legacy per-paper PDF mirrors are not linked by the Journal Edition and
    # make the Pages artifact hundreds of megabytes larger. Keep the local
    # research cache intact, but do not redeploy those obsolete public copies.
    legacy_papers = repo_dir / "papers"
    if legacy_papers.exists():
        shutil.rmtree(legacy_papers)
        print("Pruned unreferenced legacy public paper assets", flush=True)
    optimize_repository_issue_images(repo_dir / "issues")


def optimize_repository_issue_images(issues_dir: Path) -> None:
    """Bound old issue image sizes so GitHub Pages deploys stay reliable."""

    try:
        from PIL import Image
    except ImportError:
        return
    optimized = 0
    for path in sorted(issues_dir.glob("*/figures/*.png")):
        if path.stat().st_size < 900_000:
            continue
        with Image.open(path) as image:
            image = image.convert("RGB") if image.mode not in {"RGB", "L"} else image.copy()
            tmp_path = path.with_suffix(".tmp.png")
            for max_side in (1400, 1100, 900):
                candidate = image.copy()
                candidate.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                candidate.save(tmp_path, format="PNG", optimize=True, compress_level=9)
                if tmp_path.stat().st_size <= 800_000:
                    break
        if tmp_path.stat().st_size < path.stat().st_size:
            tmp_path.replace(path)
            optimized += 1
        else:
            tmp_path.unlink(missing_ok=True)
    if optimized:
        print(f"Optimized {optimized} historical issue image(s)", flush=True)


def replace_dir(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def ensure_readme(repo_dir: Path) -> None:
    readme = repo_dir / "README.md"
    if readme.exists():
        return
    readme.write_text(
        "# Paper Radar\n\n"
        "Automated embodied-perception paper digests for Guorong He.\n\n"
        "- Latest issue: https://guorong-he.github.io/paper-radar/latest/\n"
        "- Archived issues live under `/issues/YYYY-MM-DD/`.\n",
        encoding="utf-8",
    )


def has_changes(repo_dir: Path) -> bool:
    result = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, text=True, capture_output=True, check=True)
    return bool(result.stdout.strip())


def push(repo_dir: Path, token: str) -> None:
    askpass = repo_dir / ".git" / "paper-radar-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *Username*) echo \"x-access-token\" ;;\n"
        f"  *) printf '%s\\n' '{token}' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    env = os.environ.copy()
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        run(["git", "push", "origin", BRANCH], cwd=repo_dir, env=env)
    finally:
        askpass.unlink(missing_ok=True)


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.stderr:
            sys.stderr.write(result.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd)
    for stream in (result.stdout, result.stderr):
        if stream:
            # GitHub token is never printed by git, but keep command output compact.
            sys.stdout.write(stream)


if __name__ == "__main__":
    main()
