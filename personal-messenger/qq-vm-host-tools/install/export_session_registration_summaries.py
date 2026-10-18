"""Export privacy-minimal registration evidence summaries from the QQ guest."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

ROOT = Path(r"C:\Users\xiayu\Documents\Codex\2026-09-09\new-chat")
CONTROL = ROOT / r"outputs\qq-vm\install\control_qq_runtime_host.py"
DIAG = ROOT / r"outputs\qq-vm\install\diagnostics"


def load_control():
    spec = importlib.util.spec_from_file_location("pmai_registration_export", CONTROL)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONTROL_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    run_id = uuid.uuid4().hex
    report = {"schema": "pmai-session-registration-summary-v1", "succeeded": False, "items": []}
    guest = host = None
    clear = bytearray()
    try:
        control = load_control()
        _vbox, host, guest, clear, _password = control._open_guest()
        if control._runtime_status(guest, "registration-export-" + run_id).get("runtime_process_alive") is not False:
            raise RuntimeError("RUNTIME_NOT_STOPPED")
        for index in (1, 2, 3):
            suffix = "" if index == 1 else f"-{index}"
            guest_path = rf"C:\PMAI\data\runtime\qq-default-account\registered-session-scope{suffix}.json"
            local = DIAG / f".registration-{index}-{run_id}.json"
            control._wait_progress(guest.FileCopyFromGuest(guest_path, str(local), []), 30_000)
            value = json.loads(local.read_text(encoding="utf-8-sig"))
            evidence = value.get("session_evidence", {})
            signature = str(value.get("participant_signature", ""))
            stable = str(value.get("stable_participant_signature", ""))
            adoption = value.get("initial_adoption")
            adoption_signature = (
                str(adoption.get("participant_signature", ""))
                if isinstance(adoption, dict)
                else ""
            )
            migration = value.get("session_identity_migration")
            report["items"].append({
                "index": index,
                "binding_id": value.get("binding_id"),
                "participant_signature_sha256": sha(signature),
                "stable_signature_present": bool(stable),
                "stable_participant_signature_sha256": sha(stable) if stable else None,
                "header_digest": evidence.get("header_digest"),
                "process_id": evidence.get("process_id"),
                "window_handle": evidence.get("window_handle"),
                "process_started_at_100ns": evidence.get("process_started_at_100ns"),
                "selected_row_runtime_id_hash": evidence.get("selected_row_runtime_id_hash"),
                "client_version": evidence.get("client_version"),
                "selector_pack_version": evidence.get("selector_pack_version"),
                "initial_adoption": {
                    "present": isinstance(adoption, dict),
                    "keys": sorted(adoption) if isinstance(adoption, dict) else [],
                    "binding_id": adoption.get("binding_id") if isinstance(adoption, dict) else None,
                    "last_direction": adoption.get("last_direction") if isinstance(adoption, dict) else None,
                    "bubble_count": adoption.get("bubble_count") if isinstance(adoption, dict) else None,
                    "last_ordinal": adoption.get("last_ordinal") if isinstance(adoption, dict) else None,
                    "participant_signature_sha256": (
                        sha(adoption_signature) if adoption_signature else None
                    ),
                },
                "session_identity_migration": {
                    "present": isinstance(migration, dict),
                    "previous_evidence_hash": (
                        migration.get("previous_evidence_hash")
                        if isinstance(migration, dict)
                        else None
                    ),
                    "current_evidence_hash": (
                        migration.get("current_evidence_hash")
                        if isinstance(migration, dict)
                        else None
                    ),
                },
            })
            local.unlink(missing_ok=True)
        report["succeeded"] = True
        return 0
    except Exception as exc:
        report["error_code"] = str(exc)[:128]
        return 2
    finally:
        DIAG.mkdir(parents=True, exist_ok=True)
        (DIAG / f"session-registration-summary-{run_id}.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
        for i in range(len(clear)):
            clear[i] = 0
        if guest is not None:
            try: guest.Close()
            except Exception: pass
        if host is not None:
            try: host.UnlockMachine()
            except Exception: pass

if __name__ == "__main__":
    raise SystemExit(main())
