//! PTY output pump — batches a PTY's output so one `pty_read` IPC call carries many
//! kernel reads.
//!
//! Why: a macOS PTY read returns at most 1024 bytes and, for line-by-line output,
//! usually a few bytes (measured 2026-10-23, `seq 1 300000`: 125,056 reads for 2.3 MB,
//! median 8 bytes; a 64 KB buffer instead of 4 KB changed nothing: 121,702 reads).
//! When every kernel read was one `pty_read`, that was one webview IPC round trip,
//! one `spawn_blocking` and one `xterm.write` per few bytes.
//!
//! How: one reader thread per PTY reads continuously into a bounded buffer.
//! [`OutputPump::take`] blocks until there is output, lingers up to `linger` for more
//! (so a burst arrives as one chunk; a keystroke echo waits at most `linger`), and
//! drains up to `max` bytes. The reader stops at [`HIGH_WATER`] buffered bytes until
//! the frontend drains it, so a fast producer is still back-pressured by the PTY,
//! as before. Std only (no tauri types) so it is unit-tested on its own.
//!
//! Public: [`OutputPump`], [`HIGH_WATER`].

use std::io::Read;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

/// The reader thread waits while this many bytes are buffered and not yet taken.
pub const HIGH_WATER: usize = 1 << 20;

/// errno EIO (5 on macOS and Linux): a PTY master's read once the slave closed.
const EIO: i32 = 5;

struct State {
    buf: Vec<u8>,
    /// Set once the reader stopped: `None` = clean EOF, `Some(e)` = read error.
    end: Option<Option<String>>,
}

/// The buffered output of one PTY, filled by its reader thread.
pub struct OutputPump {
    state: Mutex<State>,
    /// Signalled on new output, on end, and on every take (for the reader's wait).
    cv: Condvar,
}

impl OutputPump {
    /// Start the reader thread for `reader` and return the pump it fills. The thread
    /// ends at EOF or a read error (the PTY closed: child exited or session killed).
    pub fn start(mut reader: Box<dyn Read + Send>) -> Arc<OutputPump> {
        let pump = Arc::new(OutputPump {
            state: Mutex::new(State { buf: Vec::new(), end: None }),
            cv: Condvar::new(),
        });
        let p = Arc::clone(&pump);
        std::thread::Builder::new()
            .name("pty-reader".into())
            .spawn(move || {
                let mut chunk = vec![0u8; 64 * 1024];
                loop {
                    let r = reader.read(&mut chunk);
                    let mut st = p.state.lock().unwrap_or_else(|e| e.into_inner());
                    match r {
                        Ok(0) => st.end = Some(None),
                        Ok(n) => st.buf.extend_from_slice(&chunk[..n]),
                        Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                        // EIO is how a PTY master reports "the slave side closed"
                        // (the child exited): a normal end, not a failure.
                        Err(e) if e.raw_os_error() == Some(EIO) => st.end = Some(None),
                        Err(e) => st.end = Some(Some(e.to_string())),
                    }
                    let done = st.end.is_some();
                    p.cv.notify_all();
                    if done {
                        return;
                    }
                    while st.buf.len() >= HIGH_WATER && st.end.is_none() {
                        st = p.cv.wait(st).unwrap_or_else(|e| e.into_inner());
                    }
                }
            })
            .expect("spawn pty reader thread");
        pump
    }

    /// Block until output is available, linger up to `linger` (or until `max` bytes
    /// are buffered), then return up to `max` bytes. `Ok(empty)` = clean EOF;
    /// `Err(e)` = the read error, both only after all output was taken.
    pub fn take(&self, max: usize, linger: Duration) -> Result<Vec<u8>, String> {
        let mut st = self.state.lock().unwrap_or_else(|e| e.into_inner());
        while st.buf.is_empty() && st.end.is_none() {
            st = self.cv.wait(st).unwrap_or_else(|e| e.into_inner());
        }
        if st.buf.is_empty() {
            return match &st.end {
                Some(Some(e)) => Err(e.clone()),
                _ => Ok(Vec::new()),
            };
        }
        let deadline = Instant::now() + linger;
        while st.buf.len() < max && st.end.is_none() {
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            st = self.cv.wait_timeout(st, deadline - now).unwrap_or_else(|e| e.into_inner()).0;
        }
        let n = st.buf.len().min(max);
        let out: Vec<u8> = st.buf.drain(..n).collect();
        self.cv.notify_all(); // a reader parked at HIGH_WATER may continue
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    //! The pump batches a burst into one take, returns an echo promptly, ends with
    //! EOF only after all output was taken, and back-pressures at HIGH_WATER.
    use super::*;
    use std::io;
    use std::sync::mpsc;

    /// A reader fed by a channel: each message is one kernel read; a dropped
    /// sender is EOF.
    struct ChanReader(mpsc::Receiver<Vec<u8>>);
    impl Read for ChanReader {
        fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
            match self.0.recv() {
                Ok(b) => {
                    out[..b.len()].copy_from_slice(&b);
                    Ok(b.len())
                }
                Err(_) => Ok(0),
            }
        }
    }

    #[test]
    fn a_burst_of_small_reads_is_one_take() {
        let (tx, rx) = mpsc::channel();
        let pump = OutputPump::start(Box::new(ChanReader(rx)));
        for _ in 0..1000 {
            tx.send(b"12345678".to_vec()).unwrap();
        }
        let got = pump.take(1 << 20, Duration::from_millis(50)).unwrap();
        assert_eq!(got.len(), 8000, "1000 kernel reads arrive as ONE chunk");
        drop(tx);
        assert_eq!(pump.take(1 << 20, Duration::from_millis(1)).unwrap(), Vec::<u8>::new());
    }

    #[test]
    fn an_echo_waits_at_most_the_linger() {
        let (tx, rx) = mpsc::channel();
        let pump = OutputPump::start(Box::new(ChanReader(rx)));
        tx.send(b"a".to_vec()).unwrap();
        let t = Instant::now();
        assert_eq!(pump.take(1024, Duration::from_millis(20)).unwrap(), b"a");
        let waited = t.elapsed();
        assert!(waited < Duration::from_millis(500), "waited {waited:?}");
        drop(tx);
    }

    #[test]
    fn take_respects_max_and_eof_comes_after_all_output() {
        let (tx, rx) = mpsc::channel();
        let pump = OutputPump::start(Box::new(ChanReader(rx)));
        tx.send(vec![7u8; 3000]).unwrap();
        drop(tx);
        std::thread::sleep(Duration::from_millis(50));
        assert_eq!(pump.take(1000, Duration::ZERO).unwrap().len(), 1000);
        assert_eq!(pump.take(5000, Duration::ZERO).unwrap().len(), 2000);
        assert!(pump.take(5000, Duration::ZERO).unwrap().is_empty(), "then EOF");
    }

    #[test]
    fn eio_from_a_closed_pty_is_a_clean_eof() {
        struct Eio(bool);
        impl Read for Eio {
            fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
                if self.0 {
                    return Err(io::Error::from_raw_os_error(EIO));
                }
                self.0 = true;
                out[..3].copy_from_slice(b"bye");
                Ok(3)
            }
        }
        let pump = OutputPump::start(Box::new(Eio(false)));
        assert_eq!(pump.take(10, Duration::from_millis(20)).unwrap(), b"bye");
        assert!(pump.take(10, Duration::ZERO).unwrap().is_empty(), "EIO = EOF, not an error");
    }

    #[test]
    fn a_read_error_is_reported_after_the_output() {
        struct Once(bool);
        impl Read for Once {
            fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
                if self.0 {
                    return Err(io::Error::new(io::ErrorKind::Other, "boom"));
                }
                self.0 = true;
                out[..2].copy_from_slice(b"hi");
                Ok(2)
            }
        }
        let pump = OutputPump::start(Box::new(Once(false)));
        assert_eq!(pump.take(10, Duration::from_millis(20)).unwrap(), b"hi");
        assert_eq!(pump.take(10, Duration::ZERO).unwrap_err(), "boom");
    }

    #[test]
    fn the_reader_stops_at_high_water_until_drained() {
        let (tx, rx) = mpsc::sync_channel::<Vec<u8>>(0);
        let pump = OutputPump::start(Box::new(ChanReader(rx)));
        let chunk = vec![1u8; 64 * 1024];
        for _ in 0..(HIGH_WATER / chunk.len()) {
            tx.send(chunk.clone()).unwrap();
        }
        // The reader is now parked at HIGH_WATER: one more send cannot be received.
        std::thread::sleep(Duration::from_millis(50));
        assert!(tx.try_send(chunk.clone()).is_err(), "reader must not read past HIGH_WATER");
        let got = pump.take(HIGH_WATER, Duration::ZERO).unwrap();
        assert_eq!(got.len(), HIGH_WATER);
        tx.send(chunk.clone()).unwrap(); // drained: it reads again
        drop(tx);
    }
}
