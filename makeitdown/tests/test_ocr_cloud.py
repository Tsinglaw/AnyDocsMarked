import json
import makeitdown.ocr_cloud as oc
from makeitdown.models import ConversionResult


class _Resp:
    def __init__(self, status=200, payload=None, text="", content=b""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"status {self.status_code}")


def test_cloud_convert_happy_path(tmp_path, monkeypatch):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")

    # Sequence: POST job -> GET job (running) -> GET job (done) -> GET jsonl
    states = iter([
        _Resp(payload={"data": {"state": "running",
                                "extractProgress": {"totalPages": 1, "extractedPages": 0}}}),
        _Resp(payload={"data": {"state": "done",
                                "extractProgress": {"extractedPages": 1,
                                                    "startTime": "t0", "endTime": "t1"},
                                "resultUrl": {"jsonUrl": "https://x/result.jsonl"}}}),
    ])
    jsonl_line = json.dumps({"result": {"layoutParsingResults": [
        {"markdown": {"text": "# Page 1\n\n| a | b |\n|---|---|", "images": {}}}
    ]}})

    def fake_post(url, **kw):
        return _Resp(payload={"data": {"jobId": "job-123"}})

    def fake_get(url, **kw):
        if url.endswith("result.jsonl"):
            return _Resp(text=jsonl_line)
        return next(states)

    monkeypatch.setattr(oc.requests, "post", fake_post)
    monkeypatch.setattr(oc.requests, "get", fake_get)

    client = oc.CloudOCR(token="TKN", poll_interval=0)
    result = client.convert(f)
    assert isinstance(result, ConversionResult)
    assert "# Page 1" in result.text
    assert result.engine == "cloud:paddleocr-vl-1.6"
    assert result.pages == 1


def test_cloud_requests_pass_timeout(tmp_path, monkeypatch):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    timeouts = []
    jsonl = json.dumps({"result": {"layoutParsingResults": [
        {"markdown": {"text": "# ok", "images": {}}}]}})

    def fake_post(url, **kw):
        timeouts.append(kw.get("timeout"))
        return _Resp(payload={"data": {"jobId": "j"}})

    def fake_get(url, **kw):
        timeouts.append(kw.get("timeout"))
        if url.endswith("result.jsonl"):
            return _Resp(text=jsonl)
        return _Resp(payload={"data": {"state": "done",
                                       "resultUrl": {"jsonUrl": "https://x/result.jsonl"}}})

    monkeypatch.setattr(oc.requests, "post", fake_post)
    monkeypatch.setattr(oc.requests, "get", fake_get)
    oc.CloudOCR(token="T", poll_interval=0).convert(f)
    assert timeouts and all(t is not None for t in timeouts)


def test_cloud_poll_times_out(tmp_path, monkeypatch):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(oc.requests, "post",
                        lambda url, **kw: _Resp(payload={"data": {"jobId": "j"}}))
    monkeypatch.setattr(oc.requests, "get",
                        lambda url, **kw: _Resp(payload={"data": {"state": "running"}}))
    clock = iter([0.0, 10.0, 20.0, 30.0])
    monkeypatch.setattr(oc.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)

    client = oc.CloudOCR(token="T", poll_interval=0, max_poll_seconds=15)
    try:
        client.convert(f)
        assert False, "expected timeout RuntimeError"
    except RuntimeError as e:
        assert "timed out" in str(e).lower()


def test_cloud_convert_raises_on_poll_http_error(tmp_path, monkeypatch):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(oc.requests, "post",
                        lambda url, **kw: _Resp(payload={"data": {"jobId": "j"}}))
    monkeypatch.setattr(oc.requests, "get",
                        lambda url, **kw: _Resp(status=500, text="server error"))
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)  # skip retry backoff
    client = oc.CloudOCR(token="TKN", poll_interval=0)
    try:
        client.convert(f)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "job poll failed" in str(e)


def test_cloud_convert_raises_on_failed_job(tmp_path, monkeypatch):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4")

    monkeypatch.setattr(oc.requests, "post",
                        lambda url, **kw: _Resp(payload={"data": {"jobId": "j"}}))
    monkeypatch.setattr(oc.requests, "get",
                        lambda url, **kw: _Resp(payload={"data": {"state": "failed",
                                                                  "errorMsg": "boom"}}))
    client = oc.CloudOCR(token="TKN", poll_interval=0)
    try:
        client.convert(f)
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "boom" in str(e)


def test_get_retries_transient_500_then_succeeds(monkeypatch):
    # First two attempts hit a transient 5xx; the third succeeds. The caller
    # sees only the final good response — retries are invisible.
    seq = iter([_Resp(status=502), _Resp(status=500), _Resp(payload={"ok": True})])
    calls = []
    monkeypatch.setattr(oc.requests, "get", lambda url, **kw: calls.append(url) or next(seq))
    slept = []
    monkeypatch.setattr(oc.time, "sleep", lambda s: slept.append(s))
    resp = oc.CloudOCR(token="T")._get("https://x/job")
    assert resp.status_code == 200 and len(calls) == 3
    assert slept == [2.0, 4.0]  # exponential backoff between attempts


def test_get_retries_connection_error_then_succeeds(monkeypatch):
    # oc.requests stays the real module (only .get is patched), so its
    # exception types are usable directly.
    conn_error = oc.requests.ConnectionError
    state = {"n": 0}

    def flaky(url, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise conn_error("blip")
        return _Resp(payload={"ok": True})

    monkeypatch.setattr(oc.requests, "get", flaky)
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)
    assert oc.CloudOCR(token="T")._get("https://x/job").status_code == 200


def test_get_does_not_retry_4xx(monkeypatch):
    # A bad token / vanished job is not transient: exactly one attempt.
    calls = []
    monkeypatch.setattr(oc.requests, "get",
                        lambda url, **kw: calls.append(url) or _Resp(status=401))
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)
    assert oc.CloudOCR(token="T")._get("https://x/job").status_code == 401
    assert len(calls) == 1


def test_get_lasting_failure_surfaces_final_outcome(monkeypatch):
    conn_error = oc.requests.ConnectionError
    # Lasting 5xx: the final response is returned for the caller's own handling.
    monkeypatch.setattr(oc.requests, "get", lambda url, **kw: _Resp(status=503))
    monkeypatch.setattr(oc.time, "sleep", lambda s: None)
    assert oc.CloudOCR(token="T")._get("https://x/j").status_code == 503

    # Lasting network error: the final exception propagates.
    def always_down(url, **kw):
        raise conn_error("down")
    monkeypatch.setattr(oc.requests, "get", always_down)
    try:
        oc.CloudOCR(token="T")._get("https://x/j")
        assert False, "expected ConnectionError"
    except conn_error:
        pass
