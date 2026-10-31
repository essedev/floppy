//! Keeps this Mac awake while data moves to or from a disk: a file this Mac
//! is writing (a copy, an export, its upload) or a "Make available". Idle
//! sleep would stop the VM halfway through an upload and drop its connection
//! to the disk's metadata, which puts the disk in read-only mode until it
//! comes back. `caffeinate -i` holds the assertion, waiting on the app's pid
//! so it never outlives the app, and is released `LINGER` after the last
//! transfer. A closed lid still sleeps: that is the user's choice. No
//! administrator rights are needed.

use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// How long the Mac stays awake after the last transfer: JuiceFS may still be
/// sending the last blocks, and a copy often follows another.
pub const LINGER: Duration = Duration::from_secs(120);

type Spawn = Box<dyn Fn() -> std::io::Result<Child> + Send + Sync>;

/// The sleep assertion while transfers run.
pub struct Awake {
    spawn: Spawn,
    inner: Mutex<Inner>,
}

#[derive(Default)]
struct Inner {
    child: Option<Child>,
    last_busy: Option<Instant>,
}

fn caffeinate() -> std::io::Result<Child> {
    Command::new("/usr/bin/caffeinate")
        .args(["-i", "-w", &std::process::id().to_string()])
        .spawn()
}

impl Awake {
    /// The assertion through `/usr/bin/caffeinate`.
    pub fn new() -> Self {
        Self::with(Box::new(caffeinate))
    }

    /// The assertion through `spawn`, for tests.
    pub fn with(spawn: Spawn) -> Self {
        Awake {
            spawn,
            inner: Mutex::new(Inner::default()),
        }
    }

    /// Whether data moves now: holds the assertion, or releases it once
    /// nothing has moved for `LINGER`.
    pub fn update(&self, busy: bool) {
        self.update_at(busy, Instant::now());
    }

    fn update_at(&self, busy: bool, now: Instant) {
        let mut inner = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        // A caffeinate that ended by itself (killed from outside) is started again.
        if let Some(child) = inner.child.as_mut()
            && !matches!(child.try_wait(), Ok(None))
        {
            inner.child = None;
        }
        if busy {
            inner.last_busy = Some(now);
            if inner.child.is_none() {
                inner.child = (self.spawn)().ok();
            }
        } else if inner
            .last_busy
            .is_none_or(|t| now.saturating_duration_since(t) >= LINGER)
            && let Some(mut child) = inner.child.take()
        {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    /// Whether the assertion is held.
    #[cfg(test)]
    fn held(&self) -> bool {
        self.inner
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .child
            .is_some()
    }
}

impl Default for Awake {
    fn default() -> Self {
        Self::new()
    }
}

impl Drop for Awake {
    fn drop(&mut self) {
        let inner = self.inner.get_mut().unwrap_or_else(|e| e.into_inner());
        if let Some(mut child) = inner.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn counted() -> (Awake, Arc<AtomicUsize>) {
        let spawned = Arc::new(AtomicUsize::new(0));
        let count = spawned.clone();
        let awake = Awake::with(Box::new(move || {
            count.fetch_add(1, Ordering::SeqCst);
            Command::new("/bin/sleep").arg("60").spawn()
        }));
        (awake, spawned)
    }

    #[test]
    fn held_while_busy_and_released_after_the_linger() {
        let (awake, spawned) = counted();
        let t0 = Instant::now();
        awake.update_at(false, t0);
        assert!(!awake.held());
        awake.update_at(true, t0);
        awake.update_at(true, t0 + Duration::from_secs(5));
        assert!(awake.held());
        assert_eq!(
            spawned.load(Ordering::SeqCst),
            1,
            "one caffeinate for a run of transfers"
        );
        awake.update_at(false, t0 + Duration::from_secs(60));
        assert!(awake.held(), "still held within the linger");
        awake.update_at(false, t0 + Duration::from_secs(5) + LINGER);
        assert!(!awake.held());
        awake.update_at(true, t0 + Duration::from_secs(300));
        assert!(awake.held());
        assert_eq!(spawned.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn a_caffeinate_killed_from_outside_is_started_again() {
        let (awake, spawned) = counted();
        let t0 = Instant::now();
        awake.update_at(true, t0);
        {
            let mut inner = awake.inner.lock().unwrap();
            let child = inner.child.as_mut().unwrap();
            child.kill().unwrap();
            child.wait().unwrap();
        }
        awake.update_at(true, t0 + Duration::from_secs(5));
        assert!(awake.held());
        assert_eq!(spawned.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn a_failed_spawn_is_retried_while_busy() {
        let awake = Awake::with(Box::new(|| Err(std::io::Error::other("no caffeinate"))));
        awake.update(true);
        assert!(!awake.held());
    }
}
