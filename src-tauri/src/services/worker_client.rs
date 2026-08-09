//! Minimal, dependency-free HTTP client for the localhost worker API (TASK-006).
//!
//! The Python worker is a loopback-only sidecar; the Rust core only needs a
//! small number of authenticated `GET` calls (the `/health` readiness probe) on
//! plain `127.0.0.1` HTTP. `reqwest`/`ureq` would drag in a TLS stack we do not
//! need, so this module speaks HTTP/1.1 directly over `std::net::TcpStream`.
//!
//! Security contract: responses are size-capped, timeouts are enforced, and the
//! bearer token is only ever sent in the `Authorization` header — never logged.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use serde::Deserialize;

/// Loopback address the worker must bind (`127.0.0.1`, never the LAN).
pub const HOST_LOOPBACK: [u8; 4] = [127, 0, 0, 1];

const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);
const READ_TIMEOUT: Duration = Duration::from_secs(3);
const WRITE_TIMEOUT: Duration = Duration::from_secs(2);
/// Upper bound for response headers (plenty for a FastAPI health response).
const MAX_HEADER_BYTES: usize = 16 * 1024;
/// Upper bound for a control-plane response body.
const MAX_BODY_BYTES: usize = 1024 * 1024;

/// Response of the worker's `GET /health` endpoint (see worker schemas).
#[derive(Debug, Clone, Deserialize, PartialEq)]
pub struct HealthResponse {
    pub status: String,
    pub version: String,
    #[serde(default)]
    pub gpu: Option<serde_json::Value>,
}

/// Errors from talking to the worker over HTTP.
#[derive(Debug, Clone, PartialEq)]
pub enum HttpError {
    /// TCP connect failed (expected while the worker is still starting).
    ConnectFailed(String),
    WriteFailed(String),
    ReadFailed(String),
    /// Peer closed the connection before the response completed.
    ConnectionClosed,
    MalformedResponse(String),
    /// A non-2xx status code was received.
    Status(u16),
}

impl std::fmt::Display for HttpError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            HttpError::ConnectFailed(e) => write!(f, "connection failed: {e}"),
            HttpError::WriteFailed(e) => write!(f, "write failed: {e}"),
            HttpError::ReadFailed(e) => write!(f, "read failed: {e}"),
            HttpError::ConnectionClosed => write!(f, "connection closed before response completed"),
            HttpError::MalformedResponse(e) => write!(f, "malformed response: {e}"),
            HttpError::Status(code) => write!(f, "HTTP status {code}"),
        }
    }
}

impl std::error::Error for HttpError {}

/// Authenticated client for a specific worker session (loopback port + token).
///
/// The token is owned by the Rust core and never serialized or logged; it is
/// used only in the `Authorization: Bearer` header.
#[derive(Debug, Clone)]
pub struct WorkerClient {
    port: u16,
    token: String,
}

impl WorkerClient {
    pub fn new(port: u16, token: String) -> Self {
        Self { port, token }
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn addr(&self) -> SocketAddr {
        SocketAddr::from((HOST_LOOPBACK, self.port))
    }

    /// Probe `GET /health` with the session token.
    pub fn check_health(&self) -> Result<HealthResponse, HttpError> {
        let (status, body) = http_get(
            self.addr(),
            "/health",
            &[("Authorization", format!("Bearer {}", self.token))],
        )?;
        if status != 200 {
            return Err(HttpError::Status(status));
        }
        serde_json::from_slice(&body)
            .map_err(|e| HttpError::MalformedResponse(format!("invalid health body: {e}")))
    }
}

/// A tiny HTTP/1.1 GET used only for localhost control-plane calls.
fn http_get(
    addr: SocketAddr,
    path: &str,
    headers: &[(&str, String)],
) -> Result<(u16, Vec<u8>), HttpError> {
    let mut stream = TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT)
        .map_err(|e| HttpError::ConnectFailed(e.to_string()))?;
    stream
        .set_read_timeout(Some(READ_TIMEOUT))
        .map_err(|e| HttpError::ReadFailed(e.to_string()))?;
    stream
        .set_write_timeout(Some(WRITE_TIMEOUT))
        .map_err(|e| HttpError::WriteFailed(e.to_string()))?;

    let mut request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n",
        addr.port()
    );
    for (name, value) in headers {
        request.push_str(&format!("{name}: {value}\r\n"));
    }
    request.push_str("\r\n");
    stream
        .write_all(request.as_bytes())
        .map_err(|e| HttpError::WriteFailed(e.to_string()))?;

    read_response(&mut stream)
}

/// Read and parse a complete HTTP/1.1 response body.
fn read_response(stream: &mut TcpStream) -> Result<(u16, Vec<u8>), HttpError> {
    let raw = read_through_headers(stream)?;
    let header_end = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map(|p| p + 4)
        .unwrap_or(raw.len());
    let (header_block, body_prefix) = raw.split_at(header_end);
    let header_block = String::from_utf8_lossy(header_block);

    let status = parse_status_line(header_block.lines().next().unwrap_or_default())?;

    let content_length = header_block.lines().find_map(|line| {
        let mut parts = line.splitn(2, ':');
        let name = parts.next()?.trim();
        if name.eq_ignore_ascii_case("content-length") {
            parts.next()?.trim().parse::<usize>().ok()
        } else {
            None
        }
    });

    let mut body = body_prefix.to_vec();
    match content_length {
        Some(len) => {
            if len > MAX_BODY_BYTES {
                return Err(HttpError::MalformedResponse(
                    "response body too large".into(),
                ));
            }
            while body.len() < len {
                let mut chunk = [0u8; 4096];
                let n = stream
                    .read(&mut chunk)
                    .map_err(|e| HttpError::ReadFailed(e.to_string()))?;
                if n == 0 {
                    return Err(HttpError::ConnectionClosed);
                }
                body.extend_from_slice(&chunk[..n]);
            }
            if body.len() > len {
                body.truncate(len);
            }
        }
        None => {
            // No Content-Length: read until EOF (Connection: close).
            let mut chunk = [0u8; 4096];
            loop {
                match stream.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(n) => {
                        body.extend_from_slice(&chunk[..n]);
                        if body.len() > MAX_BODY_BYTES {
                            return Err(HttpError::MalformedResponse(
                                "response body too large".into(),
                            ));
                        }
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::TimedOut => break,
                    Err(e) => return Err(HttpError::ReadFailed(e.to_string())),
                }
            }
        }
    }
    Ok((status, body))
}

/// Read bytes up to and including the end of the header block (`\r\n\r\n`).
///
/// Extra bytes read beyond the header terminator are preserved (they may be the
/// start of the body) — the caller splits them apart.
fn read_through_headers(stream: &mut TcpStream) -> Result<Vec<u8>, HttpError> {
    let mut buf = Vec::with_capacity(512);
    let mut chunk = [0u8; 1024];
    loop {
        if buf.windows(4).any(|w| w == b"\r\n\r\n") {
            return Ok(buf);
        }
        if buf.len() > MAX_HEADER_BYTES {
            return Err(HttpError::MalformedResponse(
                "response headers too large".into(),
            ));
        }
        let n = stream
            .read(&mut chunk)
            .map_err(|e| HttpError::ReadFailed(e.to_string()))?;
        if n == 0 {
            return Err(HttpError::ConnectionClosed);
        }
        buf.extend_from_slice(&chunk[..n]);
    }
}

/// Parse the status code from a status line such as `HTTP/1.1 200 OK`.
fn parse_status_line(line: &str) -> Result<u16, HttpError> {
    let code = line
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse::<u16>().ok())
        .ok_or_else(|| HttpError::MalformedResponse(format!("bad status line: {line:?}")))?;
    Ok(code)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufReader, Read, Write};
    use std::net::{Ipv4Addr, TcpListener};
    use std::thread;

    /// Spawn a one-shot TCP server that lets `handler` answer the request.
    ///
    /// Drains the full request headers first so the socket receive buffer is
    /// empty when the handler closes the connection — otherwise Windows
    /// loopback can surface RST/read errors instead of a clean EOF.
    fn serve(handler: impl FnOnce(&mut TcpStream) + Send + 'static) -> u16 {
        let listener =
            TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), 0)).expect("bind test listener");
        let port = listener.local_addr().expect("local addr").port();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let mut reader = BufReader::new(stream.try_clone().expect("clone"));
            let mut drained = Vec::new();
            let mut chunk = [0u8; 1024];
            loop {
                if drained.windows(4).any(|w| w == b"\r\n\r\n") {
                    break;
                }
                let n = reader.read(&mut chunk).expect("read request");
                if n == 0 {
                    break;
                }
                drained.extend_from_slice(&chunk[..n]);
            }
            handler(&mut stream);
        });
        port
    }

    fn client_on(port: u16) -> WorkerClient {
        WorkerClient::new(port, "test-token".to_string())
    }

    #[test]
    fn http_get_returns_status_and_body() {
        let port = serve(|stream| {
            let response = "HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello";
            let _ = stream.write_all(response.as_bytes());
        });
        let (status, body) = http_get(SocketAddr::from((HOST_LOOPBACK, port)), "/health", &[])
            .expect("request succeeds");
        assert_eq!(status, 200);
        assert_eq!(body, b"hello");
    }

    #[test]
    fn http_get_handles_body_split_across_header_read() {
        let port = serve(|stream| {
            let response = "HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\nhello world";
            let _ = stream.write_all(response.as_bytes());
        });
        let (status, body) = http_get(SocketAddr::from((HOST_LOOPBACK, port)), "/health", &[])
            .expect("request succeeds");
        assert_eq!(status, 200);
        assert_eq!(body, b"hello world");
    }

    #[test]
    fn check_health_parses_worker_response() {
        let port = serve(|stream| {
            let body = r#"{"status":"ok","version":"0.1.0","gpu":null}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes());
        });
        let health = client_on(port).check_health().expect("health succeeds");
        assert_eq!(health.status, "ok");
        assert_eq!(health.version, "0.1.0");
        assert_eq!(health.gpu, None);
    }

    #[test]
    fn check_health_surfaces_non_200() {
        let port = serve(|stream| {
            let response = "HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n";
            let _ = stream.write_all(response.as_bytes());
        });
        let err = client_on(port).check_health().expect_err("401 is an error");
        assert_eq!(err, HttpError::Status(401));
    }

    #[test]
    fn check_health_rejects_connection_refused() {
        // Nothing listening on this port.
        let listener = TcpListener::bind((Ipv4Addr::from(HOST_LOOPBACK), 0)).expect("bind");
        let port = listener.local_addr().expect("local addr").port();
        drop(listener);
        let err = client_on(port).check_health().expect_err("must fail");
        assert!(matches!(err, HttpError::ConnectFailed(_)));
    }

    #[test]
    fn check_health_rejects_premature_close() {
        let port = serve(|stream| {
            let _ = stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Le");
        });
        let err = client_on(port).check_health().expect_err("must fail");
        assert!(matches!(err, HttpError::ConnectionClosed));
    }

    #[test]
    fn check_health_rejects_bad_status_line() {
        let port = serve(|stream| {
            let _ = stream.write_all(b"BOGUS\r\nContent-Length: 0\r\n\r\n");
        });
        let err = client_on(port).check_health().expect_err("must fail");
        assert!(matches!(err, HttpError::MalformedResponse(_)));
    }
}
