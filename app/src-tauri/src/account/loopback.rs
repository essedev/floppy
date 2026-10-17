//! The loopback the sign-in link ends on (RFC 8252, section 7.3): a listener
//! on 127.0.0.1 and a free port, alive while the request waits. The browser
//! arrives at `/callback?state=…&code=…` after the person presses the button
//! on the link's page; the `state` this app put in the redirect address keeps
//! other local processes from feeding it codes.

use crate::text;
use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, TcpListener, TcpStream};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

/// How often the listener looks at its stop flag.
const POLL: Duration = Duration::from_millis(100);
/// A browser that connects and sends nothing is dropped after this.
const READ_TIMEOUT: Duration = Duration::from_secs(2);
const MAX_REQUEST: usize = 8 * 1024;

/// A running loopback; dropping it stops the listener.
pub struct Loopback {
    redirect_uri: String,
    stop: Arc<AtomicBool>,
}

impl Loopback {
    /// Starts serving until stopped, `ttl` passes or a valid code arrives,
    /// which goes to `on_code` after the browser got its page.
    pub fn start(
        state: String,
        ttl: Duration,
        on_code: impl FnOnce(String) + Send + 'static,
    ) -> io::Result<Loopback> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let stop = Arc::new(AtomicBool::new(false));
        let flag = Arc::clone(&stop);
        let redirect_uri = format!("http://127.0.0.1:{port}/callback?state={state}");
        thread::spawn(move || serve(listener, &state, ttl, &flag, on_code));
        Ok(Loopback { redirect_uri, stop })
    }

    /// The address the server sends the browser to, with this listener's state.
    pub fn redirect_uri(&self) -> &str {
        &self.redirect_uri
    }

    /// Stops the listener; the port closes within `POLL`.
    pub fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }
}

impl Drop for Loopback {
    fn drop(&mut self) {
        self.stop();
    }
}

fn serve(
    listener: TcpListener,
    state: &str,
    ttl: Duration,
    stop: &AtomicBool,
    on_code: impl FnOnce(String),
) {
    let started = Instant::now();
    while !stop.load(Ordering::SeqCst) && started.elapsed() < ttl {
        match listener.accept() {
            Ok((stream, _)) => {
                if let Some(code) = answer(stream, state) {
                    stop.store(true, Ordering::SeqCst);
                    on_code(code);
                    return;
                }
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => thread::sleep(POLL),
            Err(_) => thread::sleep(POLL),
        }
    }
}

/// What a request asked for.
#[derive(Debug, PartialEq, Eq)]
enum Ask {
    Code(String),
    Favicon,
    Wrong,
}

fn parse(head: &str, state: &str) -> Ask {
    let Some(target) = head
        .lines()
        .next()
        .and_then(|l| l.strip_prefix("GET "))
        .and_then(|l| l.split(' ').next())
    else {
        return Ask::Wrong;
    };
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    if path == "/favicon.ico" {
        return Ask::Favicon;
    }
    if path != "/callback" {
        return Ask::Wrong;
    }
    let mut got_state = None;
    let mut code = None;
    for (k, v) in url::form_urlencoded::parse(query.as_bytes()) {
        match k.as_ref() {
            "state" => got_state = Some(v.into_owned()),
            "code" => code = Some(v.into_owned()),
            _ => {}
        }
    }
    match (got_state, code) {
        (Some(s), Some(c)) if s == state && !c.is_empty() && c.len() <= 200 => Ask::Code(c),
        _ => Ask::Wrong,
    }
}

fn read_head(stream: &mut TcpStream) -> io::Result<String> {
    stream.set_nonblocking(false)?;
    stream.set_read_timeout(Some(READ_TIMEOUT))?;
    let mut buf = Vec::with_capacity(1024);
    let mut chunk = [0u8; 1024];
    while buf.len() < MAX_REQUEST && !buf.windows(4).any(|w| w == b"\r\n\r\n") {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

fn respond(stream: &mut TcpStream, status: &str, body: &str) {
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\n\
         Cache-Control: no-store\r\nContent-Security-Policy: default-src 'none'; style-src 'unsafe-inline'\r\n\
         Connection: close\r\n\r\n",
        body.len()
    );
    // The browser may have gone already; the code, if any, is still used.
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body.as_bytes());
    let _ = stream.flush();
}

fn answer(mut stream: TcpStream, state: &str) -> Option<String> {
    let head = read_head(&mut stream).ok()?;
    match parse(&head, state) {
        Ask::Code(code) => {
            respond(&mut stream, "200 OK", &text::loopback_page(true));
            Some(code)
        }
        Ask::Favicon => {
            respond(&mut stream, "404 Not Found", "");
            None
        }
        Ask::Wrong => {
            respond(&mut stream, "400 Bad Request", &text::loopback_page(false));
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    fn get(uri: &str, path: &str) -> String {
        let port = uri
            .trim_start_matches("http://127.0.0.1:")
            .split('/')
            .next()
            .unwrap();
        let mut s = TcpStream::connect(("127.0.0.1", port.parse::<u16>().unwrap())).unwrap();
        write!(s, "GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).unwrap();
        out
    }

    #[test]
    fn parses_only_the_callback_with_its_state() {
        assert_eq!(
            parse("GET /callback?state=s1&code=abc HTTP/1.1\r\n", "s1"),
            Ask::Code("abc".into())
        );
        assert_eq!(
            parse("GET /callback?state=other&code=abc HTTP/1.1\r\n", "s1"),
            Ask::Wrong
        );
        assert_eq!(
            parse("GET /callback?state=s1 HTTP/1.1\r\n", "s1"),
            Ask::Wrong
        );
        assert_eq!(
            parse("POST /callback?state=s1&code=a HTTP/1.1", "s1"),
            Ask::Wrong
        );
        assert_eq!(parse("GET /favicon.ico HTTP/1.1", "s1"), Ask::Favicon);
    }

    #[test]
    fn hands_over_the_code_once_and_closes() {
        let (tx, rx) = mpsc::channel();
        let lb = Loopback::start("st".into(), Duration::from_secs(10), move |c| {
            tx.send(c).unwrap();
        })
        .unwrap();
        let uri = lb.redirect_uri().to_string();
        assert!(uri.starts_with("http://127.0.0.1:") && uri.ends_with("/callback?state=st"));
        assert!(get(&uri, "/callback?state=forged&code=x").starts_with("HTTP/1.1 400"));
        let ok = get(&uri, "/callback?state=st&code=the-code");
        assert!(ok.starts_with("HTTP/1.1 200"));
        assert_eq!(rx.recv_timeout(Duration::from_secs(2)).unwrap(), "the-code");
        thread::sleep(POLL * 3);
        let port: u16 = uri[17..].split('/').next().unwrap().parse().unwrap();
        assert!(TcpStream::connect(("127.0.0.1", port)).is_err());
    }

    #[test]
    fn stops_when_dropped() {
        let lb = Loopback::start("st".into(), Duration::from_secs(10), |_| {}).unwrap();
        let port: u16 = lb.redirect_uri()[17..]
            .split('/')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        drop(lb);
        thread::sleep(POLL * 3);
        assert!(TcpStream::connect(("127.0.0.1", port)).is_err());
    }
}
