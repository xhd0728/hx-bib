use anyhow::Result;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    fs,
    path::PathBuf,
    sync::Mutex,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct HttpResponse {
    pub status: Option<u16>,
    pub body: String,
    pub error: Option<String>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Cached {
    status: u16,
    body: String,
    fetched_at: f64,
}
pub struct Net {
    client: Client,
    delay: f64,
    use_cache: bool,
    cache_path: PathBuf,
    cache: Mutex<HashMap<String, Cached>>,
    throttle: Mutex<HashMap<String, Instant>>,
    penalties: Mutex<HashMap<String, f64>>,
    #[cfg(test)]
    fixtures: Option<Mutex<HashMap<String, Vec<HttpResponse>>>>,
}

pub fn cache_directory() -> PathBuf {
    if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(std::env::temp_dir)
            .join("hx-bib")
    } else {
        std::env::var_os("XDG_CACHE_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".cache")
            })
            .join("hx-bib")
    }
}
fn timestamp() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
impl Net {
    pub fn new(delay: f64, use_cache: bool) -> Result<Self> {
        let path = cache_directory().join("rust-http-cache.json");
        let cache = if use_cache {
            fs::read_to_string(&path)
                .ok()
                .and_then(|s| serde_json::from_str(&s).ok())
                .unwrap_or_default()
        } else {
            HashMap::new()
        };
        Ok(Self {
            client: Client::builder()
                .user_agent("hx-bib/0.1 (mailto:noreply@example.com)")
                .timeout(Duration::from_secs(30))
                .build()?,
            delay: delay.max(0.0),
            use_cache,
            cache_path: path,
            cache: Mutex::new(cache),
            throttle: Mutex::new(HashMap::new()),
            penalties: Mutex::new(HashMap::new()),
            #[cfg(test)]
            fixtures: None,
        })
    }
    pub fn get(&self, url: &str, accept: &str, retries: usize, timeout: u64) -> HttpResponse {
        #[cfg(test)]
        if let Some(fixtures) = &self.fixtures {
            let mut responses = fixtures.lock().unwrap();
            let key = responses
                .keys()
                .filter(|prefix| url.starts_with(prefix.as_str()))
                .max_by_key(|prefix| prefix.len())
                .cloned()
                .unwrap_or_else(|| panic!("Unmocked request {url}"));
            let sequence = responses.get_mut(&key).unwrap();
            return if sequence.len() > 1 {
                sequence.remove(0)
            } else {
                sequence[0].clone()
            };
        }
        let key = format!("{accept}|{url}");
        if self.use_cache {
            if let Some(c) = self.cache.lock().unwrap().get(&key) {
                let ttl = if c.status == 200 {
                    86400.0
                } else if c.status == 404 || c.status == 410 {
                    3600.0
                } else {
                    0.0
                };
                let age = timestamp() - c.fetched_at;
                if age >= 0.0 && age < ttl {
                    log::debug!("GET {} {url} (cache hit)", c.status);
                    return HttpResponse {
                        status: Some(c.status),
                        body: c.body.clone(),
                        error: None,
                    };
                }
            }
        }
        let host = url::Url::parse(url)
            .ok()
            .and_then(|u| u.host_str().map(str::to_owned))
            .unwrap_or_default();
        let mut result = HttpResponse {
            status: None,
            body: String::new(),
            error: None,
        };
        for attempt in 0..retries.max(1) {
            let base: f64 = match host.as_str() {
                "export.arxiv.org" => 3.1,
                "arxiv.org" => 1.0,
                "dblp.org" => 1.6,
                "api.crossref.org" | "api2.openreview.net" => 0.5,
                _ => 0.0,
            };
            let mut last = self.throttle.lock().unwrap();
            let global = last
                .get("")
                .map(|v| v.elapsed().as_secs_f64())
                .unwrap_or(f64::INFINITY);
            let elapsed = last
                .get(&host)
                .map(|v| v.elapsed().as_secs_f64())
                .unwrap_or(f64::INFINITY);
            let penalty = *self.penalties.lock().unwrap().get(&host).unwrap_or(&0.0);
            let wait = (self.delay - global).max(base + penalty - elapsed);
            if wait > 0.0 {
                std::thread::sleep(Duration::from_secs_f64(wait));
            }
            last.insert("".into(), Instant::now());
            last.insert(host.clone(), Instant::now());
            drop(last);
            let start = Instant::now();
            log::debug!("GET {url} attempt {}/{}", attempt + 1, retries);
            match self
                .client
                .get(url)
                .header("User-Agent", if accept.is_empty() && host != "export.arxiv.org" {
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                } else { "hx-bib/0.1 (mailto:noreply@example.com)" })
                .header("Accept", accept)
                .timeout(Duration::from_secs(timeout))
                .send()
            {
                Ok(response) => {
                    let status = response.status().as_u16();
                    match response.text() {
                        Ok(body) => {
                            result = HttpResponse {
                                status: Some(status),
                                body,
                                error: None,
                            }
                        }
                        Err(error) => {
                            result = HttpResponse {
                                status: None,
                                body: String::new(),
                                error: Some(error.to_string()),
                            }
                        }
                    }
                    if status == 429 {
                        let mut penalties = self.penalties.lock().unwrap();
                        let p = penalties.entry(host.clone()).or_insert(0.0);
                        *p = (*p + 2.0).min(10.0);
                    }
                    log::debug!("GET {status} {url} {:.2}s", start.elapsed().as_secs_f64());
                    if [429, 500, 502, 503, 504].contains(&status) && attempt + 1 < retries {
                        std::thread::sleep(Duration::from_secs(2 * (attempt as u64 + 1)));
                        continue;
                    }
                    break;
                }
                Err(error) => {
                    result = HttpResponse {
                        status: None,
                        body: String::new(),
                        error: Some(error.to_string()),
                    };
                    log::debug!("GET {url} failed: {error:?}");
                    if attempt + 1 < retries {
                        std::thread::sleep(Duration::from_secs(attempt as u64 + 1));
                    }
                }
            }
        }
        if self.use_cache {
            if let Some(status) = result.status.filter(|s| [200, 404, 410].contains(s)) {
                self.cache.lock().unwrap().insert(
                    key,
                    Cached {
                        status,
                        body: result.body.clone(),
                        fetched_at: timestamp(),
                    },
                );
            }
        }
        result
    }
    pub fn save(&self) -> Result<()> {
        if !self.use_cache {
            return Ok(());
        }
        fs::create_dir_all(self.cache_path.parent().unwrap())?;
        let temp = self
            .cache_path
            .with_extension(format!("{}.tmp", std::process::id()));
        fs::write(&temp, serde_json::to_vec(&*self.cache.lock().unwrap())?)?;
        fs::rename(temp, &self.cache_path)?;
        Ok(())
    }
    #[cfg(test)]
    pub(crate) fn fixture(responses: HashMap<String, Vec<HttpResponse>>) -> Self {
        let mut net = Self::new(0.0, false).unwrap();
        net.fixtures = Some(Mutex::new(responses));
        net
    }
}
impl HttpResponse {
    pub fn failure(&self) -> String {
        self.error.clone().unwrap_or_else(|| match self.status {
            None => "connection failed or timed out".into(),
            Some(200) => "empty response".into(),
            Some(status) => format!("HTTP {status}"),
        })
    }
}
