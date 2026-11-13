//! Prototype: the same kernel reads as serveros-node-resource-snapshot
//! (two samples), std only, one JSON line. For a like-for-like benchmark.
use std::fmt::Write as _;
use std::{env, fs, thread, time::Duration};

#[repr(C)]
struct StatVfs { bsize: u64, frsize: u64, blocks: u64, bfree: u64, bavail: u64,
                 files: u64, ffree: u64, favail: u64, fsid: u64, flag: u64, namemax: u64, spare: [i32; 6] }
extern "C" { fn statvfs(path: *const i8, buf: *mut StatVfs) -> i32; }

fn read(p: &str) -> Option<String> { fs::read_to_string(p).ok() }

fn kv(text: &str, key: &str) -> Option<u64> {
    text.lines().find(|l| l.starts_with(key))
        .and_then(|l| l.split_whitespace().nth(1)).and_then(|v| v.parse().ok())
}

fn cpu_total(stat: &str) -> Option<(u64, u64)> {
    let l = stat.lines().next()?;
    let v: Vec<u64> = l.split_whitespace().skip(1).filter_map(|x| x.parse().ok()).collect();
    if v.len() < 4 { return None; }
    let total: u64 = v.iter().sum();
    let idle = v[3] + v.get(4).copied().unwrap_or(0);
    Some((total, idle))
}

struct Sample { stat: Option<String>, mem: Option<String>, vm: Option<String>,
                psi: Vec<(String, Option<String>)>, disk: Option<String>, net: Option<String>,
                up: Option<String> }

fn sample() -> Sample {
    Sample { stat: read("/proc/stat"), mem: read("/proc/meminfo"), vm: read("/proc/vmstat"),
             psi: ["cpu", "memory", "io"].iter()
                 .map(|r| (r.to_string(), read(&format!("/proc/pressure/{r}")))).collect(),
             disk: read("/proc/diskstats"), net: read("/proc/net/dev"), up: read("/proc/uptime") }
}

fn main() {
    let mut interval = 500u64;
    let mut fsys: Vec<(String, String)> = vec![];
    let args: Vec<String> = env::args().collect();
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--interval-ms" => { interval = args[i + 1].parse().unwrap_or(500); i += 1; }
            "--filesystem" => { let (r, p) = args[i + 1].split_once('=').unwrap(); fsys.push((r.into(), p.into())); i += 1; }
            _ => {}
        }
        i += 1;
    }
    if fsys.is_empty() { fsys.push(("ROOT_FS".into(), "/".into())); }
    let a = sample();
    if interval > 0 { thread::sleep(Duration::from_millis(interval)); }
    let b = sample();
    let boot = read("/proc/sys/kernel/random/boot_id").unwrap_or_default();
    let mut o = String::with_capacity(4096);
    o.push_str("{\"schema\":\"proto/core-lite-proto\"");
    let _ = write!(o, ",\"boot_id\":\"{}\"", boot.trim());
    if let (Some(x), Some(y)) = (a.stat.as_deref().and_then(cpu_total), b.stat.as_deref().and_then(cpu_total)) {
        let dt = y.0.saturating_sub(x.0);
        if dt > 0 { let _ = write!(o, ",\"cpu_used_percent\":{:.2}", 100.0 * (1.0 - (y.1 - x.1) as f64 / dt as f64)); }
        else { o.push_str(",\"cpu_used_percent\":null"); }
    }
    if let Some(m) = b.mem.as_deref() {
        let _ = write!(o, ",\"mem_total_kb\":{},\"mem_available_kb\":{},\"swap_total_kb\":{},\"swap_free_kb\":{}",
                       kv(m, "MemTotal:").unwrap_or(0), kv(m, "MemAvailable:").unwrap_or(0),
                       kv(m, "SwapTotal:").unwrap_or(0), kv(m, "SwapFree:").unwrap_or(0));
    }
    if let (Some(x), Some(y)) = (a.vm.as_deref(), b.vm.as_deref()) {
        let d = |k: &str| kv(y, k).unwrap_or(0).saturating_sub(kv(x, k).unwrap_or(0));
        let _ = write!(o, ",\"pswpin\":{},\"pswpout\":{}", d("pswpin "), d("pswpout "));
    }
    for (r, t) in &b.psi {
        if let Some(t) = t {
            for line in t.lines() {
                let mut it = line.split_whitespace();
                let kind = it.next().unwrap_or("");
                if let Some(a60) = it.nth(1).and_then(|s| s.strip_prefix("avg60=")) {
                    let _ = write!(o, ",\"psi_{r}_{kind}_avg60\":{a60}");
                }
            }
        }
    }
    let _ = write!(o, ",\"disks\":{}", b.disk.as_deref().map(|d| d.lines().count()).unwrap_or(0));
    let _ = write!(o, ",\"ifaces\":{}", b.net.as_deref().map(|d| d.lines().count().saturating_sub(2)).unwrap_or(0));
    let _ = write!(o, ",\"uptime\":\"{}\"", b.up.as_deref().unwrap_or("").split_whitespace().next().unwrap_or(""));
    o.push_str(",\"filesystems\":[");
    for (n, (role, path)) in fsys.iter().enumerate() {
        let mut s: StatVfs = unsafe { std::mem::zeroed() };
        let c = std::ffi::CString::new(path.as_str()).unwrap();
        let ok = unsafe { statvfs(c.as_ptr(), &mut s) } == 0;
        if n > 0 { o.push(','); }
        if ok { let _ = write!(o, "{{\"role\":\"{role}\",\"total_bytes\":{},\"available_bytes\":{}}}", s.blocks * s.frsize, s.bavail * s.frsize); }
        else { let _ = write!(o, "{{\"role\":\"{role}\",\"state\":\"UNAVAILABLE\"}}"); }
    }
    o.push_str("]}");
    println!("{o}");
}
