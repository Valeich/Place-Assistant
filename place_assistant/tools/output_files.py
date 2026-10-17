"""read_output_file and convert_json_to_docx tools: work with files the
collection pipeline already wrote to .output/, without re-running it.

- read_output_file shows a JSON/CSV result in chat. Some environments, such
  as `adk web`, have no file browser.
- convert_json_to_docx builds a .docx report from an older JSON run, without
  spending new API calls.

Sandboxed to .output/: filenames are reduced to their basename, so
"../../etc/passwd" becomes "passwd" and simply isn't found.
"""

import json
import os

from ..config import OUTPUT_DIR
from ..pipeline import export

_OUTPUT_DIR = os.path.realpath(OUTPUT_DIR)
_MAX_CONTENT_CHARS = 30000


def _resolve_in_output_dir(
    filename: str, allowed_suffixes: tuple[str, ...]
) -> tuple[str, None] | tuple[None, dict]:
    """Returns (resolved_path, None) on success, or (None, error_dict) on
    failure — shared validation for both tools below.
    """
    base = os.path.basename(filename)
    if not base.endswith(allowed_suffixes):
        return None, {
            "status": "error",
            "error_message": f"Only {'/'.join(allowed_suffixes)} files are supported (got: {filename!r}).",
        }
    resolved = os.path.realpath(os.path.join(_OUTPUT_DIR, base))
    if not resolved.startswith(_OUTPUT_DIR + os.sep):
        return None, {"status": "error", "error_message": "That file is outside the .output/ directory."}
    if not os.path.isfile(resolved):
        return None, {
            "status": "error",
            "error_message": (
                f"No file named '{base}' in .output/. It may not have been "
                "generated yet, or the name doesn't match exactly what "
                "collect_places returned."
            ),
        }
    return resolved, None


def read_output_file(filename: str) -> dict:
    """Reads back a JSON or CSV file that area_collector or product_survey
    already wrote to the project's .output/ directory, and returns its
    content so it can be shown directly in chat. Use this whenever the user
    wants to actually see a collection or survey result — not just its path
    — or says they can't find/open the file themselves.

    Args:
        filename: The bare filename as it appears in a json_path or csv_path
            result, e.g. "jakarta_pusat_indonesia_cafe_alcohol.json".
            Any directory components are ignored — only files inside .output/
            can ever be read.

    Returns:
        dict: {"status": "success", "filename": str, "content": str,
        "truncated": bool} with the file's text (JSON pretty-printed, CSV
        as-is). truncated=True means the file was too large to include in
        full — say so rather than presenting it as complete, and point the
        user to the file on disk for the rest. Or {"status": "error",
        "error_message": str} if the file doesn't exist yet or isn't a
        .json/.csv output file — relay that message rather than guessing why.
    """
    resolved, error = _resolve_in_output_dir(filename, (".json", ".csv"))
    if error:
        return error

    with open(resolved, "r", encoding="utf-8") as f:
        raw = f.read()

    if resolved.endswith(".json"):
        try:
            raw = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
        except Exception:
            pass  # fall back to the raw text if it's somehow not valid JSON

    truncated = len(raw) > _MAX_CONTENT_CHARS
    return {
        "status": "success",
        "filename": os.path.basename(resolved),
        "content": raw[:_MAX_CONTENT_CHARS],
        "truncated": truncated,
    }


def convert_json_to_docx(filename: str) -> dict:
    """Converts an already-collected JSON output file into a .docx report
    (a readable per-place summary with a menu/price table, importable into
    Google Docs). Use this when the user wants a document-style/"Google
    Docs" output for data that already exists as JSON in .output/ — this
    reuses that file, it does NOT call collect_places again or spend any
    new API calls.

    Args:
        filename: bare filename of an existing .json file in .output/, e.g.
            "jakarta_pusat_indonesia_cafe_alcohol.json" (as returned by an
            earlier collect_places call, in json_path).

    Returns:
        dict: {"status": "success", "docx_path": str} — a local file path;
        say plainly this is a file (importable into Google Docs via File >
        Open, or by dragging it into Google Drive), not something created
        directly in the user's Google Drive. Or {"status": "error",
        "error_message": str} if the file doesn't exist, isn't valid JSON,
        or has no records to convert — relay that rather than guessing why.
    """
    resolved, error = _resolve_in_output_dir(filename, (".json",))
    if error:
        return error

    with open(resolved, "r", encoding="utf-8") as f:
        try:
            records = json.load(f)
        except Exception as exc:
            return {"status": "error", "error_message": f"'{os.path.basename(resolved)}' isn't valid JSON: {exc}"}
    base_name = os.path.basename(resolved)[: -len(".json")]
    title = base_name.replace("_", " ").title()
    if isinstance(records, dict) and "summary" in records and "places" in records:
        # A product survey or menu check from product_survey.
        docx_path = export.save_survey_docx(records, _OUTPUT_DIR, base_name, title)
    elif isinstance(records, list):
        docx_path = export.save_docx(records, output_dir=_OUTPUT_DIR, base_name=base_name, title=title)
    else:
        return {
            "status": "error",
            "error_message": f"'{os.path.basename(resolved)}' isn't a list of place records or a survey.",
        }
    if not docx_path:
        return {
            "status": "error",
            "error_message": f"'{os.path.basename(resolved)}' has no place records to convert.",
        }
    return {"status": "success", "docx_path": docx_path}
