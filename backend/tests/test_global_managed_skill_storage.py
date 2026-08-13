from __future__ import annotations

import asyncio
import json
import multiprocessing
import zipfile
from pathlib import Path

import pytest

from deerflow.skills.storage.global_managed_skill_storage import GlobalManagedSkillStorage


def _archive(path: Path, name: str = "research-assistant") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\nversion: 1.0.0\ndescription: Managed test Skill\n---\n",
        )
        archive.writestr(f"{name}/references/guide.md", "ready\n")


async def _allow_scan(_skill_dir: Path, _skill_name: str) -> None:
    return None


def _install_in_process(base_dir: str, archive_path: str, start, results) -> None:
    storage = GlobalManagedSkillStorage(base_dir=Path(base_dir))
    start.wait()
    try:
        result = asyncio.run(
            storage.install_from_archive(
                archive_path,
                receiver_metadata={
                    "contractVersion": "1.1.0",
                    "skillVersionId": "sv.research-assistant.1.0.0",
                    "version": "1.0.0",
                    "runtimeSkillName": "research-assistant",
                    "packageDigest": "sha256:" + "a" * 64,
                },
                precommit_scan=_allow_scan,
            )
        )
        results.put(("ok", result["skill_name"]))
    except Exception as exc:  # pragma: no cover - asserted in parent process
        results.put((type(exc).__name__, str(exc)))


@pytest.mark.asyncio
async def test_global_install_is_invisible_until_atomic_commit_and_load_probe(tmp_path: Path) -> None:
    archive = tmp_path / "research.skill"
    _archive(archive)
    storage = GlobalManagedSkillStorage(base_dir=tmp_path / "state")
    scan_entered = asyncio.Event()
    release_scan = asyncio.Event()

    async def blocked_scan(skill_dir: Path, skill_name: str) -> None:
        assert skill_name == "research-assistant"
        assert skill_dir.is_dir()
        scan_entered.set()
        await release_scan.wait()

    task = asyncio.create_task(
        storage.install_from_archive(
            archive,
            receiver_metadata={
                "contractVersion": "1.1.0",
                "skillVersionId": "sv.research-assistant.1.0.0",
                "version": "1.0.0",
                "runtimeSkillName": "research-assistant",
                "packageDigest": "sha256:" + "a" * 64,
            },
            precommit_scan=blocked_scan,
        )
    )
    await scan_entered.wait()

    assert not storage.skill_dir("research-assistant").exists()
    assert storage.load_probe("research-assistant") is None

    release_scan.set()
    result = await task
    probe = storage.load_probe("research-assistant")

    assert result["skill_name"] == "research-assistant"
    assert probe is not None and probe.name == "research-assistant"
    assert probe.category.value == "integrations"
    metadata = json.loads((storage.skill_dir("research-assistant") / ".nexus-receiver.json").read_text())
    assert metadata["skillVersionId"] == "sv.research-assistant.1.0.0"
    assert not any(path.name.startswith(".staging-") for path in storage.provider_root.iterdir())


def test_two_processes_cannot_commit_the_same_global_skill(tmp_path: Path) -> None:
    archive = tmp_path / "research.skill"
    _archive(archive)
    base_dir = tmp_path / "state"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [context.Process(target=_install_in_process, args=(str(base_dir), str(archive), start, results)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    outcomes = sorted(results.get(timeout=2)[0] for _ in processes)
    assert outcomes == ["SkillAlreadyExistsError", "ok"]
    assert GlobalManagedSkillStorage(base_dir=base_dir).load_probe("research-assistant") is not None
