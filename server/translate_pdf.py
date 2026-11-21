"""Turn any PDF into a DOCX with CloudConvert, then translate its phrases.

The PDF path is the only input. Nothing in here is tied to a sample file.

    python server/translate_pdf.py path/to/file.pdf
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.cloudconvert.com/v2"


def load_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def call(method: str, url: str, key: str, payload: dict | None = None, timeout: int = 180) -> dict:
    data = None
    headers = {"Authorization": f"Bearer {key}"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise SystemExit(f"CloudConvert HTTP {error.code}: {detail}") from None
    return json.loads(body) if body else {}


def upload(form: dict, pdf: Path) -> None:
    boundary = "----nespatchpdf"
    parts: list[bytes] = []
    for name, value in form["parameters"].items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{pdf.name}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode()
    )
    parts.append(pdf.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        form["url"],
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise SystemExit(f"CloudConvert upload HTTP {error.code}: {detail}") from None


def docx_destination(pdf: Path) -> Path:
    direct = pdf.with_suffix(".docx")
    if direct.exists():
        return pdf.with_name(f"{pdf.stem}.cc.docx")
    return direct


def cloudconvert_docx(pdf: Path, docx: Path, key: str) -> None:
    created = call(
        "POST",
        f"{API}/jobs",
        key,
        {
            "tasks": {
                "import-file": {"operation": "import/upload"},
                "convert-file": {
                    "operation": "convert",
                    "input": "import-file",
                    "input_format": "pdf",
                    "output_format": "docx",
                },
                "export-file": {"operation": "export/url", "input": "convert-file"},
            }
        },
    )
    tasks = {task["name"]: task for task in created["data"]["tasks"]}
    upload(tasks["import-file"]["result"]["form"], pdf)
    job_id = created["data"]["id"]
    print("cloudconvert started", flush=True)
    for _ in range(240):
        job = call("GET", f"{API}/jobs/{job_id}", key)
        status = job["data"]["status"]
        print(f"cloudconvert {status}", flush=True)
        if status == "finished":
            export = next(task for task in job["data"]["tasks"] if task["name"] == "export-file")
            url = export["result"]["files"][0]["url"]
            urllib.request.urlretrieve(url, docx)
            print(f"docx: {docx}", flush=True)
            return
        if status == "error":
            messages = [
                task.get("message") or task["name"]
                for task in job["data"]["tasks"]
                if task.get("status") == "error"
            ]
            raise SystemExit("CloudConvert failed: " + "; ".join(messages))
        time.sleep(5)
    raise SystemExit("CloudConvert timed out.")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python server/translate_pdf.py path/to/file.pdf")
    load_env()
    key = os.environ.get("CLOUDCONVERT_API_KEY", "")
    if not key:
        sys.exit("Set CLOUDCONVERT_API_KEY in the environment.")
    pdf = Path(sys.argv[1])
    if pdf.suffix.lower() != ".pdf" or not pdf.is_file():
        sys.exit("Pass an existing PDF file.")
    docx = docx_destination(pdf)
    cloudconvert_docx(pdf, docx, key)
    from translate_docx_phrases import translate_docx

    translate_docx(docx)


if __name__ == "__main__":
    main()
