//! serveros-eventd — the device half of event push (requirement D-4).
//!
//! Once a second it asks this machine's CloudCLI gateway (loopback only) what
//! moved since the last cursor — CloudCLI's own `changes` tool — and appends
//! each run start / run end / waiting-for-a-person event to a bounded JSONL
//! spool. The node connector serves that spool to the Tokyo hub over a signed
//! long-poll (`GET /v1/events`), so the hub hears of a finished run within
//! seconds instead of on its next 30-second poll.
//!
//! What it never does: reach anything but loopback, print or log the CloudCLI
//! credential, carry message text (CloudCLI's `changes` summaries are ids and
//! transitions only, and only ids are kept here), or grow without bound.
//!
//! Files (all owner-only on Unix):
//!   <spool>                 one JSON event per line, `seq` strictly increasing
//!   <spool>.state.json      cursor + last seq, so a restart resumes
//!   <spool>.status.json     liveness: last poll, last error code, counters

// No console window on Windows: it runs as a per-user task at logon. Output
// still reaches a caller that redirects it (`--version`, `--once`).
#![cfg_attr(windows, windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::collections::{HashSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const VERSION: &str = env!("CARGO_PKG_VERSION");
const SPOOL_MAX_BYTES: u64 = 4 * 1024 * 1024;
const SPOOL_KEEP_LINES: usize = 4000;
const SEEN_MAX: usize = 4000;
const FIRST_LOOKBACK_SECS: u64 = 600;
const MAX_BODY: usize = 4 * 1024 * 1024;

struct Args {
    endpoint: String,
    credential: Option<PathBuf>,
    spool: PathBuf,
    interval_ms: u64,
    once: bool,
}

fn usage() -> ! {
    eprintln!(
        "serveros-eventd {VERSION}\n\
         usage: serveros-eventd --endpoint http://127.0.0.1:<port>/mcp --credential-file <path> \
         --spool <path> [--interval-ms 1000] [--once]\n       serveros-eventd --version"
    );
    std::process::exit(2)
}

fn parse_args() -> Args {
    let mut it = std::env::args().skip(1);
    let (mut endpoint, mut credential, mut spool, mut interval_ms, mut once) =
        (None, None, None, 1000u64, false);
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--version" => {
                println!("serveros-eventd {VERSION}");
                std::process::exit(0)
            }
            "--endpoint" => endpoint = it.next(),
            "--credential-file" => credential = it.next().map(PathBuf::from),
            "--spool" => spool = it.next().map(PathBuf::from),
            "--interval-ms" => {
                interval_ms = it.next().and_then(|v| v.parse().ok()).unwrap_or_else(|| usage())
            }
            "--once" => once = true,
            _ => usage(),
        }
    }
    Args {
        endpoint: endpoint.unwrap_or_else(|| usage()),
        credential,
        spool: spool.unwrap_or_else(|| usage()),
        interval_ms: interval_ms.clamp(200, 60_000),
        once,
    }
}

// ---------------------------------------------------------------- time ----

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

/// RFC 3339 UTC from epoch seconds (no dependency on a time crate).
fn iso(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // civil_from_days (Howard Hinnant)
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let mo = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if mo <= 2 { y + 1 } else { y };
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

// ------------------------------------------------------------ http/mcp ----

#[derive(Debug)]
struct Fault {
    code: &'static str,
}

fn fault(code: &'static str) -> Fault {
    Fault { code }
}

struct Endpoint {
    host: String,
    port: u16,
    path: String,
}

/// Loopback only: this process must never become a way to reach elsewhere.
fn parse_endpoint(url: &str) -> Result<Endpoint, Fault> {
    let rest = url.strip_prefix("http://").ok_or(fault("ENDPOINT_NOT_LOOPBACK_HTTP"))?;
    let (hostport, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let (host, port) = match hostport.rsplit_once(':') {
        Some((h, p)) => (h, p.parse::<u16>().map_err(|_| fault("ENDPOINT_BAD_PORT"))?),
        None => (hostport, 80),
    };
    let host = host.trim_start_matches('[').trim_end_matches(']');
    if !matches!(host, "127.0.0.1" | "localhost" | "::1") {
        return Err(fault("ENDPOINT_NOT_LOOPBACK_HTTP"));
    }
    Ok(Endpoint { host: host.to_string(), port, path: path.to_string() })
}

struct Response {
    status: u16,
    session_id: Option<String>,
    content_type: String,
    body: String,
}

fn dechunk(raw: &[u8]) -> Result<Vec<u8>, Fault> {
    let mut out = Vec::new();
    let mut i = 0;
    loop {
        let line_end = raw[i..].windows(2).position(|w| w == b"\r\n").ok_or(fault("BAD_CHUNK"))? + i;
        let size_text = std::str::from_utf8(&raw[i..line_end]).map_err(|_| fault("BAD_CHUNK"))?;
        let size = usize::from_str_radix(size_text.split(';').next().unwrap_or("").trim(), 16)
            .map_err(|_| fault("BAD_CHUNK"))?;
        i = line_end + 2;
        if size == 0 {
            return Ok(out);
        }
        if i + size > raw.len() || out.len() + size > MAX_BODY {
            return Err(fault("BAD_CHUNK"));
        }
        out.extend_from_slice(&raw[i..i + size]);
        i += size + 2;
    }
}

fn post(ep: &Endpoint, token: Option<&str>, session: Option<&str>, body: &str) -> Result<Response, Fault> {
    let addr = (ep.host.as_str(), ep.port)
        .to_socket_addrs()
        .map_err(|_| fault("UNREACHABLE"))?
        .next()
        .ok_or(fault("UNREACHABLE"))?;
    let mut stream =
        TcpStream::connect_timeout(&addr, Duration::from_secs(3)).map_err(|_| fault("UNREACHABLE"))?;
    stream.set_read_timeout(Some(Duration::from_secs(20))).ok();
    stream.set_write_timeout(Some(Duration::from_secs(10))).ok();
    let mut head = format!(
        "POST {} HTTP/1.1\r\nHost: {}:{}\r\nContent-Type: application/json\r\n\
         Accept: application/json, text/event-stream\r\nMCP-Protocol-Version: 2024-11-05\r\n\
         User-Agent: serveros-eventd/{VERSION}\r\nConnection: close\r\nContent-Length: {}\r\n",
        ep.path,
        ep.host,
        ep.port,
        body.len()
    );
    if let Some(t) = token {
        head.push_str(&format!("Authorization: Bearer {t}\r\n"));
    }
    if let Some(s) = session {
        head.push_str(&format!("Mcp-Session-Id: {s}\r\n"));
    }
    head.push_str("\r\n");
    stream.write_all(head.as_bytes()).map_err(|_| fault("UNREACHABLE"))?;
    stream.write_all(body.as_bytes()).map_err(|_| fault("UNREACHABLE"))?;
    let mut raw = Vec::new();
    stream
        .take((MAX_BODY + 65_536) as u64)
        .read_to_end(&mut raw)
        .map_err(|_| fault("TIMEOUT"))?;
    let split = raw.windows(4).position(|w| w == b"\r\n\r\n").ok_or(fault("BAD_RESPONSE"))?;
    let header_text = String::from_utf8_lossy(&raw[..split]).to_string();
    let mut payload = raw[split + 4..].to_vec();
    let mut lines = header_text.split("\r\n");
    let status = lines
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|s| s.parse::<u16>().ok())
        .ok_or(fault("BAD_RESPONSE"))?;
    let (mut session_id, mut content_type, mut chunked) = (None, String::new(), false);
    for line in lines {
        if let Some((k, v)) = line.split_once(':') {
            let (k, v) = (k.trim().to_ascii_lowercase(), v.trim().to_string());
            match k.as_str() {
                "mcp-session-id" => session_id = Some(v),
                "content-type" => content_type = v.to_ascii_lowercase(),
                "transfer-encoding" if v.to_ascii_lowercase().contains("chunked") => chunked = true,
                _ => {}
            }
        }
    }
    if chunked {
        payload = dechunk(&payload)?;
    }
    Ok(Response { status, session_id, content_type, body: String::from_utf8_lossy(&payload).to_string() })
}

/// One JSON-RPC reply from a plain-JSON or SSE body.
fn decode(resp: &Response) -> Result<Value, Fault> {
    if resp.status == 401 || resp.status == 403 {
        return Err(fault("UNAUTHORIZED"));
    }
    if resp.status >= 400 {
        return Err(fault("HTTP_ERROR"));
    }
    let text = resp.body.trim_start();
    if resp.content_type.contains("text/event-stream") || text.starts_with("event:") || text.starts_with("data:") {
        for line in resp.body.lines() {
            if let Some(chunk) = line.strip_prefix("data:") {
                if let Ok(v) = serde_json::from_str::<Value>(chunk.trim()) {
                    return Ok(v);
                }
            }
        }
        return Err(fault("BAD_RESPONSE"));
    }
    if text.is_empty() {
        return Ok(Value::Null);
    }
    serde_json::from_str(text).map_err(|_| fault("BAD_RESPONSE"))
}

struct Mcp {
    ep: Endpoint,
    credential: Option<PathBuf>,
    session: Option<String>,
    next_id: u64,
}

impl Mcp {
    fn token(&self) -> Option<String> {
        self.credential
            .as_ref()
            .and_then(|p| fs::read_to_string(p).ok())
            .map(|t| t.trim().to_string())
            .filter(|t| !t.is_empty())
    }

    fn rpc(&mut self, method: &str, params: Value, notify: bool) -> Result<Value, Fault> {
        let mut msg = json!({"jsonrpc": "2.0", "method": method, "params": params});
        if !notify {
            self.next_id += 1;
            msg["id"] = json!(self.next_id);
        }
        let token = self.token();
        let resp = post(&self.ep, token.as_deref(), self.session.as_deref(), &msg.to_string())?;
        if let Some(sid) = &resp.session_id {
            self.session = Some(sid.clone());
        }
        if notify {
            return Ok(Value::Null);
        }
        let reply = decode(&resp)?;
        if reply.get("error").is_some() {
            return Err(fault("RPC_ERROR"));
        }
        Ok(reply.get("result").cloned().unwrap_or(Value::Null))
    }

    fn ensure(&mut self) -> Result<(), Fault> {
        if self.session.is_some() {
            return Ok(());
        }
        self.rpc(
            "initialize",
            json!({"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "serveros-eventd", "version": VERSION}}),
            false,
        )?;
        if self.session.is_none() {
            // A stateless gateway: keep going without a session id.
            self.session = Some(String::new());
        }
        self.rpc("notifications/initialized", json!({}), true)?;
        Ok(())
    }

    fn changes(&mut self, cursor: Option<&str>, since: Option<&str>) -> Result<Value, Fault> {
        self.ensure()?;
        let mut args = json!({"kinds": ["run", "interaction"], "limit": 200});
        if let Some(c) = cursor {
            args["cursor"] = json!(c);
        } else if let Some(s) = since {
            args["since"] = json!(s);
        }
        let result = self.rpc("tools/call", json!({"name": "changes", "arguments": args}), false)?;
        if result.get("isError") == Some(&Value::Bool(true)) {
            let text = result.pointer("/content/0/text").and_then(Value::as_str).unwrap_or("");
            return Err(if text.contains("INVALID_ARGUMENT") { fault("CURSOR_REFUSED") } else { fault("TOOL_ERROR") });
        }
        if let Some(s) = result.get("structuredContent") {
            return Ok(s.clone());
        }
        let text = result.pointer("/content/0/text").and_then(Value::as_str).unwrap_or("");
        serde_json::from_str(text).map_err(|_| fault("BAD_RESPONSE"))
    }
}

// ------------------------------------------------------------- events ----

/// The device event this pusher emits for one CloudCLI `changes` event, or
/// None. Ids and states only: CloudCLI's summaries are already body-free and
/// only the run status word is taken from them.
fn normalize(event: &Value) -> Option<Value> {
    let kind = event.get("kind")?.as_str()?;
    let at = event.pointer("/at/iso").and_then(Value::as_str).unwrap_or("");
    let session = event.pointer("/ref/sessionId").and_then(Value::as_str);
    let run = event.pointer("/ref/runId").and_then(Value::as_str);
    let summary = event.get("summary").and_then(Value::as_str).unwrap_or("");
    let safe = |s: &str| s.chars().filter(|c| c.is_ascii_alphanumeric() || "-_.:".contains(*c)).take(120).collect::<String>();
    match kind {
        "run" => {
            let (session, run) = (session?, run?);
            let (name, status) = if let Some(i) = summary.find(" ended (") {
                let tail = &summary[i + 8..];
                ("run.ended", tail.split(')').next().map(safe))
            } else if summary.contains(" started") {
                ("run.started", None)
            } else {
                ("run.changed", None)
            };
            Some(json!({"kind": name, "session": safe(session), "run": safe(run),
                        "status": status, "at": safe(at)}))
        }
        "interaction" => Some(json!({"kind": "interaction.waiting", "session": session.map(safe),
                                     "run": run.map(safe), "at": safe(at)})),
        _ => None,
    }
}

struct Spool {
    path: PathBuf,
    seq: u64,
    seen: VecDeque<String>,
    seen_set: HashSet<String>,
}

fn private_create(path: &Path, append: bool) -> std::io::Result<File> {
    let mut opts = OpenOptions::new();
    opts.create(true).write(true);
    if append {
        opts.append(true);
    } else {
        opts.truncate(true);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    opts.open(path)
}

fn write_atomic(path: &Path, text: &str) -> std::io::Result<()> {
    let tmp = path.with_extension(format!("tmp{}", std::process::id()));
    {
        let mut f = private_create(&tmp, false)?;
        f.write_all(text.as_bytes())?;
        f.sync_all().ok();
    }
    fs::rename(&tmp, path)
}

impl Spool {
    fn open(path: PathBuf, seq: u64) -> Spool {
        let mut spool = Spool { path, seq, seen: VecDeque::new(), seen_set: HashSet::new() };
        // Recover the last seq and the dedupe window from the file itself.
        if let Ok(f) = File::open(&spool.path) {
            for line in BufReader::new(f).lines().map_while(Result::ok) {
                if let Ok(v) = serde_json::from_str::<Value>(&line) {
                    spool.seq = spool.seq.max(v.get("seq").and_then(Value::as_u64).unwrap_or(0));
                    spool.remember(Self::key(&v));
                }
            }
        }
        spool
    }

    fn key(v: &Value) -> String {
        format!("{}|{}|{}|{}", v["kind"], v["session"], v["run"], v["at"])
    }

    fn remember(&mut self, key: String) -> bool {
        if !self.seen_set.insert(key.clone()) {
            return false;
        }
        self.seen.push_back(key);
        while self.seen.len() > SEEN_MAX {
            if let Some(old) = self.seen.pop_front() {
                self.seen_set.remove(&old);
            }
        }
        true
    }

    fn append(&mut self, mut event: Value) -> std::io::Result<bool> {
        if !self.remember(Self::key(&event)) {
            return Ok(false);
        }
        self.seq += 1;
        event["seq"] = json!(self.seq);
        event["observed_at"] = json!(iso(now_secs()));
        let mut f = private_create(&self.path, true)?;
        writeln!(f, "{event}")?;
        Ok(true)
    }

    /// Keep the spool bounded: past the size cap keep only the newest lines.
    fn trim(&self) -> std::io::Result<()> {
        let size = fs::metadata(&self.path).map(|m| m.len()).unwrap_or(0);
        if size <= SPOOL_MAX_BYTES {
            return Ok(());
        }
        let lines: Vec<String> = BufReader::new(File::open(&self.path)?).lines().map_while(Result::ok).collect();
        let keep = &lines[lines.len().saturating_sub(SPOOL_KEEP_LINES)..];
        write_atomic(&self.path, &(keep.join("\n") + "\n"))
    }
}

fn sidecar(spool: &Path, suffix: &str) -> PathBuf {
    let mut name = spool.file_name().map(|n| n.to_os_string()).unwrap_or_default();
    name.push(suffix);
    spool.with_file_name(name)
}

fn main() {
    let args = parse_args();
    let ep = match parse_endpoint(&args.endpoint) {
        Ok(ep) => ep,
        Err(f) => {
            eprintln!("refused: {}", f.code);
            std::process::exit(2)
        }
    };
    if let Some(dir) = args.spool.parent() {
        fs::create_dir_all(dir).ok();
    }
    let state_path = sidecar(&args.spool, ".state.json");
    let status_path = sidecar(&args.spool, ".status.json");
    let state: Value = fs::read_to_string(&state_path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(json!({}));
    let mut cursor = state.get("cursor").and_then(Value::as_str).map(String::from);
    let mut spool = Spool::open(args.spool.clone(), state.get("seq").and_then(Value::as_u64).unwrap_or(0));
    let mut mcp = Mcp { ep, credential: args.credential.clone(), session: None, next_id: 0 };
    let (mut polls, mut emitted, mut failures) = (0u64, 0u64, 0u32);
    let mut last_ok: Option<u64> = None;
    let mut last_error: Option<&'static str>;
    let started = now_secs();
    loop {
        let t0 = Instant::now();
        polls += 1;
        let since = iso(now_secs().saturating_sub(FIRST_LOOKBACK_SECS));
        match mcp.changes(cursor.as_deref(), Some(&since)) {
            Ok(page) => {
                failures = 0;
                last_error = None;
                last_ok = Some(now_secs());
                for event in page.get("events").and_then(Value::as_array).cloned().unwrap_or_default() {
                    if let Some(norm) = normalize(&event) {
                        match spool.append(norm) {
                            Ok(true) => emitted += 1,
                            Ok(false) => {}
                            Err(_) => last_error = Some("SPOOL_WRITE"),
                        }
                    }
                }
                if let Some(c) = page.get("cursor").and_then(Value::as_str) {
                    cursor = Some(c.to_string());
                }
                spool.trim().ok();
                write_atomic(&state_path, &json!({"cursor": cursor, "seq": spool.seq}).to_string()).ok();
            }
            Err(f) => {
                failures += 1;
                last_error = Some(f.code);
                mcp.session = None; // re-initialise on the next poll
                if f.code == "CURSOR_REFUSED" {
                    cursor = None;
                }
            }
        }
        let status = json!({
            "schema": "serveros/eventd-status.v1", "version": VERSION, "pid": std::process::id(),
            "started_at": iso(started), "last_poll_at": iso(now_secs()),
            "last_ok_at": last_ok.map(iso), "ok": last_error.is_none(), "error": last_error,
            "polls": polls, "events": emitted, "seq": spool.seq, "interval_ms": args.interval_ms,
        });
        write_atomic(&status_path, &status.to_string()).ok();
        if args.once {
            if let Some(code) = last_error {
                eprintln!("poll failed: {code}");
                std::process::exit(1)
            }
            println!("{status}");
            return;
        }
        // Back off on repeated failure (1x … 30x the interval, capped at 30 s).
        let wait = Duration::from_millis(
            (args.interval_ms * u64::from(failures.min(30)).max(1)).min(30_000),
        );
        if let Some(rest) = wait.checked_sub(t0.elapsed()) {
            std::thread::sleep(rest);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_loopback_endpoints() {
        assert!(parse_endpoint("http://127.0.0.1:3001/mcp").is_ok());
        assert!(parse_endpoint("http://localhost:3001/mcp").is_ok());
        assert!(parse_endpoint("http://10.0.0.5:3001/mcp").is_err());
        assert!(parse_endpoint("https://127.0.0.1:3001/mcp").is_err());
    }

    #[test]
    fn iso_is_utc() {
        assert_eq!(iso(0), "1970-01-01T00:00:00Z");
        assert_eq!(iso(1_795_000_000), "2026-11-18T11:06:40Z");
    }

    #[test]
    fn run_events_keep_ids_and_status_only() {
        let ended = json!({"kind": "run", "at": {"iso": "2026-11-19T01:02:03.000Z"},
            "ref": {"runId": "r-1", "sessionId": "s-1"},
            "summary": "Run r-1 ended (completed) in session s-1."});
        let n = normalize(&ended).unwrap();
        assert_eq!(n["kind"], "run.ended");
        assert_eq!(n["status"], "completed");
        assert_eq!(n["run"], "r-1");
        let started = json!({"kind": "run", "at": {"iso": "x"}, "ref": {"runId": "r-2", "sessionId": "s"},
            "summary": "Run r-2 started in session s."});
        assert_eq!(normalize(&started).unwrap()["kind"], "run.started");
        assert!(normalize(&json!({"kind": "quay_task"})).is_none());
    }

    #[test]
    fn chunked_bodies_decode() {
        assert_eq!(dechunk(b"3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n").unwrap(), b"abcde");
    }

    #[test]
    fn spool_dedupes_and_counts() {
        let dir = std::env::temp_dir().join(format!("eventd-test-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("events.jsonl");
        let _ = fs::remove_file(&path);
        let mut spool = Spool::open(path.clone(), 0);
        let e = json!({"kind": "run.ended", "session": "s", "run": "r", "at": "t", "status": "completed"});
        assert!(spool.append(e.clone()).unwrap());
        assert!(!spool.append(e).unwrap());
        let reopened = Spool::open(path.clone(), 0);
        assert_eq!(reopened.seq, 1);
        fs::remove_dir_all(&dir).ok();
    }
}
