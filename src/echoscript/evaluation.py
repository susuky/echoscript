"""Reproducible local ASR evaluation; no model imports until the run command."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import fields
import hashlib
from importlib.metadata import PackageNotFoundError, version
import itertools
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import unicodedata
import zipfile


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_text(text: str) -> str:
    """NFC/casefold, ignoring punctuation and spaces; never transliterate scripts."""
    return "".join(c for c in unicodedata.normalize("NFC", text).casefold()
                   if unicodedata.category(c)[0] in {"L", "N", "M"})


def edit_alignment(reference, hypothesis) -> dict:
    """Exact unit-cost Levenshtein alignment with deterministic tie breaking."""
    n, m = len(reference), len(hypothesis)
    # ponytail: bound quadratic work; use separately annotated clips for long recordings.
    if n * m > 25_000_000:
        raise ValueError("Reference is too long for exact alignment; split evaluation into annotated clips")
    trace = [bytearray(m + 1) for _ in range(n + 1)]
    previous = list(range(m + 1))
    for i, ref in enumerate(reference, 1):
        current = [i] + [0] * m
        for j, hyp in enumerate(hypothesis, 1):
            costs = (previous[j - 1] + (ref != hyp), previous[j] + 1, current[j - 1] + 1)
            operation = min(range(3), key=costs.__getitem__)
            current[j], trace[i][j] = costs[operation], operation
        previous = current
    i, j = n, m
    substitutions = deletions = insertions = 0
    matched: dict[int, int] = {}
    deleted: list[int] = []
    while i or j:
        operation = 2 if i == 0 else 1 if j == 0 else trace[i][j]
        if operation == 0:
            i, j = i - 1, j - 1
            if reference[i] == hypothesis[j]:
                matched[i] = j
            else:
                substitutions += 1
        elif operation == 1:
            i -= 1
            deletions += 1
            deleted.append(i)
        else:
            j -= 1
            insertions += 1
    errors = substitutions + deletions + insertions
    return {"errors": errors, "reference_units": n,
            "rate": errors / n if n else None,
            "substitutions": substitutions, "deletions": deletions, "insertions": insertions,
            "matches": matched, "deleted_indices": deleted}


def text_metrics(reference: str, hypothesis: str) -> dict:
    characters = edit_alignment(normalize_text(reference), normalize_text(hypothesis))
    verbatim = edit_alignment(unicodedata.normalize("NFC", reference), unicodedata.normalize("NFC", hypothesis))
    words = edit_alignment(
        [normalize_text(word) for word in reference.split() if normalize_text(word)],
        [normalize_text(word) for word in hypothesis.split() if normalize_text(word)],
    )
    public = lambda score: {k: v for k, v in score.items() if k not in {"matches", "deleted_indices"}}
    return {"cer": public(characters), "cer_verbatim": public(verbatim), "wer_whitespace": public(words),
            "verbatim_equal": reference == hypothesis}


def load_manifest(path: Path) -> dict:
    from echoscript.schema import JobOptions

    manifest = _read(path)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("cases"), list):
        raise ValueError("Manifest requires schema_version 1 and cases")
    for collection in (manifest["cases"], manifest.get("policies", [])):
        identifiers = [item.get("id") for item in collection]
        if any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", item) for item in identifiers):
            raise ValueError("Case and policy ids must contain only ASCII letters, digits, _ or -")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate case or policy id")
    for case in manifest["cases"]:
        reference = case.get("reference")
        if reference is None:
            continue
        if not reference.get("provenance") or not (
            reference.get("human_verified") is True or reference.get("source_kind") == "author_subtitle"
        ):
            raise ValueError(f"{case['id']}: reference requires human verification or an identified author subtitle, and provenance")
        if not isinstance(reference.get("raw_text"), str):
            raise ValueError(f"{case['id']}: reference requires raw_text (empty only for verified silence)")
        if "display_text" in reference and not isinstance(reference["display_text"], str):
            raise ValueError("display_text must be a human-approved string")
        position = 0
        normalized_reference = normalize_text(reference["raw_text"])
        for utterance in reference.get("utterances", []):
            utterance_text = normalize_text(utterance.get("text", ""))
            if not utterance_text:
                raise ValueError("Reference utterances must contain text")
            found = normalized_reference.find(utterance_text, position)
            if found < 0:
                raise ValueError("Reference utterances must occur in raw_text in order")
            position = found + len(utterance_text)
            if "start" in utterance or "end" in utterance:
                start, end = utterance.get("start"), utterance.get("end")
                if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
                    raise ValueError("Reference utterance times must be finite, positive intervals")
    known = {field.name for field in fields(JobOptions)}
    for policy in manifest.get("policies", []):
        options = policy.get("options", {})
        unknown = set(options) - known
        if unknown:
            raise ValueError(f"Unknown JobOptions: {sorted(unknown)}")
        JobOptions.from_dict(options)
    return manifest


def _terms(reference: dict, hypothesis: str) -> dict:
    folded = unicodedata.normalize("NFC", hypothesis).casefold()
    raw = unicodedata.normalize("NFC", reference["raw_text"]).casefold()
    return {term: {"expected": raw.count(unicodedata.normalize("NFC", term).casefold()),
                   "observed": folded.count(unicodedata.normalize("NFC", term).casefold())}
            for term in reference.get("terms", []) if term}


def _numbers(reference: dict, hypothesis: str) -> dict:
    pattern = r"[$¥€£]?\d+(?:[.,]\d+)*(?:%|％)?"
    expected = Counter(re.findall(pattern, reference["raw_text"]))
    observed = Counter(re.findall(pattern, hypothesis))
    return {"expected": dict(expected), "observed": dict(observed),
            "missing": dict(expected - observed), "unexpected": dict(observed - expected),
            "annotated": {value: {"expected": reference["raw_text"].count(value),
                                   "observed": hypothesis.count(value)}
                          for value in reference.get("numbers", []) if value}}


def _utterances(reference: dict, hypothesis: dict, alignment: dict) -> list[dict]:
    raw_ref = normalize_text(reference["raw_text"])
    raw_hyp = normalize_text(hypothesis.get("text", ""))
    timing = {}
    position = 0
    for segment in hypothesis.get("segments", []):
        words = segment.get("words", [])
        segment_text = normalize_text(segment.get("text", "")) if "text" in segment else "".join(
            normalize_text(word.get("text", "")) for word in words)
        segment_start = raw_hyp.find(segment_text, position) if segment_text else -1
        if segment_start < 0:
            continue
        # Locate every segment, including gaps. A later repeated word must not
        # lend its timestamp to an earlier occurrence that has no alignment.
        position = segment_start + len(segment_text)
        if segment.get("alignment", "available") != "available":
            continue
        word_position = 0
        # Untimed gaps are never filled; only actual model word times are scored.
        for word in words:
            token = normalize_text(word.get("text", ""))
            found = segment_text.find(token, word_position) if token else -1
            if found >= 0:
                for index in range(found, found + len(token)):
                    timing[segment_start + index] = (word.get("start"), word.get("end"))
                word_position = found + len(token)
    output, position = [], 0
    deleted = set(alignment["deleted_indices"])
    for index, utterance in enumerate(reference.get("utterances", [])):
        value = normalize_text(utterance["text"])
        found = raw_ref.find(value, position)
        if found < 0:
            raise ValueError("Reference utterances must occur in raw_text in order")
        indices = range(found, found + len(value))
        missing = sum(i in deleted for i in indices)
        item = {"id": utterance.get("id", str(index)), "reference_characters": len(value),
                "deleted_characters": missing, "suspected_omitted": missing / len(value) >= 0.8,
                "suspected_repeated": raw_hyp.count(value) > raw_ref.count(value),
                "timestamp": {"status": "no_human_timestamps"}}
        if "start" in utterance:
            if reference.get("human_verified") is not True:
                item["timestamp"] = {"status": "reference_times_not_human_verified"}
                output.append(item)
                position = found + len(value)
                continue
            first = alignment["matches"].get(found)
            last = alignment["matches"].get(found + len(value) - 1)
            if first in timing and last in timing:
                start, end = timing[first][0], timing[last][1]
                if all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)) and 0 <= start < end:
                    item["timestamp"] = {"status": "matched_boundary_words",
                                         "start_error_seconds": start - utterance["start"],
                                         "end_error_seconds": end - utterance["end"]}
                else:
                    item["timestamp"] = {"status": "invalid_hypothesis_times"}
            else:
                item["timestamp"] = {"status": "unmatched_or_unaligned_boundary"}
        output.append(item)
        position = found + len(value)
    return output


def _inference_integrity(raw: dict) -> dict:
    metadata = raw.get("metadata", {})
    complete = metadata.get("integrity", {}).get("complete")
    chunks = metadata.get("chunks", [])
    if complete is False or any(chunk.get("asr_status") != "done" for chunk in chunks):
        asr = "incomplete"
    elif complete is True:
        asr = "complete"
    else:
        asr = "unknown"
    return {"asr": asr, "alignment": metadata.get("alignment", {}).get("status", "unknown")}


def score(manifest: dict, hypotheses: dict, *, base_dir: Path = Path(".")) -> dict:
    from echoscript.pipeline.normalize import normalize_transcript
    from echoscript.schema import Transcript

    cases = {case["id"]: case for case in manifest["cases"]}
    results, alignments, audio_hashes, seen = [], {}, {}, set()
    declared_policies = {policy["id"] for policy in manifest.get("policies", [])}
    for run in hypotheses.get("runs", []):
        key = (run["case_id"], run["policy_id"])
        if key in seen:
            raise ValueError(f"Duplicate hypothesis: {key}")
        seen.add(key)
        case = cases[key[0]]
        if declared_policies and key[1] not in declared_policies:
            raise ValueError(f"Unknown policy id: {key[1]}")
        if run.get("audio_sha256") and case.get("sha256") and run["audio_sha256"] != case["sha256"]:
            raise ValueError(f"Hypothesis audio checksum mismatch: {key[0]}")
        audio_hashes[key] = run.get("audio_sha256") or case.get("sha256")
        item = {"case_id": key[0], "policy_id": key[1],
                "corpus_type": case.get("corpus_type", "unspecified"),
                "languages": case.get("languages", []),
                "provenance": run.get("provenance"), "runtime": run.get("runtime")}
        if run.get("error"):
            results.append({**item, "status": "inference_failed", "error": run["error"]})
            continue
        reference = case.get("reference")
        if not reference or not (reference.get("human_verified") is True or reference.get("source_kind") == "author_subtitle"):
            results.append({**item, "status": "missing_human_reference"})
            continue
        def transcript(name):
            value = run.get(name)
            return _read(base_dir / value) if isinstance(value, str) else value
        raw = transcript("raw")
        display = transcript("display")
        if not raw or not isinstance(raw.get("text"), str):
            raise ValueError(f"{key}: raw ASR hypothesis is required")
        alignment = edit_alignment(normalize_text(reference["raw_text"]), normalize_text(raw["text"]))
        alignments[key] = alignment
        verified = reference.get("human_verified") is True
        item.update(status="scored" if verified else "scored_against_author_subtitle",
                    reference_standard="human_verified" if verified else "author_subtitle_not_verbatim_audited",
                    asr=text_metrics(reference["raw_text"], raw["text"]),
                    terms=_terms(reference, raw["text"]), numbers=_numbers(reference, raw["text"]),
                    utterances=_utterances(reference, raw, alignment),
                    inference_integrity=_inference_integrity(raw),
                    diagnostics=raw.get("metadata", {}))
        suffix = 0
        deleted = set(alignment["deleted_indices"])
        for index in range(alignment["reference_units"] - 1, -1, -1):
            if index not in deleted:
                break
            suffix += 1
        item["integrity"] = {"trailing_deleted_characters": suffix,
                             "inserted_characters": alignment["insertions"],
                             "verified_silence": verified and not normalize_text(reference["raw_text"])}
        if "display_text" in reference:
            if display is not None:
                item["final"] = text_metrics(reference["display_text"], display["text"])
                display_ref = {**reference, "raw_text": reference["display_text"]}
                item["final"]["terms"] = _terms(display_ref, display["text"])
                item["final"]["numbers"] = _numbers(display_ref, display["text"])
            if "zh_script" in run.get("options", {}):
                oracle = normalize_transcript(Transcript(text=reference["raw_text"],
                                              language=reference.get("language")), run["options"]["zh_script"])
                item["postprocessing_on_reference"] = text_metrics(reference["display_text"], oracle.text)
        results.append(item)
    complementarity, excluded_pairs = [], []
    policies = sorted({key[1] for key in alignments})
    for left, right in itertools.combinations(policies, 2):
        paired = []
        for case_id in cases:
            a, b = alignments.get((case_id, left)), alignments.get((case_id, right))
            if a is None or b is None:
                continue
            left_hash, right_hash = audio_hashes[(case_id, left)], audio_hashes[(case_id, right)]
            if left_hash and right_hash and left_hash != right_hash:
                excluded_pairs.append({"case_id": case_id, "left": left, "right": right,
                                       "reason": "audio_checksum_mismatch"})
                continue
            all_indices = set(range(a["reference_units"]))
            a_errors, b_errors = all_indices - a["matches"].keys(), all_indices - b["matches"].keys()
            paired.append({"case_id": case_id, "left_errors": a["errors"], "right_errors": b["errors"],
                           "audio_identity": "matching_checksums" if left_hash and right_hash else "not_recorded",
                           "reference_standard": "human_verified" if cases[case_id]["reference"].get("human_verified") is True else "author_subtitle_not_verbatim_audited",
                           "left_insertions": a["insertions"], "right_insertions": b["insertions"],
                           "left_only_reference_errors": len(a_errors - b_errors),
                           "right_only_reference_errors": len(b_errors - a_errors),
                           "shared_reference_errors": len(a_errors & b_errors)})
        if paired:
            complementarity.append({"left": left, "right": right, "paired_cases": paired})
    expected = {(case["id"], policy["id"]) for case in manifest["cases"] for policy in manifest.get("policies", [])}
    return {"schema_version": 1,
            "metric_policy": "NFC/casefold; CER ignores punctuation/space; WER uses whitespace (not Japanese word segmentation)",
            "results": results, "missing_runs": [list(key) for key in sorted(expected - seen)],
            "policy_processes": hypotheses.get("policy_processes", []),
            "complementarity": complementarity,
            "excluded_complementarity_pairs": excluded_pairs,
            "complementarity_policy": "Reference positions use deterministic minimum-edit paths; repeated text can have ambiguous error locations. Insertions are reported separately.",
            "automatic_cross_model_correction": {"enabled": False,
                "reason": "Paired reference errors quantify oracle opportunity, not a validated selector or correction policy"}}


def run_matrix(manifest: dict, manifest_path: Path, output_dir: Path, policy_id: str | None = None) -> dict:
    policies = [p for p in manifest.get("policies", []) if policy_id is None or p["id"] == policy_id]
    if not policies:
        raise ValueError("No matching policies in manifest")
    output_dir = output_dir.resolve()
    if len(policies) > 1:
        # Native CUDA allocations can survive model cleanup. Isolate policies so
        # a preceding backend cannot change another policy's available memory.
        hypotheses = {"schema_version": 1, "runs": [], "policy_processes": []}
        for policy in policies:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", policy["id"]):
                raise ValueError("Invalid policy id")
            child_dir = output_dir / "policies" / policy["id"]
            child_dir.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            with (child_dir / "process.log").open("w", encoding="utf-8") as log:
                child = subprocess.run(
                    [sys.executable, "-m", "echoscript.evaluation", "run", str(manifest_path.resolve()),
                     "--policy", policy["id"], "--output-dir", str(child_dir)],
                    stdout=log, stderr=subprocess.STDOUT, check=False,
                )
            hypotheses["policy_processes"].append({"policy_id": policy["id"], "returncode": child.returncode,
                "elapsed_seconds": time.perf_counter() - started, "log": str(child_dir / "process.log")})
            saved = child_dir / "hypotheses.json"
            # A failing child may have rejected an old cached configuration;
            # never present that stale file as this execution's success.
            child_runs = _read(saved).get("runs", []) if child.returncode == 0 and saved.exists() else []
            hypotheses["runs"].extend(child_runs)
            present = {run["case_id"] for run in child_runs}
            for case in manifest["cases"]:
                if case["id"] not in present:
                    hypotheses["runs"].append({"case_id": case["id"], "policy_id": policy["id"],
                        "error": {"type": "PolicyProcessFailed", "message": f"Policy process exited {child.returncode}; see {child_dir / 'process.log'}"}})
            _write(output_dir / "hypotheses.json", hypotheses)
        return hypotheses

    from echoscript.config import Settings
    from echoscript.pipeline.pipeline import TranscriptionPipeline
    from echoscript.schema import JobOptions
    from echoscript.worker.model_manager import ModelManager

    settings = Settings(data_dir=output_dir, db_path=output_dir / "jobs.sqlite3", jobs_dir=output_dir / "jobs")
    settings.ensure_directories()
    packages = {}
    for name in ("echoscript", "qwen-asr", "faster-whisper", "transformers", "torch", "opencc-python-reimplemented"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            pass
    source_root = Path(__file__).parent
    source_digest = hashlib.sha256()
    source_files = []
    for source in sorted(source_root.rglob("*.py")):
        name, content = str(source.relative_to(source_root)), source.read_bytes()
        source_digest.update(name.encode())
        source_digest.update(content)
        source_files.append((f"src/echoscript/{name}", content))
    source_archive = output_dir / f"source-{source_digest.hexdigest()}.zip"
    if not source_archive.exists():
        temporary = source_archive.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in source_files:
                archive.writestr(name, content)
        temporary.replace(source_archive)
    provenance = {"packages": packages, "python": platform.python_version(),
                  "manifest_sha256": _sha256(manifest_path), "source_sha256": source_digest.hexdigest(),
                  "source_archive": source_archive.name,
                  "model_revision": "not_resolved; use a pinned local model path for revision-controlled runs"}
    runs = []
    for policy in policies:
        for case in manifest["cases"]:
            audio = (manifest_path.parent / case["audio"]).expanduser().resolve()
            actual_hash = _sha256(audio)
            if case.get("sha256") and case["sha256"] != actual_hash:
                raise ValueError(f"Audio checksum mismatch: {case['id']}")
            options = JobOptions.from_dict(policy.get("options", {}))
            identifier = f"{case['id']}--{policy['id']}"
            fingerprint = hashlib.sha256(json.dumps([actual_hash, options.to_dict(), provenance], sort_keys=True).encode()).hexdigest()
            saved = output_dir / identifier / "hypothesis.json"
            attempts = []
            if saved.exists():
                run = _read(saved)
                if run.get("fingerprint") != fingerprint:
                    raise ValueError(f"Existing evaluation configuration changed: {saved}; use another output directory")
                if not run.get("error") and run.get("status") != "partial":
                    runs.append(run)
                    continue
                previous_runtime = run.get("runtime", {})
                attempts = previous_runtime.get("attempts", [{"elapsed_seconds": previous_runtime.get("elapsed_seconds", 0),
                                                            "status": run.get("status", "inference_failed")}])
            run = {"case_id": case["id"], "policy_id": policy["id"], "fingerprint": fingerprint,
                   "options": options.to_dict(), "audio_sha256": actual_hash, "provenance": provenance}
            models = ModelManager()
            resumed = (settings.jobs_dir / identifier / "chunks.json").is_file()
            started = time.perf_counter()
            try:
                result = TranscriptionPipeline(settings, models).run(
                    {"id": identifier, "source_type": "upload", "media_path": str(audio),
                     "source_value": audio.name, "options": options.to_dict()})
                run["raw"] = _read(settings.jobs_dir / identifier / "asr.json")
                run["display"] = _read(result)
                run["inference_integrity"] = _inference_integrity(run["raw"])
                integrity = run["inference_integrity"]
                run["status"] = "partial" if (integrity["asr"] == "incomplete" or (
                    options.timestamps and run["raw"]["text"].strip()
                    and integrity["alignment"] in {"partial", "unavailable"})) else "complete"
            except Exception as exc:
                run["error"] = {"type": type(exc).__name__, "message": str(exc)}
                run["status"] = "inference_failed"
            finally:
                elapsed = time.perf_counter() - started
                models.unload_all()
            duration = run.get("raw", {}).get("duration")
            rate = policy.get("cost_per_hour")
            if rate is not None and (not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0):
                raise ValueError("cost_per_hour must be finite and nonnegative")
            attempts.append({"elapsed_seconds": elapsed, "status": run["status"], "resumed_from_checkpoints": resumed})
            cumulative = sum(attempt["elapsed_seconds"] for attempt in attempts)
            run["runtime"] = {"elapsed_seconds": cumulative, "attempts": attempts,
                              "includes_model_load": True, "audio_seconds": duration,
                              "real_time_factor": cumulative / duration if duration else None,
                              "estimated_cost": cumulative / 3600 * rate if rate is not None else None,
                              "currency": policy.get("currency") if rate is not None else None}
            _write(saved, run)
            runs.append(run)
            _write(output_dir / "hypotheses.json", {"schema_version": 1, "runs": runs})
    hypotheses = {"schema_version": 1, "runs": runs}
    _write(output_dir / "hypotheses.json", hypotheses)
    return hypotheses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="以人工參考稿評測辨識、後處理、時間及成本")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "score", "run"):
        command = sub.add_parser(name)
        command.add_argument("manifest", type=Path)
        if name == "score":
            command.add_argument("hypotheses", type=Path)
            command.add_argument("--output", type=Path, required=True)
        if name == "run":
            command.add_argument("--output-dir", type=Path, required=True)
            command.add_argument("--policy")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if args.command == "validate":
            print(json.dumps({"cases": len(manifest["cases"]),
                              "missing_human_reference": [c["id"] for c in manifest["cases"] if not c.get("reference")],
                              "author_subtitle_not_verbatim_audited": [c["id"] for c in manifest["cases"]
                                  if c.get("reference", {}).get("source_kind") == "author_subtitle"
                                  and c["reference"].get("human_verified") is not True]}, ensure_ascii=False))
        else:
            hypotheses = (_read(args.hypotheses) if args.command == "score" else
                          run_matrix(manifest, args.manifest, args.output_dir, args.policy))
            report = score(manifest, hypotheses, base_dir=args.hypotheses.parent if args.command == "score" else args.output_dir)
            output = args.output if args.command == "score" else args.output_dir / "report.json"
            _write(output, report)
            print(str(output.resolve()))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"評測失敗：{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
