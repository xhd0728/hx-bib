# hx-bib Rust

Download the Windows x64 installer or Apple Silicon DMG from [Releases](https://github.com/xhd0728/hx-bib/releases). The CLI archives contain `hx-bib-rs`. Both run without Python.

```bash
hx-bib-rs format refs.bib --indent 2 --dry-run
hx-bib-rs clean refs.bib --doi --pages -o cleaned.bib
hx-bib-rs normalize refs.bib --venue-style full --dry-run
hx-bib-rs check refs.bib --strict
hx-bib-rs verify refs.bib --arxiv --retries 2 --json report.json
hx-bib-rs table paper.tex --amp align --stdout
```

Build the CLI with Rust and the platform's build tools installed:

```bash
cargo build --locked --release --bin hx-bib-rs
```

Build the Tauri client from this directory:

```bash
cargo install tauri-cli --version '^2' --locked
cargo tauri build --features desktop
```

Development parity check, using the original Python implementation:

```bash
python -m pip install -e '..[dev]'
python tests/parity.py --binary target/release/hx-bib-rs
```

On Windows, append `.exe` to the binary path. The desktop's left panel contains the settings. Apply runs enabled operations together. The right panel groups the diff and verification notes by entry. arXiv checks run separately.

The workflow checks daily at 04:17 Asia/Shanghai. It publishes after both platforms pass tests and builds, and skips when the current commit already has a published Rust release. You can also run **Rust clients** manually in Actions.
