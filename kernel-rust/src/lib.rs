//! know_your_context_kernel — the Rust extraction kernel (wave W5, SUB-93).
//!
//! Two surfaces from one codebase:
//! * the `kys-kernel` CLI (bin) for the standalone/server path;
//! * the `know_your_context_kernel` Python module (PyO3, feature `pyo3`) for the
//!   agent-harness path, loaded through `know_your_context.ingest.kernel_bridge`.
//!
//! Both produce canonical JSON byte-identical to the Python reference
//! extractor (`know_your_context.ingest.extractor`).

pub mod canon;
pub mod generated;
pub mod registry;
pub mod walker;

pub use walker::{
    extract_file, extract_source, file_node_id, make_node_id, sha256_hex, ExtractionResult,
    KernelError, EXTRACTION_VERSION, MAX_SOURCE_FILE_SIZE_BYTES,
};

#[cfg(feature = "pyo3")]
mod pyo3_bridge {
    use std::collections::BTreeMap;

    use pyo3::create_exception;
    use pyo3::exceptions::PyException;
    use pyo3::prelude::*;
    use pyo3::types::{PyBool, PyDict, PyInt, PyList, PyString, PyTuple};

    use crate::canon::{self, Json};
    use crate::registry;
    use crate::walker::{self};

    create_exception!(know_your_context_kernel, KernelError, PyException);

    fn to_py_err(error: walker::KernelError) -> PyErr {
        // args carry the machine-readable code plus structured context so the
        // Python bridge can rebuild the reference extractor's exception
        // classes (FileTooLargeError & co.) exactly.
        Python::attach(|py| {
            let context = pyo3::types::PyDict::new(py);
            if let Some(path) = &error.path {
                context.set_item("path", path).expect("dict set_item");
            }
            if let Some(size) = error.size {
                context.set_item("size", size).expect("dict set_item");
            }
            if let Some(limit) = error.limit {
                context.set_item("limit", limit).expect("dict set_item");
            }
            KernelError::new_err((error.code, error.message, context.unbind()))
        })
    }

    /// Extract an in-memory source string; returns the canonical JSON artifact.
    ///
    /// B3/K2 (TASK2-PLAN WS-C): the parse+walk is pure Rust CPU work, so the
    /// GIL is released around it — otherwise a thread pool over kernel
    /// extractions is *slower* than serial (measured 0.79x at 2 threads).
    /// The string arguments are owned first because `&str` borrows are tied
    /// to the GIL lifetime.
    #[pyfunction]
    #[pyo3(signature = (path, source, language=None, generated_hint=false))]
    fn extract_source(
        py: Python<'_>,
        path: &str,
        source: &str,
        language: Option<&str>,
        generated_hint: bool,
    ) -> PyResult<String> {
        let path = path.to_owned();
        let source = source.to_owned();
        let language = language.map(str::to_owned);
        py.detach(move || {
            walker::extract_source(&path, &source, language.as_deref(), generated_hint)
                .map(|result| result.canonical_json())
                .map_err(to_py_err)
        })
    }

    /// Extract a file from disk; returns the canonical JSON artifact.
    ///
    /// Releases the GIL like [`extract_source`]: disk I/O plus the walk must
    /// not serialise Python threads (ingest pipeline parallelism, B3/K2).
    #[pyfunction]
    #[pyo3(signature = (path, display_path=None))]
    fn extract_file(py: Python<'_>, path: &str, display_path: Option<&str>) -> PyResult<String> {
        let path = path.to_owned();
        let display_path = display_path.map(str::to_owned);
        py.detach(move || {
            walker::extract_file(&path, display_path.as_deref())
                .map(|result| result.canonical_json())
                .map_err(to_py_err)
        })
    }

    /// `kind:sha256(path:kind:name:line)[:col]` — identical to the Python model.
    #[pyfunction]
    #[pyo3(signature = (path, kind, name, line, col=None))]
    fn make_node_id(path: &str, kind: &str, name: &str, line: u64, col: Option<u64>) -> String {
        walker::make_node_id(path, kind, name, line, col)
    }

    fn py_to_json(value: &Bound<'_, PyAny>) -> PyResult<Json> {
        if value.is_instance_of::<PyBool>() {
            return Ok(Json::Bool(value.extract::<bool>()?));
        }
        if value.is_instance_of::<PyInt>() {
            return Ok(Json::Int(value.extract::<i64>()?));
        }
        if value.is_instance_of::<PyString>() {
            return Ok(Json::Str(value.extract::<String>()?));
        }
        if let Ok(dict) = value.cast::<PyDict>() {
            let mut map = BTreeMap::new();
            for (key, item) in dict.iter() {
                let key: String = key.extract()?;
                map.insert(key, py_to_json(&item)?);
            }
            return Ok(Json::Obj(map));
        }
        if let Ok(list) = value.cast::<PyList>() {
            let mut items = Vec::new();
            for item in list.iter() {
                items.push(py_to_json(&item)?);
            }
            return Ok(Json::Arr(items));
        }
        if let Ok(tuple) = value.cast::<PyTuple>() {
            let mut items = Vec::new();
            for item in tuple.iter() {
                items.push(py_to_json(&item)?);
            }
            return Ok(Json::Arr(items));
        }
        Err(PyException::new_err(format!(
            "canonical_json supports dict/list/tuple/str/int/bool only, got {}",
            value.get_type().name()?
        )))
    }

    /// Canonical JSON of a plain Python structure — same bytes as
    /// `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
    #[pyfunction]
    fn canonical_json(value: &Bound<'_, PyAny>) -> PyResult<String> {
        Ok(canon::canonical_json(&py_to_json(value)?))
    }

    /// Every language registered in the shared registry dump, sorted.
    #[pyfunction]
    fn supported_languages() -> Vec<&'static str> {
        registry::supported_languages()
    }

    /// Kernel build version.
    #[pyfunction]
    fn kernel_version() -> &'static str {
        env!("CARGO_PKG_VERSION")
    }

    /// Generated/minified hint — identical to the Python `detect_generated`.
    #[pyfunction]
    fn detect_generated(path: &str, source: &str) -> bool {
        crate::generated::detect_generated(path, source)
    }

    #[pymodule]
    fn know_your_context_kernel(module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add_function(wrap_pyfunction!(extract_source, module)?)?;
        module.add_function(wrap_pyfunction!(extract_file, module)?)?;
        module.add_function(wrap_pyfunction!(make_node_id, module)?)?;
        module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
        module.add_function(wrap_pyfunction!(supported_languages, module)?)?;
        module.add_function(wrap_pyfunction!(kernel_version, module)?)?;
        module.add_function(wrap_pyfunction!(detect_generated, module)?)?;
        module.add("KernelError", module.py().get_type::<KernelError>())?;
        module.add("EXTRACTION_VERSION", crate::EXTRACTION_VERSION)?;
        Ok(())
    }
}
